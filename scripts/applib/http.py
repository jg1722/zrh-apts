"""A deliberately gentle HTTP client: shared User-Agent, per-host delay between
requests, bounded retries. No parallelism — we scrape one request at a time."""
from __future__ import annotations

import time
from urllib.parse import urlsplit

import requests

from . import config

_last_hit: dict[str, float] = {}


def _politeness() -> dict:
    return config.sites().get("politeness", {})


def _respect_delay(url: str) -> None:
    host = urlsplit(url).netloc
    delay = float(_politeness().get("request_delay_seconds", 2.0))
    last = _last_hit.get(host)
    if last is not None:
        wait = delay - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_hit[host] = time.monotonic()


def get(url: str, *, params: dict | None = None, accept: str | None = None,
        referer: str | None = None) -> requests.Response:
    """Polite GET. Raises requests.RequestException after exhausting retries."""
    pol = _politeness()
    headers = {"User-Agent": pol.get("user_agent", "Mozilla/5.0")}
    if accept:
        headers["Accept"] = accept
    if referer:
        headers["Referer"] = referer
    timeout = float(pol.get("timeout_seconds", 25))
    retries = int(pol.get("max_retries", 2))

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        _respect_delay(url)
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            return resp
        except requests.RequestException as exc:  # network-level failure
            last_exc = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def get_json(url: str, *, params: dict | None = None) -> dict:
    resp = get(url, params=params, accept="application/json")
    resp.raise_for_status()
    return resp.json()
