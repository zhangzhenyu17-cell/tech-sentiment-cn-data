import json

import pandas as pd

import tech_sentiment.earnings_materialization as module
from tech_sentiment.immutable_checkpoint import ImmutableCheckpointStore
from tech_sentiment.official_filing_facts import DownloadedOfficialDocument


def _announcement(title="2025年度业绩预告"):
    return pd.DataFrame(
        [
            {
                "代码": "600000",
                "简称": "测试",
                "公告标题": title,
                "公告时间": "2026-01-05 10:00:00",
                "公告链接": (
                    "https://www.cninfo.com.cn/new/disclosure/detail?"
                    "stockCode=600000&announcementId=ABC123&orgId=gssh0600000"
                ),
                "公告附件链接": "https://static.cninfo.com.cn/finalpage/2026-01-05/ABC123.PDF",
            }
        ]
    )


def test_exact_official_document_emits_canonical_cninfo_direction_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "fetch_cninfo_announcements_direct", lambda **kwargs: _announcement())
    monkeypatch.setattr(
        module,
        "download_official_document",
        lambda url: DownloadedOfficialDocument(url=url, sha256="a" * 64, content=b"pdf"),
    )
    monkeypatch.setattr(module, "extract_pdf_text", lambda content: "业绩预告类型：预减")

    result = module.materialize_cninfo_earnings_directions(
        ["600000"],
        query_start_date="2025-01-01",
        end_date="2026-01-06",
        trading_dates=pd.to_datetime(["2026-01-05", "2026-01-06"]),
        source_commit="abc123",
        checkpoint_dir=tmp_path,
    )
    assert result.summary["readiness_state"] == "QUALIFIED_INPUT"
    assert len(result.evidence) == 1
    row = result.evidence.iloc[0]
    assert row["source_identity"] == "CNINFO_ANNOUNCEMENT_ARCHIVE"
    assert row["provider"] == "CNINFO"
    assert row["revision_id"] == f"DOCUMENT_SHA256:{'a' * 64}"
    payload = json.loads(row["evidence_payload"])
    assert payload["earnings_expectation_direction"] == "DOWN"
    provenance = json.loads(row["provenance"])
    assert provenance["source_identity_inherited_not_new_evidence_source"] is True
    assert provenance["future_prices_or_returns_used"] is False


def test_unknown_document_direction_is_not_treated_as_not_down(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "fetch_cninfo_announcements_direct", lambda **kwargs: _announcement())
    monkeypatch.setattr(
        module,
        "download_official_document",
        lambda url: DownloadedOfficialDocument(url=url, sha256="b" * 64, content=b"pdf"),
    )
    monkeypatch.setattr(module, "extract_pdf_text", lambda content: "公司预计本期经营情况存在不确定性")

    result = module.materialize_cninfo_earnings_directions(
        ["600000"],
        query_start_date="2025-01-01",
        end_date="2026-01-06",
        trading_dates=pd.to_datetime(["2026-01-05", "2026-01-06"]),
        source_commit="abc123",
        checkpoint_dir=tmp_path,
    )
    assert result.evidence.empty
    assert result.summary["readiness_state"] == "QUALIFIED_INPUT"
    assert result.summary["unknown_is_not_not_down"] is True
    assert result.summary["unclassified_documents"] == 1
    assert result.errors.empty
    assert len(result.unclassified) == 1
    row = result.unclassified.iloc[0]
    assert row["reason"] == "UNCLASSIFIED_NO_EXPLICIT_UNAMBIGUOUS_DIRECTION"
    assert row["document_sha256"] == "b" * 64


def test_download_failure_remains_hard_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "fetch_cninfo_announcements_direct", lambda **kwargs: _announcement())

    def fail_download(url):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(module, "download_official_document", fail_download)

    result = module.materialize_cninfo_earnings_directions(
        ["600000"],
        query_start_date="2025-01-01",
        end_date="2026-01-06",
        trading_dates=pd.to_datetime(["2026-01-05", "2026-01-06"]),
        source_commit="abc123",
        checkpoint_dir=tmp_path,
    )
    assert result.summary["readiness_state"] == "PARTIAL_COVERAGE"
    assert len(result.errors) == 1
    assert result.unclassified.empty
    assert result.coverage.iloc[0]["query_status"] == "FAILED"


def test_unknown_direction_progress_checkpoint_survives_runtime_commit_change(
    tmp_path,
    monkeypatch,
):
    legacy_commit = "1" * 40
    progress_commit = "2" * 40
    legacy_root = tmp_path / "legacy"
    progress_root = tmp_path / "progress"
    legacy_store = ImmutableCheckpointStore(legacy_root)

    raw = _announcement()
    legacy_query = module._symbol_query_identity(
        source_commit=legacy_commit,
        symbol="600000",
        query_start="2025-01-01",
        query_end="2026-01-06",
    )
    legacy_store.save(
        legacy_query,
        frames={"announcements": raw},
        metadata={"entity_id": "600000.SH"},
    )

    monkeypatch.setattr(
        module,
        "fetch_cninfo_announcements_direct",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy query checkpoint should be reused")
        ),
    )
    downloads = {"count": 0}

    def download(url):
        downloads["count"] += 1
        return DownloadedOfficialDocument(
            url=url,
            sha256="c" * 64,
            content=b"pdf",
        )

    monkeypatch.setattr(module, "download_official_document", download)
    monkeypatch.setattr(
        module,
        "extract_pdf_text",
        lambda content: "公司预计本期经营情况存在不确定性",
    )

    first = module.materialize_cninfo_earnings_directions(
        ["600000"],
        query_start_date="2025-01-01",
        end_date="2026-01-06",
        trading_dates=pd.to_datetime(["2026-01-05", "2026-01-06"]),
        source_commit="3" * 40,
        checkpoint_source_commit=legacy_commit,
        progress_checkpoint_source_commit=progress_commit,
        checkpoint_dir=progress_root,
        legacy_checkpoint_dir=legacy_root,
    )
    assert downloads["count"] == 1
    assert first.summary["checkpoint_store_mode"] == (
        "SPLIT_LEGACY_AND_CURRENT_PROGRESS_STORES_V1"
    )
    assert first.summary["reused_legacy_queries"] == 1
    assert first.summary["unclassified_documents"] == 1

    monkeypatch.setattr(
        module,
        "download_official_document",
        lambda url: (_ for _ in ()).throw(
            AssertionError("durable earnings progress should avoid re-download")
        ),
    )
    second = module.materialize_cninfo_earnings_directions(
        ["600000"],
        query_start_date="2025-01-01",
        end_date="2026-01-06",
        trading_dates=pd.to_datetime(["2026-01-05", "2026-01-06"]),
        source_commit="4" * 40,
        checkpoint_source_commit=legacy_commit,
        progress_checkpoint_source_commit=progress_commit,
        checkpoint_dir=progress_root,
        legacy_checkpoint_dir=legacy_root,
    )
    assert second.summary["resumed_documents"] == 1
    assert second.summary["unclassified_documents"] == 1
    assert second.errors.empty
