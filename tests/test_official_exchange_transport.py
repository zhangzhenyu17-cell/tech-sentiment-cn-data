import pytest

from tech_sentiment.official_exchange_transport import fetch_official_json


class FakeResponse:
    def __init__(self, url, *, payload=None, status=200, text=""):
        self.url = url
        self._payload = payload
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_official_json_falls_from_plain_to_browser_without_changing_query():
    calls = []

    def plain_get(url, **kwargs):
        calls.append(("plain", url, kwargs["params"]))
        return FakeResponse(url, status=403)

    def browser_get(url, **kwargs):
        calls.append(("browser", url, kwargs["params"]))
        return FakeResponse(url, payload={"result": [{"ok": True}]})

    payload = fetch_official_json(
        url="https://query.sse.com.cn/commonQuery.do",
        params={"sqlId": "X", "STAT_DATE": "2026-09-17"},
        referer="https://www.sse.com.cn/example/",
        allowed_query_hosts=("query.sse.com.cn",),
        allowed_warmup_hosts=("www.sse.com.cn",),
        plain_get=plain_get,
        browser_get=browser_get,
        browser_session_factory=lambda: None,
    )

    assert payload == {"result": [{"ok": True}]}
    assert calls[0][2] == calls[1][2]


def test_official_json_warmed_session_stays_on_official_hosts():
    calls = []

    def plain_get(url, **kwargs):
        return FakeResponse(url, status=403)

    def browser_get(url, **kwargs):
        return FakeResponse(url, status=403)

    class Session:
        def get(self, url, **kwargs):
            calls.append(url)
            if url == "https://www.sse.com.cn/example/":
                return FakeResponse(url, status=403)
            return FakeResponse(url, payload={"result": [{"ok": True}]})

        def close(self):
            calls.append("closed")

    payload = fetch_official_json(
        url="https://query.sse.com.cn/commonQuery.do",
        params={"sqlId": "X"},
        referer="https://www.sse.com.cn/example/",
        allowed_query_hosts=("query.sse.com.cn",),
        warmup_url="https://www.sse.com.cn/example/",
        allowed_warmup_hosts=("www.sse.com.cn",),
        plain_get=plain_get,
        browser_get=browser_get,
        browser_session_factory=Session,
    )

    assert payload == {"result": [{"ok": True}]}
    assert calls == [
        "https://www.sse.com.cn/example/",
        "https://query.sse.com.cn/commonQuery.do",
        "closed",
    ]


def test_official_json_rejects_offsite_redirect_and_reports_transport_stages():
    def plain_get(url, **kwargs):
        return FakeResponse("https://example.com/challenge", payload={"x": 1})

    def browser_get(url, **kwargs):
        return FakeResponse("https://example.com/challenge", payload={"x": 1})

    with pytest.raises(RuntimeError, match="official HTTPS transports exhausted") as exc:
        fetch_official_json(
            url="https://query.sse.com.cn/commonQuery.do",
            params={"sqlId": "X"},
            referer="https://www.sse.com.cn/example/",
            allowed_query_hosts=("query.sse.com.cn",),
            allowed_warmup_hosts=("www.sse.com.cn",),
            plain_get=plain_get,
            browser_get=browser_get,
            browser_session_factory=lambda: None,
        )

    message = str(exc.value)
    assert "plain:ValueError" in message
    assert "browser:ValueError" in message
