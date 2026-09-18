from __future__ import annotations

import http.client
import socket
import time
from typing import Callable, TypeVar
from urllib.error import HTTPError, URLError

try:  # requests is an optional runtime dependency at import time.
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

try:  # curl_cffi is installed by the data extra, but keep import optional.
    from curl_cffi.requests import RequestsError as CurlRequestsError
except ImportError:  # pragma: no cover
    CurlRequestsError = None  # type: ignore[assignment]


T = TypeVar("T")

# libcurl transport-level errors that are safe to retry verbatim. HTTP status
# failures are deliberately excluded (for example CURLE_HTTP_RETURNED_ERROR=22).
_CURL_TRANSIENT_CODES = {
    5,   # COULDNT_RESOLVE_PROXY
    6,   # COULDNT_RESOLVE_HOST
    7,   # COULDNT_CONNECT
    18,  # PARTIAL_FILE
    28,  # OPERATION_TIMEDOUT
    35,  # SSL_CONNECT_ERROR
    52,  # GOT_NOTHING
    55,  # SEND_ERROR
    56,  # RECV_ERROR
    92,  # HTTP2_STREAM
}


def is_transient_network_error(exc: BaseException) -> bool:
    """Return True only for transport failures that are safe to retry verbatim.

    Data/schema/semantic validation errors are deliberately excluded. Retrying
    those can hide a provider contract change and would weaken fail-closed
    behavior.
    """

    if isinstance(exc, HTTPError):
        return False

    transient: tuple[type[BaseException], ...] = (
        URLError,
        TimeoutError,
        ConnectionError,
        ConnectionResetError,
        ConnectionAbortedError,
        BrokenPipeError,
        socket.timeout,
        http.client.RemoteDisconnected,
    )
    if isinstance(exc, transient):
        return True
    if requests is not None and isinstance(
        exc,
        (
            requests.ConnectionError,
            requests.Timeout,
        ),
    ):
        return True
    if CurlRequestsError is not None and isinstance(exc, CurlRequestsError):
        try:
            code = int(getattr(exc, "code", 0) or 0)
        except (TypeError, ValueError):
            code = 0
        return code in _CURL_TRANSIENT_CODES
    return False


def call_with_bounded_network_retry(
    call: Callable[[], T],
    *,
    attempts: int = 3,
    backoff_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Retry the exact same network call on transport errors only."""

    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds must be >= 0")

    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return call()
        except BaseException as exc:
            if not is_transient_network_error(exc) or attempt + 1 >= attempts:
                raise
            last = exc
            delay = backoff_seconds * (attempt + 1)
            if delay:
                sleep(delay)
    assert last is not None  # pragma: no cover
    raise last


__all__ = ["is_transient_network_error", "call_with_bounded_network_retry"]
