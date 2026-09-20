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
CONTRACT = ROOT / "reference/v4c03_phase_a_public_universe_v1.json"
WORKFLOW = ROOT / ".github/workflows/v4c03-01-phase-a-public-universe.yml"


def _active(membership: pd.DataFrame, date: str) -> set[str]:
    target = pd.Timestamp(date)
    start = pd.to_datetime(membership["effective_start"])
    end = pd.to_datetime(membership["effective_end"])
    return set(
        membership.loc[start.le(target) & end.ge(target), "symbol"]
        .astype(str)
        .str.zfill(6)
    )


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


def test_phase_a_public_contract_is_fixed_and_public_only() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["status"] == "FROZEN_PUBLIC_DATA_SCOPE"
    assert contract["window"] == {
        "start_date": "2021-06-15",
        "end_date": "2021-12-31",
    }
    assert set(contract["universes"]) == {"STAR50", "ChiNext50"}
    assert contract["private_model_semantics_allowed"] is False
    assert contract["portfolio_or_holdings_data_allowed"] is False
    assert contract["forward_result_computation_allowed"] is False
    assert contract["automatic_trigger_allowed"] is False
    assert contract["production_or_trading_authority_changed"] is False


def test_star50_official_chain_reconstructs_exact_50_and_2021_transitions() -> None:
    membership = _reconstruct("STAR50")

    june = _active(membership, "2021-06-15")
    assert len(june) == 50
    assert "688065" in june and "688015" not in june
    assert "689009" in june and "688299" not in june

    before_sep = _active(membership, "2021-09-10")
    on_sep = _active(membership, "2021-09-13")
    assert len(before_sep) == len(on_sep) == 50
    assert "688020" in before_sep and "688063" not in before_sep
    assert "688020" not in on_sep and "688063" in on_sep

    before_dec = _active(membership, "2021-12-10")
    on_dec = _active(membership, "2021-12-13")
    assert len(before_dec) == len(on_dec) == 50
    assert "688007" in before_dec and "688083" not in before_dec
    assert "688007" not in on_dec and "688083" in on_dec


def test_chinext50_official_chain_reconstructs_exact_50_and_2021_transitions() -> None:
    membership = _reconstruct("ChiNext50")

    june = _active(membership, "2021-06-15")
    assert len(june) == 50
    assert "300001" in june and "300024" not in june
    assert "300896" in june and "300666" not in june

    before_dec = _active(membership, "2021-12-10")
    on_dec = _active(membership, "2021-12-13")
    assert len(before_dec) == len(on_dec) == 50
    assert "300017" in before_dec and "300373" not in before_dec
    assert "300017" not in on_dec and "300373" in on_dec


def test_v4c03_public_workflow_is_manual_only_and_failure_isolated() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: v4c03-01-phase-a-public-universe\n")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for forbidden in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert forbidden not in text
    assert "matrix:" in text
    assert "universe: [STAR50, ChiNext50]" in text
    assert "fail-fast: false" in text
    assert "Upload reusable universe work unit" in text
    assert "v4c03-public-data-v1" in text
    assert "ret_" not in text
    assert "fwd_" not in text


def test_v4c03_scope_does_not_mutate_frozen_v4a_symbol_scope() -> None:
    from scripts.build_capital_pit_symbol_scope import DEFAULT_INPUTS

    paths = {relative for _, relative, _ in DEFAULT_INPUTS}
    assert "data/reference/v4c03_kc50_adjustments_2021h2_2026.csv" not in paths
    assert "data/reference/v4c03_chinext50_adjustments_2021h2_2026.csv" not in paths
