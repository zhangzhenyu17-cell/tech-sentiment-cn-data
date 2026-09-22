from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from tech_sentiment.immutable_checkpoint import ImmutableCheckpointStore
from tech_sentiment.prospective_context_checkpoint_v1 import semantic_fingerprint
from tech_sentiment.resumable_capital import (
    expected_capital_checkpoint_identities,
    expected_szse_etf_checkpoint_identities,
)
from tech_sentiment.prospective_context_raw_v1 import (
    RECEIPT_NAME,
    _stamp_capture,
    _validate_window_symbol_coverage,
    _validate_live_snapshot_exact,
    _validate_same_day_capital_preflight,
    _same_day_capital_checkpoint_preflight_ready,
    _same_day_capital_preflight,
    capture_trading_dates,
    package_capture,
)


ROOT = Path(__file__).resolve().parents[1]


class _CalendarClient:
    def __init__(self, dates: list[str]):
        self.dates = dates

    def tool_trade_date_hist_sina(self):
        return pd.DataFrame({"trade_date": pd.to_datetime(self.dates)})


def _membership(date: str = "2026-09-21") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [f"{i:06d}" for i in range(1, 51)],
            "effective_start": ["2026-01-01"] * 50,
            "effective_end": [date] * 50,
        }
    )


def _live() -> pd.DataFrame:
    return pd.DataFrame({"symbol": [f"{i:06d}" for i in range(1, 51)]})


def test_capture_calendar_requires_real_trading_day_and_sufficient_warmup() -> None:
    dates = pd.bdate_range("2025-01-02", periods=360).date.astype(str).tolist()
    target = dates[-1]
    captured = capture_trading_dates(
        target,
        warmup_trading_days=340,
        client=_CalendarClient(dates),
    )
    assert len(captured) == 340
    assert captured[-1] == pd.Timestamp(target)

    with pytest.raises(ValueError, match="at least 313"):
        capture_trading_dates(
            target,
            warmup_trading_days=312,
            client=_CalendarClient(dates),
        )

    with pytest.raises(ValueError, match="not a confirmed"):
        capture_trading_dates(
            "2026-12-31",
            warmup_trading_days=340,
            client=_CalendarClient(dates),
        )


def test_live_snapshot_must_exactly_match_reconstructed_active_membership() -> None:
    active, live = _validate_live_snapshot_exact(
        membership=_membership(),
        live_snapshot=_live(),
        operation_date="2026-09-21",
        expected_constituents=50,
        universe="TEST50",
    )
    assert active == live

    drifted = _live()
    drifted.loc[0, "symbol"] = "999999"
    with pytest.raises(ValueError, match="does not exactly match"):
        _validate_live_snapshot_exact(
            membership=_membership(),
            live_snapshot=drifted,
            operation_date="2026-09-21",
            expected_constituents=50,
            universe="TEST50",
        )


def test_constituent_coverage_reuses_frozen_window_symbol_semantics() -> None:
    membership = _membership()
    symbols = membership["symbol"].astype(str).tolist()
    rows = [
        {"date": "2026-09-18", "symbol": symbol}
        for symbol in symbols[:48]
    ]
    coverage = _validate_window_symbol_coverage(
        prices=pd.DataFrame(rows),
        membership=membership,
        minimum_coverage=0.95,
        universe="TEST50",
    )
    assert coverage == pytest.approx(48 / 50)

    with pytest.raises(ValueError, match="coverage"):
        _validate_window_symbol_coverage(
            prices=pd.DataFrame(rows[:47]),
            membership=membership,
            minimum_coverage=0.95,
            universe="TEST50",
        )



def test_same_day_capital_preflight_fails_closed_when_588000_not_published() -> None:
    sse = pd.DataFrame(
        {
            "date": ["2026-09-18"],
            "fund_code": ["588000"],
            "fund_shares": [9_000_000_000.0],
        }
    )
    szse = pd.DataFrame(
        {
            "date": ["2026-09-21"],
            "fund_code": ["159915"],
            "fund_shares": [7_000_000_000.0],
        }
    )
    turnover = pd.DataFrame(
        {
            "date": ["2026-09-21"],
            "amount": [1.2e12],
        }
    )
    with pytest.raises(ValueError, match="588000") as exc:
        _validate_same_day_capital_preflight(
            operation_date="2026-09-21",
            sse_etf_data=sse,
            szse_etf_data=szse,
            turnover_data=turnover,
            diagnostics={"sse_588000_errors": [{"error": "NO_MATCHING_ETF_ROW"}]},
        )
    assert "SAME_DAY_PUBLIC_SOURCE_NOT_READY" in str(exc.value)
    assert "readiness_class=NOT_YET_PUBLISHED" in str(exc.value)


