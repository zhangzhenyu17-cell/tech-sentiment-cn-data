from __future__ import annotations

import http.client

import pytest
from requests.exceptions import ChunkedEncodingError

import tech_sentiment.cninfo_resilient_download as module


def test_transient_urllib_exhaustion_uses_same_provider_session(monkeypatch):
    original = "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"
    expected_fallback = (
        "https://www.cninfo.com.cn/new/announcement/download"
        "?bulletinId=1225568832&announceTime=2026-09-16"
    )
    calls: list[tuple[str, str]] = []

    def fail_primary(url: str, *, timeout: float):
        assert url == original
        assert timeout == 30.0
        raise http.client.IncompleteRead(b"partial", 1024)

    def recover_session(canonical_url: str, fallback_url: str, *, timeout: float):
        calls.append((canonical_url, fallback_url))
        assert timeout == 30.0
        return b"%PDF-1.7 recovered exact bulletin bytes", fallback_url

    monkeypatch.setattr(module, "download_official_document", fail_primary)
    monkeypatch.setattr(module, "_download_cninfo_with_https_session", recover_session)

    downloaded = module.download_cninfo_document_resilient(original)

    assert calls == [(original, expected_fallback)]
    assert downloaded.url == original
    assert downloaded.retrieval_url == expected_fallback
    assert downloaded.content == b"%PDF-1.7 recovered exact bulletin bytes"
    assert downloaded.transport_method == "cninfo_same_provider_session"
    assert len(downloaded.sha256) == 64


def test_chunked_session_exhaustion_uses_same_provider_browser(monkeypatch):
    original = "https://static.cninfo.com.cn/finalpage/2022-04-26/1216303368.PDF"
    expected_fallback = (
        "https://www.cninfo.com.cn/new/announcement/download"
        "?bulletinId=1216303368&announceTime=2022-04-26"
    )
    browser_calls: list[tuple[str, str]] = []

    def fail_primary(url: str, *, timeout: float):
        raise http.client.IncompleteRead(b"partial", 1024)

    def fail_session(canonical_url: str, fallback_url: str, *, timeout: float):
        raise ChunkedEncodingError(
            "Connection broken: IncompleteRead(1536000 bytes read, 1832288 more expected)"
        )

    def recover_browser(canonical_url: str, fallback_url: str, *, timeout: float):
        browser_calls.append((canonical_url, fallback_url))
        assert timeout == 30.0
        return b"%PDF-1.7 browser recovered exact bulletin bytes", fallback_url

    monkeypatch.setattr(module, "download_official_document", fail_primary)
    monkeypatch.setattr(module, "_download_cninfo_with_https_session", fail_session)
    monkeypatch.setattr(module, "_download_cninfo_with_browser_transport", recover_browser)

    downloaded = module.download_cninfo_document_resilient(original)

    assert browser_calls == [(original, expected_fallback)]
    assert downloaded.url == original
    assert downloaded.retrieval_url == expected_fallback
    assert downloaded.content == b"%PDF-1.7 browser recovered exact bulletin bytes"
    assert downloaded.transport_method == "cninfo_same_provider_browser"


def test_browser_transport_exhaustion_uses_strict_range_resume(monkeypatch):
    original = "https://static.cninfo.com.cn/finalpage/2022-04-26/1216303368.PDF"
    expected_fallback = (
        "https://www.cninfo.com.cn/new/announcement/download"
        "?bulletinId=1216303368&announceTime=2022-04-26"
    )
    range_calls: list[tuple[str, str]] = []

    def fail_primary(url: str, *, timeout: float):
        raise http.client.IncompleteRead(b"partial", 1024)

    def fail_session(canonical_url: str, fallback_url: str, *, timeout: float):
        raise ChunkedEncodingError("session entity stream truncated")

    def fail_browser(canonical_url: str, fallback_url: str, *, timeout: float):
        raise RuntimeError(
            "CNINFO browser HTTPS transport exhausted without official PDF bytes"
        )

    def recover_range(canonical_url: str, fallback_url: str, *, timeout: float):
        range_calls.append((canonical_url, fallback_url))
        assert timeout == 30.0
        return b"%PDF-1.7 range recovered exact bulletin bytes", original

    monkeypatch.setattr(module, "download_official_document", fail_primary)
    monkeypatch.setattr(module, "_download_cninfo_with_https_session", fail_session)
    monkeypatch.setattr(module, "_download_cninfo_with_browser_transport", fail_browser)
    monkeypatch.setattr(module, "_download_cninfo_with_range_resume", recover_range)

    downloaded = module.download_cninfo_document_resilient(original)

    assert range_calls == [(original, expected_fallback)]
    assert downloaded.url == original
    assert downloaded.retrieval_url == original
    assert downloaded.content == b"%PDF-1.7 range recovered exact bulletin bytes"
    assert downloaded.transport_method == "cninfo_same_provider_range_resume"


