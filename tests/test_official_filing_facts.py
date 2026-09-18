import json
from urllib.error import HTTPError

import pandas as pd
import pytest

import tech_sentiment.official_filing_facts as filing_module
from tech_sentiment.official_filing_facts import (
    download_official_document,
    build_filing_fact_rows,
    derive_fundamental_trend_evidence,
    extract_standard_filing_facts,
    filing_period_end_from_title,
)


def test_filing_period_end_is_derived_from_the_versioned_report_title():
    assert filing_period_end_from_title("某公司2025年年度报告") == pd.Timestamp("2025-12-31")
    assert filing_period_end_from_title("某公司2026年第一季度报告") == pd.Timestamp("2026-03-31")
    assert filing_period_end_from_title("某公司2026年半年度报告（修订版）") == pd.Timestamp("2026-06-30")
    assert filing_period_end_from_title("某公司2026年第三季度报告") == pd.Timestamp("2026-09-30")


def test_standard_filing_facts_require_proven_yuan_units_and_do_not_fill():
    text = """
    主要会计数据和财务指标  单位：元 币种：人民币
    营业收入 1,200.00 1,000.00
    归属于上市公司股东的净利润 120.00 100.00
    经营活动产生的现金流量净额 90.00 80.00
    总资产 5,000.00 4,800.00
    归属于上市公司股东的所有者权益 3,000.00 2,900.00
    基本每股收益 1.20 1.00
    """
    facts = extract_standard_filing_facts(text)
    assert facts["OPERATING_REVENUE"] == 1200.0
    assert facts["NET_PROFIT_PARENT"] == 120.0
    assert facts["NET_PROFIT_MARGIN"] == pytest.approx(0.1)
    assert facts["BASIC_EPS"] == 1.2
    assert "NONEXISTENT" not in facts

    with pytest.raises(ValueError, match="non-yuan unit"):
        extract_standard_filing_facts("单位：万元\n营业收入 10 9")


def _facts(
    title: str,
    available: str,
    doc: str,
    revenue: float,
    profit: float,
    published: str | None = None,
) -> pd.DataFrame:
    text = f"""
    主要会计数据和财务指标 单位：元 币种：人民币
    营业收入 {revenue} {revenue - 1}
    归属于上市公司股东的净利润 {profit} {profit - 1}
    """
    return build_filing_fact_rows(
        entity_id="600000.SH",
        title=title,
        evidence_available_date=available,
        publication_timestamp=published or available,
        source_identity="CNINFO_ANNOUNCEMENT_ARCHIVE",
        provider="CNINFO",
        document_id=doc,
        revision_id=f"DOCUMENT:{doc}",
        document_url=f"https://static.cninfo.com.cn/finalpage/{doc}.PDF",
        document_sha256=(doc * 64)[:64],
        text=text,
    )


