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