class _FakeStreamingResponse:
    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        headers: dict[str, str],
        chunks: list[bytes],
        stream_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.headers = headers
        self._chunks = chunks
        self._stream_error = stream_error
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, *, chunk_size: int):
        assert chunk_size == module._RANGE_CHUNK_SIZE
        yield from self._chunks
        if self._stream_error is not None:
            raise self._stream_error

    def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, responses: list[_FakeStreamingResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.closed = False

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected extra range request")
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def test_range_resume_continues_from_exact_accumulated_offset(monkeypatch):
    original = "https://static.cninfo.com.cn/finalpage/2022-04-26/1216303368.PDF"
    payload = b"%PDF-1.7\nexact immutable bulletin payload\n%%EOF"
    first = payload[:19]
    rest = payload[len(first) :]
    session = _FakeSession(
        [
            _FakeStreamingResponse(
                status_code=200,
                url=original,
                headers={"Content-Length": str(len(payload)), "ETag": '"fixed-etag"'},
                chunks=[first],
                stream_error=ChunkedEncodingError("entity stream truncated"),
            ),
            _FakeStreamingResponse(
                status_code=206,
                url=original,
                headers={
                    "Content-Range": f"bytes {len(first)}-{len(payload) - 1}/{len(payload)}",
                    "Content-Length": str(len(rest)),
                    "ETag": '"fixed-etag"',
                },
                chunks=[rest],
            ),
        ]
    )
    monkeypatch.setattr(module.requests, "Session", lambda: session)

    content, retrieval_url = module._download_cninfo_candidate_with_range_resume(
        original,
        original,
        timeout=30.0,
    )

    assert content == payload
    assert retrieval_url == original
    assert len(session.calls) == 2
    assert "Range" not in session.calls[0]["headers"]
    assert session.calls[1]["headers"]["Range"] == f"bytes={len(first)}-"
    assert session.calls[1]["headers"]["If-Range"] == '"fixed-etag"'
    assert session.closed is True
    assert all(response.closed for response in session.responses) is True


def test_range_resume_rejects_mismatched_content_range(monkeypatch):
    original = "https://static.cninfo.com.cn/finalpage/2022-04-26/1216303368.PDF"
    payload = b"%PDF-1.7\nexact immutable bulletin payload\n%%EOF"
    first = payload[:19]
    session = _FakeSession(
        [
            _FakeStreamingResponse(
                status_code=200,
                url=original,
                headers={"Content-Length": str(len(payload))},
                chunks=[first],
                stream_error=ChunkedEncodingError("entity stream truncated"),
            ),
            _FakeStreamingResponse(
                status_code=206,
                url=original,
                headers={
                    "Content-Range": f"bytes {len(first) + 1}-{len(payload) - 1}/{len(payload)}",
                },
                chunks=[payload[len(first) + 1 :]],
            ),
        ]
    )
    monkeypatch.setattr(module.requests, "Session", lambda: session)

    with pytest.raises(ValueError, match="does not match requested offset"):
        module._download_cninfo_candidate_with_range_resume(
            original,
            original,
            timeout=30.0,
        )


def test_range_resume_rejects_server_ignoring_range(monkeypatch):
    original = "https://static.cninfo.com.cn/finalpage/2022-04-26/1216303368.PDF"
    payload = b"%PDF-1.7\nexact immutable bulletin payload\n%%EOF"
    first = payload[:19]
    session = _FakeSession(
        [
            _FakeStreamingResponse(
                status_code=200,
                url=original,
                headers={"Content-Length": str(len(payload))},
                chunks=[first],
                stream_error=ChunkedEncodingError("entity stream truncated"),
            ),
            _FakeStreamingResponse(
                status_code=200,
                url=original,
                headers={"Content-Length": str(len(payload))},
                chunks=[payload],
            ),
        ]
    )
    monkeypatch.setattr(module.requests, "Session", lambda: session)

    with pytest.raises(ValueError, match="ignored Range"):
        module._download_cninfo_candidate_with_range_resume(
            original,
            original,
            timeout=30.0,
        )


def test_unrelated_browser_runtime_failure_does_not_enable_range_resume(monkeypatch):
    original = "https://static.cninfo.com.cn/finalpage/2022-04-26/1216303368.PDF"

    monkeypatch.setattr(
        module,
        "download_official_document",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            http.client.IncompleteRead(b"partial", 1024)
        ),
    )
    monkeypatch.setattr(
        module,
        "_download_cninfo_with_https_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ChunkedEncodingError("session truncated")
        ),
    )
    monkeypatch.setattr(
        module,
        "_download_cninfo_with_browser_transport",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("unrelated browser semantic failure")
        ),
    )
    monkeypatch.setattr(
        module,
        "_download_cninfo_with_range_resume",
        lambda *args, **kwargs: pytest.fail("range fallback must not run"),
    )

    with pytest.raises(RuntimeError, match="unrelated browser semantic failure"):
        module.download_cninfo_document_resilient(original)


def test_semantic_session_failure_does_not_switch_to_browser(monkeypatch):
    original = "https://static.cninfo.com.cn/finalpage/2022-04-26/1216303368.PDF"

    def fail_primary(url: str, *, timeout: float):
        raise http.client.IncompleteRead(b"partial", 1024)

    def fail_session(canonical_url: str, fallback_url: str, *, timeout: float):
        raise ValueError("session semantic validation failure")

    monkeypatch.setattr(module, "download_official_document", fail_primary)
    monkeypatch.setattr(module, "_download_cninfo_with_https_session", fail_session)
    monkeypatch.setattr(
        module,
        "_download_cninfo_with_browser_transport",
        lambda *args, **kwargs: pytest.fail("browser fallback must not run"),
    )

    with pytest.raises(ValueError, match="session semantic validation failure"):
        module.download_cninfo_document_resilient(original)


def test_non_transient_failure_does_not_switch_transport(monkeypatch):
    original = "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"

    def fail_primary(url: str, *, timeout: float):
        raise ValueError("semantic validation failure")

    monkeypatch.setattr(module, "download_official_document", fail_primary)
    monkeypatch.setattr(
        module,
        "_download_cninfo_with_https_session",
        lambda *args, **kwargs: pytest.fail("same-provider fallback must not run"),
    )

    with pytest.raises(ValueError, match="semantic validation failure"):
        module.download_cninfo_document_resilient(original)
