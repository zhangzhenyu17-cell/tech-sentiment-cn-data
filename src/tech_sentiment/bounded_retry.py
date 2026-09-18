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


T = TypeVar("T")


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
