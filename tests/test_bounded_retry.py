from urllib.error import HTTPError, URLError

import pytest

from tech_sentiment.bounded_retry import (
    call_with_bounded_network_retry,
    is_transient_network_error,
)


def test_transport_failure_retries_then_succeeds_without_changing_call():
    calls: list[int] = []

    def call():
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            raise URLError(ConnectionResetError(104, "reset"))
        return "ok"

    assert call_with_bounded_network_retry(
        call,
        attempts=3,
        backoff_seconds=0,
    ) == "ok"
    assert calls == [1, 2, 3]


def test_http_status_and_schema_failures_are_not_transport_retries():
    http = HTTPError(
        "https://example.invalid",
        403,
        "Forbidden",
        hdrs=None,
        fp=None,
    )
    assert is_transient_network_error(http) is False
    assert is_transient_network_error(ValueError("schema drift")) is False

    calls = 0

    def call():
        nonlocal calls
        calls += 1
        raise ValueError("schema drift")

    with pytest.raises(ValueError, match="schema drift"):
        call_with_bounded_network_retry(call, attempts=3, backoff_seconds=0)
    assert calls == 1
