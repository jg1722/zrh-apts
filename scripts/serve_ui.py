#!/usr/bin/env python3
"""Local web app for the apartment pipeline. Reuses applib for all logic.
NEVER sends mail / submits forms — reach-out only copies text + opens a URL.

    .venv/bin/python scripts/serve_ui.py            # serve on 127.0.0.1:8765
"""
from __future__ import annotations
import importlib
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import clusters, config, learning, paths  # noqa: E402
from applib.scoring import score_listing  # noqa: E402
from applib.store import Store  # noqa: E402
import outreach  # noqa: E402  (scripts/outreach.py)


def _scored(lst: dict, crit: dict) -> dict:
    score, parts = score_listing(lst, crit)
    out = dict(lst)
    out["score"], out["score_parts"] = score, parts
    return out


def api_listings(store: Store, params: dict) -> dict:
    crit = config.effective_criteria()
    include_rejected = params.get("include_rejected") in ("1", "true", "yes")
    rows = []
    for lst in store.listings.values():
        if lst.get("dupe_of"):
            continue
        if not include_rejected and (lst.get("gate_status") == "rejected"
                                     or lst.get("status") == "closed"):
            continue
        if params.get("bucket") and lst.get("bucket") != params["bucket"]:
            continue
        if params.get("hood") and lst.get("hood_category") != params["hood"]:
            continue
        if params.get("source") and lst.get("source") != params["source"]:
            continue
        if params.get("status") and lst.get("status") != params["status"]:
            continue
        q = (params.get("q") or "").strip().lower()
        if q and q not in json.dumps(lst, ensure_ascii=False).lower():
            continue
        rows.append(_scored(lst, crit))
    try:
        rmin = float(params.get("rent_min")) if params.get("rent_min") else None
        rmax = float(params.get("rent_max")) if params.get("rent_max") else None
        smin = float(params.get("score_min")) if params.get("score_min") else None
    except ValueError:
        rmin = rmax = smin = None
    def rent(l):
        return l.get("rent_net") or l.get("rent_gross") or 0
    if rmin is not None:
        rows = [l for l in rows if rent(l) >= rmin]
    if rmax is not None:
        rows = [l for l in rows if rent(l) <= rmax]
    if smin is not None:
        rows = [l for l in rows if l["score"] >= smin]
    clusters.annotate(rows, (crit.get("dedup") or {}).get("cluster_min_size",
                                                          clusters.DEFAULT_MIN_SIZE))
    # Mass-duplicate clusters demote to the bottom (kept visible), then score.
    rows.sort(key=lambda l: (1 if l.get("cluster_size") else 0,
                             -(l["score"]), l.get("transit_min") or 999, rent(l)))
    return {"listings": rows, "count": len(rows)}


def api_message(store: Store, listing_id: str):
    lst = store.listings.get(listing_id)
    if not lst:
        return None
    # The UI server runs for weeks; without this, edits to outreach.py /
    # templates / criteria.yaml never reach the drafts until a restart
    # (bit us on 2026-07-06: server up since Jun 25 served old drafts).
    config.criteria.cache_clear()
    importlib.reload(outreach)
    packet = outreach.render(lst)  # {id, language, subject, body, ...}
    return {
        "subject": packet["subject"],
        "body": packet["body"],
        "channel": lst.get("outreach_channel"),
        "email": lst.get("outreach_email"),
        "url": lst.get("url"),
    }


def _retune(store: Store) -> None:
    learning.retune(store.listings)


def api_reach_out(store: Store, listing_id: str) -> dict:
    lst = store.listings.get(listing_id)
    if not lst:
        return {"ok": False, "error": "unknown id"}
    lst["decision"] = "outreach"
    lst["decision_at"] = paths.now_iso()
    prev = lst.get("status")
    lst["status"] = "contacted"
    lst.setdefault("status_log", []).append(
        {"at": paths.now_iso(), "from": prev, "to": "contacted", "note": "reached out via UI"})
    store.save()
    _retune(store)
    return {"ok": True}


def api_decline(store: Store, listing_id: str, reasons: list, note: str) -> dict:
    lst = store.listings.get(listing_id)
    if not lst:
        return {"ok": False, "error": "unknown id"}
    lst["decision"] = "deprioritized"
    lst["decision_at"] = paths.now_iso()
    lst["decline_reasons"] = list(reasons or [])
    label = ", ".join(reasons or [])
    lst["decision_note"] = (f"{label}: {note}".strip(": ") if note else label) or None
    store.save()
    _retune(store)
    return {"ok": True}


