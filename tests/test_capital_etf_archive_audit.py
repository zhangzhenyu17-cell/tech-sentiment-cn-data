import pandas as pd
import pytest

from tech_sentiment.capital_etf_archive_audit import (
    EYESOFBLUE_ARCHIVE_SOURCE_ID,
    PUBLIC_ETF_ARCHIVE_REGISTRY,
    ZXTCC_METHOD_SOURCE_ID,
    audit_public_etf_share_archive,
    normalize_public_etf_share_archive,
)


def _archive_rows(dates):
    return pd.DataFrame(
        {
            "trade_date": dates,
            "exchange": "SSE",
            "fund_code": "588000",
            "total_shares_10k": [900000.12 + i for i in range(len(dates))],
            "fetched_at": "2026-07-12T10:00:00+08:00",
        }
    )


def test_archive_normalization_converts_10k_shares_and_keeps_candidate_status():
    frame = pd.concat(
        [
            _archive_rows(["2026-07-10"]),
            pd.DataFrame(
                {
                    "trade_date": ["2026-07-10"],
                    "exchange": ["SZSE"],
                    "fund_code": ["159919"],
                    "total_shares_10k": [100.0],
                    "fetched_at": ["2026-07-12T10:00:00+08:00"],
                }
            ),
        ],
        ignore_index=True,
    )
    out = normalize_public_etf_share_archive(frame, fund_code="588000")
    assert len(out) == 1
    assert out.loc[0, "fund_shares"] == 9_000_001_200
    assert out.loc[0, "point_in_time_status"] == "NOT_ESTABLISHED_BY_ARCHIVE"
    assert out.loc[0, "archive_role"] == "CANDIDATE_ONLY"
    assert out.loc[0, "source_identity"] == EYESOFBLUE_ARCHIVE_SOURCE_ID


def test_archive_audit_marks_missing_early_history_and_never_promotes_canonical():
    calendar = pd.bdate_range("2022-12-20", "2023-03-31")
    archive_dates = calendar[calendar >= pd.Timestamp("2023-01-03")]
    result = audit_public_etf_share_archive(
        _archive_rows(archive_dates.strftime("%Y-%m-%d").tolist()),
        trading_dates=calendar,
        fund_code="588000",
        window=20,
        min_coverage=0.80,
    )
    assert result.summary["status"] == "CANDIDATE_ARCHIVE_PARTIAL_PREHISTORY"
    assert result.summary["first_observed_date"] == "2023-01-03"
    assert result.summary["prehistory_gap_days"] > 0
    assert result.summary["point_in_time_qualified"] is False
    assert result.summary["canonical_input_qualified"] is False
    assert result.summary["production_or_model_output"] is False


def test_archive_duplicate_date_fails_closed():
    frame = _archive_rows(["2026-07-10", "2026-07-10"])
    with pytest.raises(ValueError, match="duplicate dates"):
        normalize_public_etf_share_archive(frame, fund_code="588000")


def test_method_reference_cannot_be_used_as_archive_data():
    registry = PUBLIC_ETF_ARCHIVE_REGISTRY[ZXTCC_METHOD_SOURCE_ID]
    assert registry["role"] == "METHOD_REFERENCE_ONLY"
    assert registry["versioned_588000_archive"] is False
    with pytest.raises(ValueError, match="method-only source"):
        normalize_public_etf_share_archive(
            _archive_rows(["2026-07-10"]),
            fund_code="588000",
            source_identity=ZXTCC_METHOD_SOURCE_ID,
        )