def test_derived_trends_use_only_prior_version_available_as_of_current_filing():
    old_2024 = _facts("2024年年度报告", "2025-04-20", "a", 100.0, 10.0)
    current_2025 = _facts("2025年年度报告", "2026-04-20", "b", 120.0, 12.0)
    future_restatement_2024 = _facts("2024年年度报告（修订版）", "2026-06-01", "c", 200.0, 20.0)
    evidence = derive_fundamental_trend_evidence(
        pd.concat([old_2024, current_2025, future_restatement_2024], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "b")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "a"
    assert payload["yoy_change"] == pytest.approx(0.2)
    assert pd.Timestamp(revenue["evidence_available_date"]) == pd.Timestamp("2026-04-20")


def test_later_restatement_is_append_only_not_history_rewrite():
    original = _facts("2024年年度报告", "2025-04-20", "a", 100.0, 10.0)
    restated = _facts("2024年年度报告（修订版）", "2025-06-01", "r", 110.0, 11.0)
    current = _facts("2025年年度报告", "2026-04-20", "b", 121.0, 12.1)
    evidence = derive_fundamental_trend_evidence(
        pd.concat([original, restated, current], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "b")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "r"
    assert payload["yoy_change"] == pytest.approx(0.1)


def test_same_close_date_revision_uses_official_publication_order_not_document_id():
    original = _facts(
        "2024年年度报告",
        "2025-04-21",
        "z_original",
        100.0,
        10.0,
        published="2025-04-21 09:00:00",
    )
    revision = _facts(
        "2024年年度报告（修订版）",
        "2025-04-21",
        "a_revision",
        110.0,
        11.0,
        published="2025-04-21 14:00:00",
    )
    current = _facts(
        "2025年年度报告",
        "2026-04-20",
        "current",
        121.0,
        12.1,
        published="2026-04-20 10:00:00",
    )
    evidence = derive_fundamental_trend_evidence(
        pd.concat([original, revision, current], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "current")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "a_revision"
    assert payload["yoy_change"] == pytest.approx(0.1)


class _FakeResponse:
    def __init__(self, content: bytes):
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._content


def test_cninfo_static_403_uses_only_official_https_download_fallback():
    calls: list[str] = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(
                request.full_url,
                403,
                "Forbidden",
                hdrs=None,
                fp=None,
            )
        return _FakeResponse(b"%PDF-1.7 exact bulletin bytes")

    original = "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"
    downloaded = download_official_document(original, opener=opener)

    assert calls[0] == original
    assert calls[1] == (
        "https://www.cninfo.com.cn/new/announcement/download"
        "?bulletinId=1225568832&announceTime=2026-09-16"
    )
    assert downloaded.url == original
    assert downloaded.retrieval_url == calls[1]
    assert downloaded.content.startswith(b"%PDF-")
    assert len(downloaded.sha256) == 64


def test_non_cninfo_or_non_403_download_failure_does_not_substitute_source():
    calls: list[str] = []

    def opener(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)

    with pytest.raises(HTTPError):
        download_official_document(
            "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF",
            opener=opener,
        )
    assert calls == [
        "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"
    ]


def test_cninfo_fallback_retries_transient_transport_failure_only():
    calls: list[str] = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)
        if len(calls) == 2:
            raise ConnectionResetError(104, "reset")
        return _FakeResponse(b"%PDF-1.7 retry success")

    original = "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"
    downloaded = download_official_document(original, opener=opener)

    assert downloaded.url == original
    assert downloaded.retrieval_url == calls[-1]
    assert len(calls) == 3
    assert calls[1] == calls[2]

def test_cninfo_double_403_uses_cookie_session_https_only(monkeypatch):
    opener_calls: list[str] = []

    def opener(request, timeout):
        opener_calls.append(request.full_url)
        raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)

    class FakeResponse:
        def __init__(self, url: str, status_code: int, content: bytes):
            self.url = url
            self.status_code = status_code
            self.content = content

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(f"unexpected status {self.status_code}")

    class FakeSession:
        def __init__(self):
            self.calls: list[str] = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == "https://www.cninfo.com.cn/":
                return FakeResponse(url, 200, b"<html></html>")
            if url.startswith("https://static.cninfo.com.cn/"):
                return FakeResponse(url, 403, b"")
            return FakeResponse(url, 200, b"%PDF-1.7 session transport")

        def close(self):
            pass

    session = FakeSession()
    monkeypatch.setattr(filing_module.requests, "Session", lambda: session)

    original = "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"
    downloaded = download_official_document(original, opener=opener)

    assert opener_calls == [
        original,
        (
            "https://www.cninfo.com.cn/new/announcement/download"
            "?bulletinId=1225568832&announceTime=2026-09-16"
        ),
    ]
    assert session.calls[0] == "https://www.cninfo.com.cn/"
    assert session.calls[1] == original
    assert session.calls[2].startswith("https://www.cninfo.com.cn/new/announcement/download?")
    assert all(url.startswith("https://") for url in session.calls)
    assert downloaded.url == original
    assert downloaded.retrieval_url == session.calls[2]
    assert downloaded.content.startswith(b"%PDF-")

def test_cninfo_triple_403_uses_browser_fingerprint_https_fallback(monkeypatch):
    opener_calls: list[str] = []
    browser_calls: list[tuple[str, str]] = []

    def opener(request, timeout):
        opener_calls.append(request.full_url)
        raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)

    def session_fallback(canonical_url, fallback_url, *, timeout):
        raise HTTPError(
            fallback_url,
            403,
            "Forbidden after CNINFO HTTPS session transport",
            hdrs=None,
            fp=None,
        )

    def browser_fallback(canonical_url, fallback_url, *, timeout):
        browser_calls.append((canonical_url, fallback_url))
        assert timeout == 30.0
        return (
            b"%PDF-1.7 browser transport",
            fallback_url,
        )

    monkeypatch.setattr(
        filing_module,
        "_download_cninfo_with_https_session",
        session_fallback,
    )
    monkeypatch.setattr(
        filing_module,
        "_download_cninfo_with_browser_transport",
        browser_fallback,
    )

    original = "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"
    downloaded = download_official_document(original, opener=opener)

    expected_fallback = (
        "https://www.cninfo.com.cn/new/announcement/download"
        "?bulletinId=1225568832&announceTime=2026-09-16"
    )
    assert opener_calls == [original, expected_fallback]
    assert browser_calls == [(original, expected_fallback)]
    assert downloaded.url == original
    assert downloaded.retrieval_url == expected_fallback
    assert downloaded.content.startswith(b"%PDF-")
    assert len(downloaded.sha256) == 64

