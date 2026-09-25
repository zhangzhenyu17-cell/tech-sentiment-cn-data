from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from tech_sentiment.innovation_drug_931152_preopen_v1 import (
    EXPECTED_CONSTITUENTS,
    FIRST_PROSPECTIVE_MARKET_SESSION,
    load_anchor_symbols,
    validate_live_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
ANCHOR = ROOT / "data/reference/innovation_drug_931152_live_anchor_2026-09-11.csv"
CONTRACT = ROOT / "reference/innovation_drug_931152_preopen_v1.json"
WORKFLOW = ROOT / ".github/workflows/innovation-drug-931152-preopen-v1.yml"


def _snapshot(symbols: tuple[str, ...], *, role: str = "official_live_witness") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": list(symbols),
            "source_index": "931152",
            "snapshot_source": "csindex_cons_xls",
            "snapshot_role": role,
            "board": "main",
            "universe_mode": "current_snapshot",
        }
    )


def test_live_anchor_is_exact_50_member_continuity_witness() -> None:
    symbols = load_anchor_symbols(ANCHOR)
    assert len(symbols) == EXPECTED_CONSTITUENTS == 50
    assert len(set(symbols)) == 50
    assert "600276" in symbols


def test_current_snapshot_requires_exact_official_anchor_match() -> None:
    symbols = load_anchor_symbols(ANCHOR)
    validated = validate_live_snapshot(
        _snapshot(symbols), anchor_symbols=symbols, market_session_date="2026-09-25"
    )
    assert validated["universe_mode"].eq("point_in_time").all()
    assert validated["effective_start"].astype(str).str.startswith("2026-09-25").all()

    changed = list(symbols)
    changed[-1] = "999999"
    with pytest.raises(ValueError, match="differs from frozen"):
        validate_live_snapshot(
            _snapshot(tuple(changed)), anchor_symbols=symbols, market_session_date="2026-09-25"
        )
    with pytest.raises(ValueError, match="official CSI"):
        validate_live_snapshot(
            _snapshot(symbols, role="independent_live_witness_only"),
            anchor_symbols=symbols,
            market_session_date="2026-09-25",
        )


def test_public_contract_is_raw_only_and_manual_only() -> None:
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert payload["first_prospective_market_session"] == FIRST_PROSPECTIVE_MARKET_SESSION
    firewall = payload["privacy_and_research_firewall"]
    assert firewall["private_model_semantics_allowed"] is False
    assert firewall["private_thresholds_or_signals_allowed"] is False
    assert firewall["forward_outcomes_allowed"] is False
    assert firewall["historical_backfill_allowed"] is False
    assert payload["workflow"] == {
        "workflow_dispatch_only": True,
        "automatic_trigger_allowed": False,
    }


def test_public_workflow_has_no_automatic_trigger() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "  workflow_dispatch:" in text
    assert "\n  schedule:" not in text
    assert "\n  push:" not in text
    assert "\n  pull_request:" not in text
    assert "\n  workflow_run:" not in text
    assert "build_innovation_drug_931152_preopen_v1.py" in text
    assert "contents: write" in text