def test_same_day_capital_preflight_lists_all_missing_sources() -> None:
    with pytest.raises(ValueError) as exc:
        _validate_same_day_capital_preflight(
            operation_date="2026-09-21",
            sse_etf_data=pd.DataFrame(),
            szse_etf_data=pd.DataFrame(),
            turnover_data=pd.DataFrame(),
            diagnostics={},
        )
    message = str(exc.value)
    assert "588000" in message
    assert "159915" in message
    assert "SSE_SZSE_TURNOVER" in message


def test_preopen_publication_probe_retries_known_not_yet_published_state() -> None:
    calls = {"sse": 0, "szse": 0, "turnover": 0}
    sleeps: list[float] = []

    def sse_fetcher(**_kwargs):
        calls["sse"] += 1
        if calls["sse"] == 1:
            return SimpleNamespace(
                data=pd.DataFrame(),
                errors=pd.DataFrame(
                    [{"date": "2026-09-22", "error": "NO_MATCHING_ETF_ROW"}]
                ),
            )
        return SimpleNamespace(
            data=pd.DataFrame(
                {
                    "date": ["2026-09-22"],
                    "fund_code": ["588000"],
                    "fund_shares": [9_000_000_000.0],
                }
            ),
            errors=pd.DataFrame(columns=["date", "error"]),
        )

    def szse_fetcher(**_kwargs):
        calls["szse"] += 1
        if calls["szse"] == 1:
            return SimpleNamespace(
                data=pd.DataFrame(),
                errors=pd.DataFrame(
                    [
                        {
                            "chunk_start": "2026-09-22",
                            "chunk_end": "2026-09-22",
                            "error": "RuntimeError: SZSE ETF source returned no rows",
                        }
                    ]
                ),
            )
        return SimpleNamespace(
            data=pd.DataFrame(
                {
                    "date": ["2026-09-22"],
                    "fund_code": ["159915"],
                    "fund_shares": [7_000_000_000.0],
                }
            ),
            errors=pd.DataFrame(columns=["chunk_start", "chunk_end", "error"]),
        )

    def turnover_fetcher(**_kwargs):
        calls["turnover"] += 1
        return SimpleNamespace(
            combined=pd.DataFrame(
                {
                    "date": ["2026-09-22"],
                    "amount": [1.2e12],
                }
            ),
            errors=pd.DataFrame(columns=["date", "exchange", "error"]),
        )

    _same_day_capital_preflight(
        operation_date="2026-09-22",
        publication_retry_attempts=2,
        publication_retry_backoff_seconds=3.0,
        sleeper=sleeps.append,
        sse_history_fetcher=sse_fetcher,
        szse_history_fetcher=szse_fetcher,
        turnover_history_fetcher=turnover_fetcher,
    )

    assert calls == {"sse": 2, "szse": 2, "turnover": 2}
    assert sleeps == [3.0]


def test_preopen_publication_probe_does_not_retry_schema_or_source_failure() -> None:
    calls = {"sse": 0}
    sleeps: list[float] = []

    def sse_fetcher(**_kwargs):
        calls["sse"] += 1
        return SimpleNamespace(
            data=pd.DataFrame(),
            errors=pd.DataFrame(
                [{"date": "2026-09-22", "error": "ValueError: schema drift"}]
            ),
        )

    def szse_fetcher(**_kwargs):
        return SimpleNamespace(
            data=pd.DataFrame(
                {
                    "date": ["2026-09-22"],
                    "fund_code": ["159915"],
                    "fund_shares": [7_000_000_000.0],
                }
            ),
            errors=pd.DataFrame(columns=["chunk_start", "chunk_end", "error"]),
        )

    def turnover_fetcher(**_kwargs):
        return SimpleNamespace(
            combined=pd.DataFrame({"date": ["2026-09-22"], "amount": [1.2e12]}),
            errors=pd.DataFrame(columns=["date", "exchange", "error"]),
        )

    with pytest.raises(ValueError, match="SOURCE_FAILURE_OR_INCOMPLETE"):
        _same_day_capital_preflight(
            operation_date="2026-09-22",
            publication_retry_attempts=4,
            publication_retry_backoff_seconds=3.0,
            sleeper=sleeps.append,
            sse_history_fetcher=sse_fetcher,
            szse_history_fetcher=szse_fetcher,
            turnover_history_fetcher=turnover_fetcher,
        )

    assert calls["sse"] == 1
    assert sleeps == []


