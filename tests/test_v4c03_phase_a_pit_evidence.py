from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "reference/v4c03_phase_a_pit_evidence_v1.json"
WORKFLOW = ROOT / ".github/workflows/v4c03-04-phase-a-pit-evidence.yml"
PREPARE = ROOT / "scripts/prepare_v4c03_phase_a_evidence_inputs.py"
FINALIZE = ROOT / "scripts/finalize_v4c03_phase_a_evidence.py"


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def test_v4c03_phase_a_evidence_contract_is_frozen_public_only() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["status"] == "FROZEN_PUBLIC_DATA_SCOPE"
    assert contract["evidence_window"] == {
        "start_date": "2021-01-04",
        "end_date": "2021-12-31",
        "reason": (
            "Provide non-sample warm-up for capital rolling state, valuation T-20 "
            "references, and as-of evidence state before the 2021-06-15 sample boundary."
        ),
    }
    assert contract["sample_window"] == {
        "start_date": "2021-06-15",
        "end_date": "2021-12-31",
    }
    assert contract["pre_sample_evidence_rows_sample_eligible"] is False
    assert contract["workflow_semantics"]["manual_only"] is True
    assert contract["workflow_semantics"]["independent_reusable_work_units"] is True
    assert contract["automatic_trigger_allowed"] is False
    assert contract["private_model_semantics_allowed"] is False
    assert contract["portfolio_or_holdings_data_allowed"] is False
    assert contract["forward_result_computation_allowed"] is False
    assert contract["evidence_qualification_semantics_changed"] is False
    assert contract["production_or_trading_authority_changed"] is False
    assert contract["public_inputs"]["phase_a"]["bundle_identity"] == (
        "8713ba348104973c3fe206da09511aa7a220363a18d5d47a7e251e36160c8abc"
    )
    assert contract["public_inputs"]["warmup"]["bundle_identity"] == (
        "ffccc7ffd3d5e2aff8a6fe74c4ed4b76603835646ffe7994a57a49cd508a97e7"
    )


def test_v4c03_phase_a_evidence_workflow_is_manual_and_failure_isolated() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: v4c03-04-phase-a-pit-evidence\n")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for forbidden in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert forbidden not in text

    assert text.count("fail-fast: false") >= 3
    assert "max-parallel: 4" in text
    assert "CNINFO_ANNOUNCEMENT_ARCHIVE" in text
    assert "SSE_ANNOUNCEMENT_ARCHIVE" in text
    assert "SZSE_ANNOUNCEMENT_ARCHIVE" in text
    assert "v4c03-phase-a-issuer-" in text
    assert "v4c03-phase-a-fundamental-" in text
    assert "v4c03-phase-a-prices-" in text
    assert "v4c03-phase-a-policy-" in text
    assert "v4c03-phase-a-capital-" in text
    assert "retention-days: 90" in text
    assert "assert_v4a_stage_qualifiable.py" not in text
    assert "research_cli" not in text
    assert "v4c_outcome" not in text
    assert "study_ice_points" not in text
    assert "study_post_warning_outcomes" not in text


