from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.materialization_manifest import (
    MaterializedAsset,
    file_sha256,
    write_manifest,
)
from tech_sentiment.pit_public_data import normalize_cninfo_announcements
from tech_sentiment.pit_replay import (
    append_only_pit,
    assert_prefix_replay_equality,
    replay_pit_as_of,
)


def _calendar():
    return pd.to_datetime(["2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08", "2026-05-11"])


def _raw(publication="2026-05-06 14:30:00", document_id="A1", title="2025年年度报告"):
    return pd.DataFrame(
        {
            "代码": ["600276"],
            "公告标题": [title],
            "公告时间": [publication],
            "announcementId": [document_id],
            "orgId": ["org-600276"],
        }
    )


def _normalize(frame):
    return normalize_cninfo_announcements(
        frame,
        entity_id="600276.SH",
        trading_dates=_calendar(),
        ingestion_timestamp="2026-09-17T12:00:00Z",
        repository_sha="abc123",
    )


def test_precise_preclose_announcement_is_available_same_market_date():
    out = _normalize(_raw("2026-05-06 14:30:00"))
    assert out.loc[0, "event_date"] == pd.Timestamp("2026-05-06")
    assert out.loc[0, "evidence_available_date"] == pd.Timestamp("2026-05-06")
    assert out.loc[0, "publication_precision"] == "TIMESTAMP"


def test_date_only_and_after_close_are_conservatively_delayed():
    date_only = _normalize(_raw("2026-05-06"))
    assert date_only.loc[0, "evidence_available_date"] == pd.Timestamp("2026-05-07")
    assert date_only.loc[0, "publication_precision"] == "DATE_ONLY_CONSERVATIVE_NEXT_TRADE_DATE"

    after_close = _normalize(_raw("2026-05-06 15:30:00", document_id="A2"))
    assert after_close.loc[0, "evidence_available_date"] == pd.Timestamp("2026-05-07")


def test_nontrading_announcement_moves_to_next_real_trading_date():
    weekend = _normalize(_raw("2026-05-09 09:00:00", document_id="A3"))
    assert weekend.loc[0, "evidence_available_date"] == pd.Timestamp("2026-05-11")


def test_missing_post_publication_trade_date_fails_closed():
    with pytest.raises(ValueError, match="post-publication market date"):
        _normalize(_raw("2026-05-11", document_id="A4"))


def test_append_only_replay_is_idempotent_and_conflicts_fail_closed():
    first = _normalize(_raw(document_id="A5", title="原始公告"))
    replay = append_only_pit(first, first.copy())
    assert len(replay) == 1

    conflict = first.copy()
    conflict.loc[0, "announcement_title"] = "被篡改标题"
    with pytest.raises(ValueError, match="append-only PIT conflict"):
        append_only_pit(first, conflict)


def test_revision_safe_replay_keeps_later_document_out_of_earlier_prefix():
    original = _normalize(_raw("2026-05-06 14:30:00", document_id="A6", title="原公告"))
    correction = _normalize(_raw("2026-05-08 10:00:00", document_id="A7", title="更正公告"))
    ledger = append_only_pit(original, correction)
    may7 = replay_pit_as_of(ledger, "2026-05-07")
    assert list(may7["document_id"]) == ["A6"]
    may8 = replay_pit_as_of(ledger, "2026-05-08")
    assert list(may8["document_id"]) == ["A6", "A7"]
    assert_prefix_replay_equality(ledger, "2026-05-07")


def test_manifest_pins_hash_source_and_nonproduction_state(tmp_path: Path):
    artifact = tmp_path / "pit.csv"
    artifact.write_text("a,b\n1,2\n", encoding="utf-8")
    asset = MaterializedAsset(
        name="cninfo_pit",
        path="pit.csv",
        sha256=file_sha256(artifact),
        rows=1,
        state="PARTIAL_COVERAGE",
        source_identity="CNINFO_ANNOUNCEMENT_ARCHIVE",
        provider="CNINFO",
        provenance="official announcement archive",
        immutable_version="sha256:" + file_sha256(artifact),
        revision_semantics="append-only; later documents never overwrite earlier evidence",
    )
    manifest = write_manifest(
        [asset],
        output_path=tmp_path / "manifest.json",
        repository_sha="deadbeef",
        generated_at="2026-09-17T12:00:00Z",
    )
    assert manifest["production_or_model_output"] is False
    assert manifest["assets"][0]["state"] == "PARTIAL_COVERAGE"


def test_manifest_rejects_unregistered_readiness_state():
    with pytest.raises(ValueError, match="invalid readiness state"):
        MaterializedAsset(
            name="x",
            path="x.csv",
            sha256="0" * 64,
            rows=0,
            state="READY",
            source_identity="X",
            provider="X",
            provenance="X",
            immutable_version="X",
            revision_semantics="X",
        )
