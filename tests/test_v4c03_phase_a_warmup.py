from __future__ import annotations

import json
from pathlib import Path
import re

import pandas as pd

from tech_sentiment.index_history import (
    read_adjustments_csv,
    read_anchor_csv,
    reconstruct_index_history,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "reference/v4c03_phase_a_warmup_public_universe_v1.json"
WORKFLOW = ROOT / ".github/workflows/v4c03-02-phase-a-warmup.yml"


def _reconstruct(universe: str) -> pd.DataFrame:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    cfg = contract["universes"][universe]
    anchor = read_anchor_csv(ROOT / cfg["anchor_path"])
    adjustments = read_adjustments_csv(ROOT / cfg["adjustments_path"])
    membership, _, diagnostics = reconstruct_index_history(
        anchor,
        adjustments,
        history_start=contract["window"]["start_date"],
        history_end=cfg["anchor_effective_date"],
        anchor_effective_date=cfg["anchor_effective_date"],
        expected_constituents=cfg["expected_constituents"],
        index_code=cfg["index_code"],
    )
    assert diagnostics.min_segment_constituents == 50
    assert diagnostics.max_segment_constituents == 50
    return membership


def _active(membership: pd.DataFrame, date: str) -> set[str]:
    target = pd.Timestamp(date)
    start = pd.to_datetime(membership["effective_start"])
    end = pd.to_datetime(membership["effective_end"])
    return set(
        membership.loc[start.le(target) & end.ge(target), "symbol"]
        .astype(str)
        .str.zfill(6)
    )


def test_warmup_contract_is_non_sample_public_only() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["status"] == "FROZEN_PUBLIC_DATA_SCOPE"
    assert contract["phase"] == "A_WARMUP_ONLY"
    assert contract["window"] == {
        "start_date": "2021-01-04",
        "end_date": "2021-06-14",
    }
    assert contract["sample_eligibility"] is False
    assert contract["sample_window_begins"] == "2021-06-15"
    assert contract["private_model_semantics_allowed"] is False
    assert contract["portfolio_or_holdings_data_allowed"] is False
    assert contract["forward_result_computation_allowed"] is False
    assert contract["automatic_trigger_allowed"] is False
    assert contract["production_or_trading_authority_changed"] is False


def test_star50_warmup_chain_reconstructs_exact_50_through_march_adjustment() -> None:
    membership = _reconstruct("STAR50")
    jan = _active(membership, "2021-01-04")
    before = _active(membership, "2021-03-12")
    after = _active(membership, "2021-03-15")
    end = _active(membership, "2021-06-14")
    assert len(jan) == len(before) == len(after) == len(end) == 50
    assert "688003" in before and "688180" not in before
    assert "688003" not in after and "688180" in after
    assert "688199" in before and "688567" not in before
    assert "688199" not in after and "688567" in after


def test_chinext50_warmup_chain_reconstructs_exact_50_until_phase_a_boundary() -> None:
    membership = _reconstruct("ChiNext50")
    jan = _active(membership, "2021-01-04")
    end = _active(membership, "2021-06-14")
    phase_a = _active(membership, "2021-06-15")
    assert len(jan) == len(end) == len(phase_a) == 50
    assert end != phase_a
    assert "300024" in end and "300001" not in end
    assert "300024" not in phase_a and "300001" in phase_a


def test_warmup_workflow_is_manual_only_failure_isolated_and_non_sample() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: v4c03-02-phase-a-warmup\n")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for forbidden in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert forbidden not in text
    assert "universe: [STAR50, ChiNext50]" in text
    assert "fail-fast: false" in text
    assert '--sample-eligibility "false"' in text
    assert '"sample_eligibility"] is False' in text
    assert "v4c03-public-data-v1" in text
    assert "research_cli" not in text
    assert "study_ice_points" not in text
    assert "study_post_warning_outcomes" not in text


def test_warmup_does_not_modify_phase_a_sample_adjustment_file() -> None:
    phase_a = (
        ROOT / "data/reference/v4c03_kc50_adjustments_2021h2_2026.csv"
    ).read_text(encoding="utf-8")
    assert "2021-03-15" not in phase_a
    warmup = (
        ROOT / "data/reference/v4c03_kc50_adjustments_2021warmup_2026.csv"
    ).read_text(encoding="utf-8")
    assert "2021-03-15" in warmup
    assert "2021-06-15" in warmup
