"""Impersonating HTTP client for portals behind Cloudflare's JS challenge.

The plain `requests`-based client in `applib.http` is blocked (HTTP 403,
"Just a moment…") by Cloudflare for some portals — notably newhome.ch. A request
made with `curl_cffi` using a real Chrome TLS/JA3 fingerprint clears that
challenge for GET requests, so we can read newhome's (otherwise JSON-clean)
ServiceStack API without a headless browser.

A heavier Playwright/Chromium fallback was evaluated during design but proved
unnecessary: every newhome endpoint we need is reachable via curl_cffi GET. This
module keeps a single small interface (`get_json` / `get_html`) so the underlying
mechanism could be swapped later without touching scout/verify code.

Politeness: the same per-host delay budget as `applib.http` (we reuse its state
so both clients share one throttle per host). On a challenge we can't clear, the
caller logs "blocked" and continues — we NEVER fabricate listings (Hard Rule 5).
"""
from __future__ import annotations

import time
from typing import Any

from . import config, http

try:  # curl_cffi is an optional dep; degrade gracefully if it's missing.
    from curl_cffi import requests as _cr  # type: ignore
    _AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    _cr = None
    _AVAILABLE = False

IMPERSONATE = "chrome"


class BlockedError(RuntimeError):
    """Raised when the anti-bot challenge could not be cleared (or curl_cffi is
    unavailable). Callers should log and skip — never fabricate."""


class NotFoundError(BlockedError):
    """The resource is gone (HTTP 404) — e.g. a listing newhome has removed.
    Distinct from a transient block so callers can mark the listing closed."""


def available() -> bool:
    return _AVAILABLE


def _looks_challenged(status: int, text: str) -> bool:
    if status in (403, 429, 503):
        return True
    head = (text or "")[:1500].lower()
    return "just a moment" in head or "cf-challenge" in head


def _get(url: str, *, params: dict | None, accept: str, referer: str):
    if not _AVAILABLE:
        raise BlockedError("curl_cffi not installed")
    pol = config.sites().get("politeness", {})
    timeout = float(pol.get("timeout_seconds", 25))
    retries = int(pol.get("max_retries", 2))
    base_delay = float(pol.get("request_delay_seconds", 2.0))
    headers = {
        "Accept": accept,
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
        "Referer": referer,
    }
    # Cloudflare challenges a fraction of requests mid-burst; a fresh attempt
    # after a pause usually clears (2026-06-11: 38/1015 verify checks 403'd
    # once each). A 404/410 is definitive — never retried.
    for attempt in range(retries + 1):
        http._respect_delay(url)  # share the per-host throttle with applib.http
        try:
            resp = _cr.get(url, params=params, headers=headers,
                           impersonate=IMPERSONATE, timeout=timeout)
        except Exception as exc:
            raise BlockedError(f"request failed: {exc}") from exc
        if resp.status_code in (404, 410):
            raise NotFoundError(f"HTTP {resp.status_code}")
        if _looks_challenged(resp.status_code, resp.text):
            if attempt < retries:
                time.sleep(max(5.0, base_delay) * (attempt + 1))
                continue
            raise BlockedError(f"anti-bot challenge (HTTP {resp.status_code})")
        if resp.status_code != 200:
            raise BlockedError(f"HTTP {resp.status_code}")
        return resp


def get_json(url: str, *, params: dict | None = None,
             referer: str = "https://www.newhome.ch/") -> Any:
    """Polite impersonating GET returning parsed JSON. Raises BlockedError on a
    challenge / non-200 / parse failure."""
    resp = _get(url, params=params, accept="application/json", referer=referer)
    try:
        return resp.json()
    except Exception as exc:
        raise BlockedError(f"non-JSON response: {exc}") from exc


def get_html(url: str, *, params: dict | None = None,
             referer: str = "https://www.newhome.ch/") -> str:
    """Polite impersonating GET returning page text."""
    resp = _get(url, params=params, accept="text/html", referer=referer)
    return resp.text or ""