def test_same_day_capital_preflight_passes_only_exact_operation_date_rows() -> None:
    sse = pd.DataFrame(
        {
            "date": ["2026-09-21"],
            "fund_code": ["588000"],
            "fund_shares": [9_000_000_000.0],
        }
    )
    szse = pd.DataFrame(
        {
            "date": ["2026-09-21"],
            "fund_code": ["159915"],
            "fund_shares": [7_000_000_000.0],
        }
    )
    turnover = pd.DataFrame(
        {
            "date": ["2026-09-21"],
            "amount": [1.2e12],
        }
    )
    _validate_same_day_capital_preflight(
        operation_date="2026-09-21",
        sse_etf_data=sse,
        szse_etf_data=szse,
        turnover_data=turnover,
        diagnostics={},
    )


def test_exact_same_capture_capital_checkpoints_can_satisfy_freshness_without_provider(
    tmp_path: Path,
) -> None:
    dates = pd.to_datetime(["2026-09-18", "2026-09-21"])
    operation_date = "2026-09-21"

    capital_revision = str(
        semantic_fingerprint(ROOT, family="capital:sse")[
            "checkpoint_revision"
        ]
    )
    szse_revision = str(
        semantic_fingerprint(ROOT, family="capital:szse")[
            "checkpoint_revision"
        ]
    )
    sse_expected = expected_capital_checkpoint_identities(
        trading_dates=dates,
        fund_codes=["588000"],
        checkpoint_revision=capital_revision,
        capture_date=operation_date,
    )
    szse_expected = expected_szse_etf_checkpoint_identities(
        trading_dates=dates,
        fund_codes=["159915"],
        checkpoint_revision=szse_revision,
        capture_date=operation_date,
    )

    sse_store = ImmutableCheckpointStore(tmp_path / "sse")
    for _, identity in sse_expected:
        if identity.producer == "sse-etf-share-history":
            sse_store.save(
                identity,
                frames={
                    "data": pd.DataFrame(
                        {
                            "date": dates,
                            "fund_code": ["588000", "588000"],
                            "fund_shares": [1.0, 2.0],
                        }
                    ),
                    "errors": pd.DataFrame(columns=["date", "error"]),
                },
                metadata={
                    "capture_date": operation_date,
                    "permanent_reuse_eligible": True,
                },
            )
        else:
            sse_store.save(
                identity,
                frames={
                    "sse": pd.DataFrame(
                        {
                            "date": dates,
                            "sse_a_share_turnover_yuan": [1.0, 1.0],
                        }
                    ),
                    "szse": pd.DataFrame(
                        {
                            "date": dates,
                            "szse_a_share_turnover_yuan": [1.0, 1.0],
                        }
                    ),
                    "combined": pd.DataFrame(
                        {
                            "date": dates,
                            "amount": [2.0, 2.0],
                        }
                    ),
                    "errors": pd.DataFrame(
                        columns=["date", "exchange", "error"]
                    ),
                },
                metadata={
                    "capture_date": operation_date,
                    "permanent_reuse_eligible": True,
                },
            )

    szse_store = ImmutableCheckpointStore(tmp_path / "szse")
    for _, identity in szse_expected:
        szse_store.save(
            identity,
            frames={
                "data": pd.DataFrame(
                    {
                        "date": dates,
                        "fund_code": ["159915", "159915"],
                        "fund_shares": [1.0, 2.0],
                    }
                ),
                "errors": pd.DataFrame(
                    columns=["chunk_start", "chunk_end", "error"]
                ),
            },
            metadata={
                "capture_date": operation_date,
                "permanent_reuse_eligible": True,
            },
        )

    assert _same_day_capital_checkpoint_preflight_ready(
        repo_root=ROOT,
        checkpoint_dir=tmp_path,
        trading_dates=pd.DatetimeIndex(dates),
        operation_date=operation_date,
    )
    assert not _same_day_capital_checkpoint_preflight_ready(
        repo_root=ROOT,
        checkpoint_dir=tmp_path,
        trading_dates=pd.DatetimeIndex(
            pd.to_datetime(["2026-09-18", "2026-09-22"])
        ),
        operation_date="2026-09-22",
    )