def api_reset(store: Store, listing_id: str) -> dict:
    lst = store.listings.get(listing_id)
    if not lst:
        return {"ok": False, "error": "unknown id"}
    lst["decision"] = None
    lst["decision_at"] = None
    lst["decision_note"] = None
    lst["decline_reasons"] = None
    prev = lst.get("status")
    if prev == "contacted":
        lst["status"] = "new"
        lst.setdefault("status_log", []).append(
            {"at": paths.now_iso(), "from": prev, "to": "new", "note": "undo via UI"})
    store.save()
    _retune(store)
    return {"ok": True}


def api_reply_confirm(store: Store, listing_id: str) -> dict:
    lst = store.listings.get(listing_id)
    if not lst:
        return {"ok": False, "error": "unknown id"}
    cand = lst.get("reply_candidate")
    if not cand:
        return {"ok": False, "error": "no candidate"}
    confirmed = dict(cand)
    confirmed["confirmed_at"] = paths.now_iso()
    lst["reply"] = confirmed
    lst["reply_candidate"] = None
    prev = lst.get("status")
    lst["status"] = "replied"
    lst.setdefault("status_log", []).append(
        {"at": paths.now_iso(), "from": prev, "to": "replied", "note": "reply confirmed via UI"})
    store.save()
    return {"ok": True}


def api_reply_reject(store: Store, listing_id: str) -> dict:
    lst = store.listings.get(listing_id)
    if not lst:
        return {"ok": False, "error": "unknown id"}
    cand = lst.get("reply_candidate")
    if not cand:
        return {"ok": False, "error": "no candidate"}
    tid = cand.get("thread_id")
    if tid:
        dismissed = lst.setdefault("reply_dismissed_threads", [])
        if tid not in dismissed:
            dismissed.append(tid)
    lst["reply_candidate"] = None
    store.save()
    return {"ok": True}


# ---- check-now (background reply pipeline) --------------------------------
_check_lock = threading.Lock()
_check_state = {"running": False, "started_at": None, "finished_at": None,
                "summary": None, "error": None}


def _check_worker():
    try:
        import check_replies
        _check_state["summary"] = check_replies.run()
    except Exception as e:  # noqa: BLE001
        _check_state["error"] = str(e)
    finally:
        _check_state["running"] = False
        _check_state["finished_at"] = paths.now_iso()
        _check_lock.release()


def api_check_start() -> dict:
    if not _check_lock.acquire(blocking=False):
        return {"ok": False, "error": "already running"}
    _check_state.update(running=True, started_at=paths.now_iso(),
                        finished_at=None, summary=None, error=None)
    threading.Thread(target=_check_worker, daemon=True).start()
    return {"ok": True, "started": True}


def api_check_status() -> dict:
    return dict(_check_state)


def api_draft_style() -> dict:
    from applib import draft_style
    return draft_style.status()


def api_draft_style_control(action: str) -> dict:
    from applib import draft_style
    if action == "pause":
        draft_style.set_paused(True)
    elif action == "resume":
        draft_style.set_paused(False)
    elif action == "reset":
        draft_style.reset()
    else:
        return {"ok": False, "error": "unknown action"}
    return {"ok": True}


def api_learning(store: Store) -> dict:
    return learning.status(store.listings)


def api_learning_control(action: str) -> dict:
    if action == "pause":
        learning.set_paused(True)
    elif action == "resume":
        learning.set_paused(False)
    elif action == "reset":
        learning.reset()
    else:
        return {"ok": False, "error": "unknown action"}
    return {"ok": True}


def resolve_photo(store: Store, listing_id: str, n: int):
    """Return (kind, payload): ('file', Path) | ('redirect', url) | ('none', None)."""
    local_dir = paths.PHOTOS_DIR / listing_id
    # defense-in-depth: never let a crafted id escape the photos dir
    inside = local_dir.resolve().is_relative_to(paths.PHOTOS_DIR.resolve())
    if inside and local_dir.is_dir():
        files = sorted(local_dir.glob("*.jpg"))
        if 0 <= n < len(files):
            return "file", files[n]
    lst = store.listings.get(listing_id) or {}
    urls = lst.get("photos") or []
    if 0 <= n < len(urls):
        return "redirect", urls[n]
    return "none", None


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
import http.server
import urllib.parse
import urllib.request
import webbrowser