def _make_market_bundle(
    root: Path,
    *,
    status: str,
    start: str,
    end: str,
    dates: list[str],
    sample_eligibility: bool | None,
) -> None:
    receipt = {
        "status": status,
        "start_date": start,
        "end_date": end,
        "source_commit": "source-commit",
        "public_only": True,
    }
    if sample_eligibility is not None:
        receipt["sample_eligibility"] = sample_eligibility
    (root / "receipt.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    for universe in ("STAR50", "ChiNext50"):
        base = root / universe
        _write_csv(
            base / "index_prices.csv",
            pd.DataFrame(
                {
                    "date": dates,
                    "close": range(100, 100 + len(dates)),
                    "amount": range(1000, 1000 + len(dates)),
                }
            ),
        )
        _write_csv(
            base / "prices.csv",
            pd.DataFrame(
                {
                    "date": [dates[-1]],
                    "symbol": ["688001" if universe == "STAR50" else "300001"],
                    "close": [10.0],
                    "pct_chg": [1.0],
                    "amount": [100.0],
                }
            ),
        )
        _write_csv(
            base / "universe_point_in_time.csv",
            pd.DataFrame(
                {
                    "symbol": ["688001" if universe == "STAR50" else "300001"],
                    "effective_start": [start],
                    "effective_end": [end],
                    "limit_pct": [20.0],
                }
            ),
        )


def test_prepare_evidence_inputs_combines_calendar_but_emits_sample_market_only(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module(PREPARE, "v4c03_prepare_inputs_test")
    phase = tmp_path / "phase"
    warm = tmp_path / "warm"
    out = tmp_path / "out"

    _make_market_bundle(
        warm,
        status="PUBLIC_PHASE_A_WARMUP_ASSEMBLED",
        start="2021-01-04",
        end="2021-06-14",
        dates=["2021-01-04", "2021-06-14"],
        sample_eligibility=False,
    )
    _make_market_bundle(
        phase,
        status="PUBLIC_PHASE_A_ASSEMBLED",
        start="2021-06-15",
        end="2021-12-31",
        dates=["2021-06-15", "2021-12-31"],
        sample_eligibility=True,
    )
    _write_csv(
        phase / "pit_symbol_scope" / "capital_pit_symbols.csv",
        pd.DataFrame({"symbol": ["688001", "300001", "688001"]}),
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(PREPARE),
            "--phase-a-root",
            str(phase),
            "--warmup-root",
            str(warm),
            "--out-dir",
            str(out),
        ],
    )
    assert module.main() == 0

    calendar = pd.read_csv(out / "trading_calendar.csv")
    assert calendar["date"].tolist() == [
        "2021-01-04",
        "2021-06-14",
        "2021-06-15",
        "2021-12-31",
    ]
    symbols = pd.read_csv(out / "symbols.csv", dtype={"symbol": str})
    assert list(symbols.columns) == ["symbol", "market"]
    assert set(symbols["symbol"].astype(str).str.zfill(6)) == {"300001", "688001"}
    assert dict(zip(symbols["symbol"], symbols["market"])) == {
        "300001": "SZ",
        "688001": "SH",
    }
    sample_star = pd.read_csv(out / "market/STAR50/index_prices.csv")
    assert sample_star["date"].tolist() == ["2021-06-15", "2021-12-31"]
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["sample_eligible_before_sample_start"] is False
    assert receipt["forward_result_computation_run"] is False


def test_szse_etf_normalization_keeps_only_exact_frozen_fund_and_dates() -> None:
    from tech_sentiment.v4c03_szse_etf_shares import (
        SZSE_ETF_SHARE_SOURCE_ID,
        normalize_szse_etf_share_history,
    )

    raw = pd.DataFrame(
        {
            "日期": ["2021-06-15", "2021-06-16", "2021-06-15"],
            "基金代码": ["159915", "159915", "159919"],
            "基金份额": [100.0, 101.0, 999.0],
        }
    )
    out = normalize_szse_etf_share_history(
        raw,
        fund_codes=["159915"],
        trading_dates=["2021-06-15"],
    )
    assert len(out) == 1
    assert out.iloc[0]["fund_code"] == "159915"
    assert float(out.iloc[0]["fund_shares"]) == 100.0
    assert out.iloc[0]["source_identity"] == SZSE_ETF_SHARE_SOURCE_ID
    assert pd.Timestamp(out.iloc[0]["evidence_available_date"]) == pd.Timestamp(
        "2021-06-15"
    )


def _minimal_finalizer_inputs(root: Path) -> tuple[Path, Path, Path]:
    inputs = root / "inputs"
    derived = root / "derived"
    capital = root / "capital"
    inputs.mkdir(parents=True)
    derived.mkdir()
    capital.mkdir()

    (inputs / "receipt.json").write_text(
        json.dumps(
            {
                "status": "V4C03_PHASE_A_EVIDENCE_INPUTS_PREPARED",
                "evidence_start": "2021-01-04",
                "sample_start": "2021-06-15",
                "end_date": "2021-12-31",
                "sample_eligible_before_sample_start": False,
            }
        ),
        encoding="utf-8",
    )
    _write_csv(inputs / "symbols.csv", pd.DataFrame({"symbol": ["688001", "300001"]}))
    _write_csv(
        inputs / "trading_calendar.csv",
        pd.DataFrame({"date": ["2021-01-04", "2021-12-31"]}),
    )
    for universe in ("STAR50", "ChiNext50"):
        for name in ("prices.csv", "universe_point_in_time.csv", "index_prices.csv"):
            _write_csv(
                inputs / "market" / universe / name,
                pd.DataFrame({"date": ["2021-06-15"], "value": [1]}),
            )

    empty = pd.DataFrame({"entity_id": pd.Series(dtype=str)})
    for name in (
        "fundamental_state_evidence.csv",
        "earnings_direction_evidence.csv",
        "major_negative_review.csv",
        "pit_evidence_extended.csv",
    ):
        _write_csv(derived / name, empty)
    _write_csv(
        derived / "trailing_valuation_rail.csv",
        pd.DataFrame({"date": ["2021-06-15"], "entity_id": ["688001.SH"]}),
    )
    (derived / "derived_pit_materialization_manifest.json").write_text(
        json.dumps(
            {
                "status": "PUBLIC_PIT_MATERIALIZATION_COMPLETED",
                "start_date": "2021-01-04",
                "end_date": "2021-12-31",
                "source_states": {"example": "DATA_INSUFFICIENT"},
                "earnings_direction_readiness_state": "DATA_INSUFFICIENT",
                "major_negative_event_exclusion_complete": False,
                "valuation_coverage": {"coverage": 0.0},
                "future_prices_or_returns_used_for_fundamental_or_earnings": False,
                "predictive_research_run": False,
                "parameter_search_run": False,
                "holdout_run": False,
                "production_run": False,
            }
        ),
        encoding="utf-8",
    )
    _write_csv(
        capital / "etf_shares.csv",
        pd.DataFrame(
            {
                "date": ["2021-06-15", "2021-06-15"],
                "fund_code": ["588000", "159915"],
                "fund_shares": [100.0, 200.0],
            }
        ),
    )
    _write_csv(
        capital / "etf_share_coverage.csv",
        pd.DataFrame({"date": ["2021-06-15"], "fund_code": ["588000"]}),
    )
    _write_csv(
        capital / "sse_szse_a_share_turnover.csv",
        pd.DataFrame({"date": ["2021-06-15"], "amount": [1000.0]}),
    )
    (capital / "capital_summary.json").write_text(
        json.dumps(
            {
                "status": "V4C03_PUBLIC_CAPITAL_MATERIALIZED",
                "start_date": "2021-01-04",
                "end_date": "2021-12-31",
                "funds": {
                    "588000": {"raw_coverage": 0.5},
                    "159915": {"raw_coverage": 0.5},
                },
                "turnover_complete_days": 1,
                "turnover_expected_days": 2,
                "turnover_error_rows": 1,
                "sse_etf_error_rows": 1,
                "szse_etf_error_chunks": 0,
                "future_prices_or_returns_used": False,
                "predictive_research_run": False,
                "parameter_search_run": False,
                "production_or_trading_authority_changed": False,
            }
        ),
        encoding="utf-8",
    )
    return inputs, derived, capital


def test_finalizer_preserves_data_insufficient_without_promoting_it(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module(FINALIZE, "v4c03_finalize_test")
    inputs, derived, capital = _minimal_finalizer_inputs(tmp_path)
    out = tmp_path / "final"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(FINALIZE),
            "--inputs-dir",
            str(inputs),
            "--derived-dir",
            str(derived),
            "--capital-dir",
            str(capital),
            "--source-commit",
            "abc123",
            "--out-dir",
            str(out),
        ],
    )
    assert module.main() == 0
    receipt = json.loads((out / "receipt.json").read_text())
    assert receipt["status"] == "V4C03_PHASE_A_PUBLIC_EVIDENCE_MATERIALIZED_OUTCOME_BLIND"
    assert receipt["derived_readiness"]["source_states"]["example"] == "DATA_INSUFFICIENT"
    assert receipt["capital_readiness"]["turnover_complete_days"] == 1
    assert receipt["pre_sample_evidence_sample_eligible"] is False
    assert receipt["evidence_qualification_semantics_changed"] is False
    assert receipt["new_context_outcome_read"] is False