def test_public_contract_is_forward_only_and_contains_no_private_model_semantics() -> None:
    contract = json.loads(
        (ROOT / "reference/prospective_context_raw_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["operation_date_semantics"]["historical_replay_allowed"] is False
    assert (
        contract["operation_date_semantics"][
            "retroactive_evidence_qualification_allowed"
        ]
        is False
    )
    assert contract["workflow"]["workflow_dispatch_only"] is True
    assert contract["workflow"]["automatic_trigger_allowed"] is False
    persistence = contract["intermediate_checkpoint_persistence"]
    assert persistence["schema"] == "prospective-context-checkpoint-progress-v1"
    assert persistence["completed_work_units_published_immutably"] is True
    assert persistence["publish_completed_units_even_when_later_capture_step_fails"] is True
    assert persistence["semantic_revision_separate_from_operational_git_commit"] is True
    assert persistence["operation_date_is_part_of_reuse_identity"] is True
    assert persistence["cross_operation_date_reuse_allowed"] is False
    assert persistence["formal_evidence_handoff"] is False
    assert persistence["qualification_granted_by_checkpoint"] is False
    assert persistence["normal_failure_publish_attempt_required"] is True
    assert persistence["hard_runner_termination_publish_guaranteed"] is False
    assert (
        persistence["maximum_expected_loss_on_hard_termination"]
        == "CURRENT_UNPUBLISHED_WORK_UNITS_ONLY"
    )
    firewall = contract["privacy_and_research_firewall"]
    assert all(value is False for value in firewall.values())
    serialized = json.dumps(contract, ensure_ascii=False).lower()
    for forbidden in (
        "market_liquidity_percentile",
        "long_horizon_flow_strength",
        "flow_persistence",
        "short_term_speculation_heat",
        "rotation_crowding_pressure",
        "crowding_score",
    ):
        assert forbidden not in serialized


def test_package_capture_is_byte_deterministic_for_same_capture(tmp_path: Path) -> None:
    root = tmp_path / "capture"
    root.mkdir()
    (root / "sample.csv").write_text("date,value\n2026-09-21,1\n", encoding="utf-8")
    receipt = {
        "schema_version": "prospective-context-public-raw-receipt-v1",
        "status": "PUBLIC_RAW_FORWARD_CAPTURE_COMPLETE",
        "operation_date": "2026-09-21",
        "historical_replay_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
        "private_model_semantics_materialized": False,
    }
    (root / RECEIPT_NAME).write_text(
        json.dumps(receipt, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    m1 = package_capture(root, output_dir=first, operation_date="2026-09-21")
    m2 = package_capture(root, output_dir=second, operation_date="2026-09-21")
    assert m1["bundle_identity"] == m2["bundle_identity"]
    assert m1["release_tag"] == "prospective-context-raw-2026-09-21"
    a1 = first / m1["archive"]
    a2 = second / m2["archive"]
    assert a1.read_bytes() == a2.read_bytes()



def test_canonical_capture_stamp_excludes_volatile_wall_clock() -> None:
    frame = pd.DataFrame({"date": ["2026-09-21"], "value": [1]})
    stamped = _stamp_capture(frame, capture_date="2026-09-21")
    assert stamped["forward_capture_date"].tolist() == ["2026-09-21"]
    assert stamped["historical_replay_allowed"].tolist() == [False]
    assert "forward_captured_at_utc" not in stamped.columns

def test_public_capture_workflow_is_retired_fail_closed() -> None:
    text = (
        ROOT / ".github/workflows/prospective-context-raw-v1.yml"
    ).read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "\n  schedule:" not in text
    assert "\n  push:" not in text
    assert "\n  pull_request:" not in text
    assert "\n  workflow_run:" not in text
    assert "contents: read" in text
    assert "contents: write" not in text
    assert "LEGACY_PROSPECTIVE_RAIL_SUPERSEDED_BY_TIMING_V2" in text
    assert "prospective-context-raw-preopen-v2" in text
    assert "build_prospective_context_raw_v1.py" not in text
    assert "prospective_context_checkpoint_bundle.py" not in text