HOST, PORT = "127.0.0.1", 8765


def _fetch_remote(url: str, cache_path: "Path | None") -> "bytes | None":
    """Fetch a remote listing image server-side (the CDNs serve 200 to us) and
    cache it to disk so the next view is local. Returns bytes, or None on failure
    (caller then 404s and the card shows its gradient placeholder)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
    except Exception:
        return None
    if cache_path is not None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(data)
        except OSError:
            pass
    return data


def _read_json_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if not length:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str, no_cache: bool = False):
        if not path.is_file():
            return self._send_json({"error": "not found"}, 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if no_cache:
            # The frontend assets change between sessions; never let the browser
            # serve a stale app.js (tabs/filters would silently break).
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            u = urllib.parse.urlparse(self.path)
            params = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            if u.path == "/" or u.path == "/index.html":
                return self._send_file(paths.WEB_DIR / "index.html", "text/html; charset=utf-8", no_cache=True)
            if u.path == "/app.js":
                return self._send_file(paths.WEB_DIR / "app.js", "application/javascript", no_cache=True)
            if u.path == "/style.css":
                return self._send_file(paths.WEB_DIR / "style.css", "text/css", no_cache=True)
            if u.path == "/api/listings":
                return self._send_json(api_listings(Store.load(), params))
            if u.path.startswith("/api/message/"):
                out = api_message(Store.load(), u.path.split("/")[-1])
                return self._send_json(out or {}, 200 if out else 404)
            if u.path == "/api/learning":
                return self._send_json(api_learning(Store.load()))
            if u.path == "/api/check-replies/status":
                return self._send_json(api_check_status())
            if u.path == "/api/draft-style":
                return self._send_json(api_draft_style())
            if u.path.startswith("/api/photo/"):
                parts = u.path.split("/")
                if len(parts) != 5 or ".." in parts[3] or not parts[3]:
                    return self._send_json({"error": "bad photo path"}, 404)
                lid, n = parts[3], parts[4]
                try:
                    n_i = int(n)
                except ValueError:
                    return self._send_json({"error": "bad index"}, 404)
                kind, payload = resolve_photo(Store.load(), lid, n_i)
                if kind == "file":
                    return self._send_file(payload, "image/jpeg")
                if kind == "redirect":
                    # proxy + cache instead of redirecting: same-origin, reliable,
                    # and the next load is served from disk
                    cache = paths.PHOTOS_DIR / lid / f"{n_i + 1:02d}.jpg"
                    data = _fetch_remote(payload, cache)
                    if data is None:
                        return self._send_json({"error": "fetch failed"}, 404)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                return self._send_json({"error": "no photo"}, 404)
            self._send_json({"error": "not found"}, 404)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def do_POST(self):
        try:
            u = urllib.parse.urlparse(self.path)
            body = _read_json_body(self)
            if u.path.startswith("/api/reach-out/"):
                return self._send_json(api_reach_out(Store.load(), u.path.split("/")[-1]))
            if u.path.startswith("/api/decline/"):
                return self._send_json(api_decline(Store.load(), u.path.split("/")[-1],
                                                   body.get("reasons", []), body.get("note", "")))
            if u.path.startswith("/api/reset/"):
                return self._send_json(api_reset(Store.load(), u.path.split("/")[-1]))
            if u.path.startswith("/api/reply/confirm/"):
                return self._send_json(api_reply_confirm(Store.load(), u.path.split("/")[-1]))
            if u.path.startswith("/api/reply/reject/"):
                return self._send_json(api_reply_reject(Store.load(), u.path.split("/")[-1]))
            if u.path.startswith("/api/learning/"):
                return self._send_json(api_learning_control(u.path.split("/")[-1]))
            if u.path == "/api/check-replies":
                return self._send_json(api_check_start())
            if u.path.startswith("/api/draft-style/"):
                return self._send_json(api_draft_style_control(u.path.split("/")[-1]))
            self._send_json({"error": "not found"}, 404)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)


def main() -> int:
    # ThreadingHTTPServer: many thumbnails load concurrently — a single-threaded
    # server serialises them and they stall. (Also sets allow_reuse_address.)
    httpd = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    url = f"http://{HOST}:{PORT}"
    print(f"ZRH Apartments UI → {url}  (Ctrl-C to stop)")
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