def test_market_routing_is_strict_for_frozen_v4c03_scope() -> None:
    module = _module(PREPARE, "v4c03_prepare_market_test")
    assert module._market_from_symbol("688001") == "SH"
    assert module._market_from_symbol("689009") == "SH"
    assert module._market_from_symbol("300001") == "SZ"
    assert module._market_from_symbol("301001") == "SZ"
    assert module._market_from_symbol("302001") == "SZ"
    import pytest
    with pytest.raises(ValueError, match="unsupported frozen V4C-03 symbol"):
        module._market_from_symbol("600000")


def test_recovery_workflow_reuses_exact_artifacts_without_provider_rerun() -> None:
    workflow = ROOT / ".github/workflows/v4c03-04a-phase-a-pit-evidence-recovery.yml"
    recovery = ROOT / "reference/v4c03_phase_a_pit_evidence_recovery_v1.json"
    text = workflow.read_text(encoding="utf-8")
    contract = json.loads(recovery.read_text(encoding="utf-8"))
    assert contract["status"] == "FROZEN_EXACT_REUSE_RECOVERY"
    assert contract["failed_run"]["run_id"] == 35497119739
    assert len(contract["exact_reused_artifacts"]) == 16
    assert "workflow_dispatch:" in text
    for forbidden_trigger in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert forbidden_trigger not in text
    for forbidden_producer in (
        "materialize_pit_evidence.py",
        "materialize_v4a_fundamental_earnings_shard.py",
        "materialize_v4a_price_shard.py",
        "materialize_v4a_policy_stage.py",
        "materialize_v4c03_phase_a_capital.py",
    ):
        assert forbidden_producer not in text
    assert "aggregate_v4a_issuer_shards.py" in text
    assert "assemble_v4a_derived_pit.py" in text
    assert "--issuer-source-commit" in text
    assert "--fundamental-source-commit" in text
    assert "--price-source-commit" in text
    assert "--policy-source-commit" in text
