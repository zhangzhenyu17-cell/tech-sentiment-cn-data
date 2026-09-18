import pytest

from tech_sentiment.official_exchange_transport import fetch_official_json


class FakeResponse:
    def __init__(self, url, *, payload=None, status=200):
        self.url = url
        self._payload = payload
        self.status_code = status
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_transport_falls_from_plain_to_browser_without_changing_query():
    calls = []

    def plain_get(url, **kwargs):
        calls.append(("plain", kwargs["params"]))
        return FakeResponse(url, status=403)

    def browser_get(url, **kwargs):
        calls.append(("browser", kwargs["params"]))
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
    assert calls[0][1] == calls[1][1]


def test_transport_rejects_offsite_redirects():
    def bad_get(url, **kwargs):
        return FakeResponse("https://example.com/challenge", payload={"x": 1})

    with pytest.raises(RuntimeError, match="official HTTPS transports exhausted"):
        fetch_official_json(
            url="https://query.sse.com.cn/commonQuery.do",
            params={"sqlId": "X"},
            referer="https://www.sse.com.cn/example/",
            allowed_query_hosts=("query.sse.com.cn",),
            allowed_warmup_hosts=("www.sse.com.cn",),
            plain_get=bad_get,
            browser_get=bad_get,
            browser_session_factory=lambda: None,
        )
