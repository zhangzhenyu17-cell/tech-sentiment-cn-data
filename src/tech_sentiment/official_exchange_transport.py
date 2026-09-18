from __future__ import annotations

import json
from typing import Callable, Iterable
from urllib.parse import urlparse

import requests


def _validate_official_https_url(url: object, *, allowed_hosts: Iterable[str]) -> str:
    text = str(url or "").strip()
    parsed = urlparse(text)
    hosts = {str(host).lower() for host in allowed_hosts}
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in hosts:
        raise ValueError(
            f"official transport redirected outside allowlisted HTTPS hosts: {text}"
        )
    return text


def _response_json(
    response: object,
    *,
    default_url: str,
    allowed_hosts: Iterable[str],
) -> object:
    response.raise_for_status()
    _validate_official_https_url(
        getattr(response, "url", default_url) or default_url,
        allowed_hosts=allowed_hosts,
    )
    try:
        return response.json()
    except Exception as json_exc:
        text = str(getattr(response, "text", "") or "").strip()
        if not text:
            raise ValueError("official HTTPS endpoint returned no JSON body") from json_exc
        # Some exchange endpoints optionally wrap JSON in a JSONP callback.  We
        # never request JSONP, but tolerate it when the provider injects a wrapper
        # while preserving the exact same response payload.
        left = text.find("(")
        right = text.rfind(")")
        if left <= 0 or right <= left:
            raise ValueError("official HTTPS endpoint returned non-JSON content") from json_exc
        try:
            return json.loads(text[left + 1 : right])
        except json.JSONDecodeError as exc:
            raise ValueError("official HTTPS endpoint returned invalid JSON/JSONP") from exc


def fetch_official_json(
    *,
    url: str,
    params: dict[str, object],
    referer: str,
    allowed_query_hosts: Iterable[str],
    warmup_url: str | None = None,
    allowed_warmup_hosts: Iterable[str] | None = None,
    timeout: float = 20.0,
    plain_get: Callable[..., object] | None = None,
    browser_get: Callable[..., object] | None = None,
    browser_session_factory: Callable[[], object] | None = None,
) -> object:
    """Fetch one official JSON document with bounded same-source transports.

    The semantic request (URL + params) is never changed between transports.
    Transport order is ordinary HTTPS, browser-fingerprint HTTPS, then an
    optional warmed browser session.  Every redirect is constrained to the
    caller's official HTTPS allowlist.  Schema validation deliberately remains
    outside this helper so a valid-but-drifted provider payload fails closed
    instead of being silently retried through another semantic path.
    """

    if timeout <= 0:
        raise ValueError("official transport timeout must be positive")
    _validate_official_https_url(url, allowed_hosts=allowed_query_hosts)
    _validate_official_https_url(referer, allowed_hosts=set(allowed_query_hosts) | set(allowed_warmup_hosts or ()))
    if warmup_url is not None:
        _validate_official_https_url(
            warmup_url,
            allowed_hosts=allowed_warmup_hosts or allowed_query_hosts,
        )

    headers = {
        "Referer": referer,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        ),
    }
    errors: list[str] = []

    if plain_get is None:
        plain_get = requests.get
    try:
        response = plain_get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
        return _response_json(
            response,
            default_url=url,
            allowed_hosts=allowed_query_hosts,
        )
    except Exception as exc:
        errors.append(f"plain:{type(exc).__name__}:{exc}")

    curl_requests = None
    if browser_get is None or browser_session_factory is None:
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:  # pragma: no cover - installed by data extra
            raise RuntimeError(
                "curl_cffi is required for official browser transport fallback"
            ) from exc

    if browser_get is None:
        browser_get = curl_requests.get
    try:
        response = browser_get(
            url,
            params=params,
            headers=headers,
            impersonate="chrome",
            timeout=timeout,
            allow_redirects=True,
        )
        return _response_json(
            response,
            default_url=url,
            allowed_hosts=allowed_query_hosts,
        )
    except Exception as exc:
        errors.append(f"browser:{type(exc).__name__}:{exc}")

    if warmup_url is not None:
        if browser_session_factory is None:
            browser_session_factory = curl_requests.Session
        session = browser_session_factory()
        try:
            bootstrap_headers = {
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": headers["Accept-Language"],
                "User-Agent": headers["User-Agent"],
            }
            try:
                bootstrap = session.get(
                    warmup_url,
                    headers=bootstrap_headers,
                    impersonate="chrome",
                    timeout=min(timeout, 10.0),
                    allow_redirects=True,
                )
                _validate_official_https_url(
                    getattr(bootstrap, "url", warmup_url) or warmup_url,
                    allowed_hosts=allowed_warmup_hosts or allowed_query_hosts,
                )
                # Do not require a 2xx warm-up: some WAFs set a useful cookie on
                # their challenge response.  The actual JSON request below must
                # still return a valid successful official response.
            except Exception as exc:
                errors.append(f"warmup:{type(exc).__name__}:{exc}")

            try:
                response = session.get(
                    url,
                    params=params,
                    headers=headers,
                    impersonate="chrome",
                    timeout=timeout,
                    allow_redirects=True,
                )
                return _response_json(
                    response,
                    default_url=url,
                    allowed_hosts=allowed_query_hosts,
                )
            except Exception as exc:
                errors.append(f"session:{type(exc).__name__}:{exc}")
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

    raise RuntimeError(
        "official HTTPS transports exhausted: " + " | ".join(errors)
    )


__all__ = ["fetch_official_json"]
