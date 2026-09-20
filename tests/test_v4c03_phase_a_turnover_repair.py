from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re

import pandas as pd
import pytest

from tech_sentiment.capital_input_data import ExchangeTurnoverFetchResult


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "reference/v4c03_phase_a_turnover_repair_v1.json"
WORKFLOW = ROOT / ".github/workflows/v4c03-04b-phase-a-turnover-repair.yml"
SCRIPT = ROOT / "scripts/repair_v4c03_phase_a_turnover.py"


def _module():
    spec = importlib.util.spec_from_file_location("v4c03_turnover_repair_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_turnover_repair_contract_is_exact_manual_and_narrow() -> None:
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert c["status"] == "FROZEN_EXACT_PUBLIC_EVIDENCE_INPUT"
    assert c["base_public_evidence"]["source_run_id"] == 35504618971
    assert c["base_public_evidence"]["bundle_identity"] == (
        "6ef568d8c84abd98214eaafab028a62da2fceda46b595461e58f06089b05342d"
    )
    assert c["repair_window"]["expected_trading_days"] == 243
    assert c["success_gate"]["turnover_complete_days"] == 243
    assert c["success_gate"]["turnover_error_rows"] == 0
    assert c["workflow_semantics"]["manual_only"] is True
    assert c["workflow_semantics"]["provider_refetch_scope"] == "TURNOVER_ONLY"
    assert c["workflow_semantics"]["issuer_rerun"] is False
    assert c["workflow_semantics"]["fundamental_rerun"] is False
    assert c["workflow_semantics"]["price_rerun"] is False
    assert c["workflow_semantics"]["policy_rerun"] is False
    assert c["workflow_semantics"]["etf_share_rerun"] is False
    assert c["new_context_outcome_read"] is False
    assert c["research_run"] is False
    assert c["evidence_qualification_semantics_changed"] is False
    assert c["production_or_trading_authority_changed"] is False


def test_turnover_repair_workflow_is_manual_only_and_does_not_rerun_other_producers() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: v4c03-04b-phase-a-turnover-repair\n")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for trigger in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert trigger not in text
    assert "repair_v4c03_phase_a_turnover.py" in text
    assert "turnover_complete_days" in text
    for producer in (
        "materialize_pit_evidence.py",
        "materialize_v4a_fundamental_earnings_shard.py",
        "materialize_v4a_price_shard.py",
        "materialize_v4a_policy_stage.py",
        "materialize_v4c03_phase_a_capital.py",
    ):
        assert producer not in text


def _build_fake_base(tmp_path: Path):
    root = tmp_path / "base"
    capital = root / "capital_input_qualification"
    capital.mkdir(parents=True)

    receipt = {
        "status": "V4C03_PHASE_A_PUBLIC_EVIDENCE_MATERIALIZED_OUTCOME_BLIND",
        "source_commit": "c2852749b8fcdcaab6ac3b76ead89d6f5a1baad1",
        "new_context_outcome_read": False,
        "new_context_forward_outcomes_read": False,
        "outcome_columns_materialized": False,
        "real_outcome_study_executed": False,
        "parameter_search_run": False,
        "feature_search_run": False,
        "threshold_search_run": False,
        "ml_run": False,
        "context_or_cause_semantics_changed": False,
        "universe_changed": False,
        "evidence_qualification_semantics_changed": False,
        "score_mapping_changed": False,
        "production_authority_changed": False,
        "trading_authority_changed": False,
    }
    (root / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    dates = pd.to_datetime(
        ["2021-01-04"]
        + list(pd.date_range("2021-01-05", "2021-12-30", periods=241).strftime("%Y-%m-%d"))
        + ["2021-12-31"]
    )
    pd.DataFrame({"date": dates}).to_csv(root / "trading_calendar.csv", index=False)

    frames = {
        "etf_shares.csv": pd.DataFrame(
            {"date": ["2021-01-04"], "fund_code": ["588000"], "fund_shares": [1.0]}
        ),
        "etf_share_coverage.csv": pd.DataFrame(
            {"date": ["2021-01-04"], "fund_code": ["588000"], "eligible": [False]}
        ),
        "sse_etf_share_errors.csv": pd.DataFrame(columns=["date", "error"]),
        "szse_etf_share_errors.csv": pd.DataFrame(columns=["chunk_start", "chunk_end", "error"]),
    }
    for name, frame in frames.items():
        frame.to_csv(capital / name, index=False)

    (capital / "capital_summary.json").write_text(
        json.dumps(
            {
                "status": "V4C03_PUBLIC_CAPITAL_MATERIALIZED",
                "start_date": "2021-01-04",
                "end_date": "2021-12-31",
                "turnover_complete_days": 5,
                "turnover_expected_days": 243,
                "turnover_error_rows": 246,
                "future_prices_or_returns_used": False,
                "predictive_research_run": False,
                "parameter_search_run": False,
                "production_or_trading_authority_changed": False,
            }
        ),
        encoding="utf-8",
    )
    return root, pd.DatetimeIndex(dates).normalize().sort_values().unique()


def test_turnover_repair_requires_exact_calendar_and_preserves_etf_bytes(tmp_path: Path) -> None:
    module = _module()
    base, dates = _build_fake_base(tmp_path)
    capital = base / "capital_input_qualification"
    module.PINNED_UNCHANGED = {
        name: _sha(capital / name)
        for name in (
            "etf_shares.csv",
            "etf_share_coverage.csv",
            "sse_etf_share_errors.csv",
            "szse_etf_share_errors.csv",
        )
    }

    def fake_fetcher(**kwargs):
        requested = pd.DatetimeIndex(kwargs["trading_dates"]).normalize()
        assert requested.equals(dates)
        sse = pd.DataFrame(
            {"date": dates, "sse_a_share_turnover_yuan": [100.0] * len(dates)}
        )
        szse = pd.DataFrame(
            {"date": dates, "szse_a_share_turnover_yuan": [200.0] * len(dates)}
        )
        combined = pd.DataFrame(
            {
                "date": dates,
                "sse_a_share_turnover_yuan": [100.0] * len(dates),
                "szse_a_share_turnover_yuan": [200.0] * len(dates),
                "amount": [300.0] * len(dates),
                "scope": ["SSE_SZSE_A_SHARES"] * len(dates),
                "canonical_all_a_state": ["INCOMPLETE_BSE_NOT_INCLUDED"] * len(dates),
            }
        )
        return ExchangeTurnoverFetchResult(
            sse=sse,
            szse=szse,
            combined=combined,
            errors=pd.DataFrame(columns=["date", "exchange", "error"]),
        )

    before = {name: (capital / name).read_bytes() for name in module.PINNED_UNCHANGED}
    out = tmp_path / "out"
    receipt = module.repair_turnover(
        base_evidence_dir=base,
        out_dir=out,
        source_commit="new-sha",
        fetcher=fake_fetcher,
    )
    assert receipt["turnover_complete_days"] == 243
    assert receipt["turnover_error_rows"] == 0
    for name, expected in before.items():
        assert (out / name).read_bytes() == expected
    summary = json.loads((out / "capital_summary.json").read_text())
    assert summary["turnover_complete_days"] == 243
    assert summary["turnover_expected_days"] == 243
    assert summary["turnover_error_rows"] == 0
    assert summary["turnover_repair"]["provider_refetch_scope"] == "TURNOVER_ONLY"


def test_turnover_repair_fails_closed_on_any_remaining_exchange_error(tmp_path: Path) -> None:
    module = _module()
    base, dates = _build_fake_base(tmp_path)
    capital = base / "capital_input_qualification"
    module.PINNED_UNCHANGED = {
        name: _sha(capital / name)
        for name in (
            "etf_shares.csv",
            "etf_share_coverage.csv",
            "sse_etf_share_errors.csv",
            "szse_etf_share_errors.csv",
        )
    }

    def bad_fetcher(**kwargs):
        return ExchangeTurnoverFetchResult(
            sse=pd.DataFrame(),
            szse=pd.DataFrame(),
            combined=pd.DataFrame(),
            errors=pd.DataFrame(
                [{"date": str(dates[0].date()), "exchange": "SSE", "error": "still missing"}]
            ),
        )

    with pytest.raises(ValueError, match="turnover repair remains incomplete"):
        module.repair_turnover(
            base_evidence_dir=base,
            out_dir=tmp_path / "out",
            source_commit="new-sha",
            fetcher=bad_fetcher,
        )
