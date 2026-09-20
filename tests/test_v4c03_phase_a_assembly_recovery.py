from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from scripts.prepare_v4c03_phase_a_evidence_inputs import _market_for_symbol


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "reference/v4c03_phase_a_assembly_recovery_v1.json"
WORKFLOW = ROOT / ".github/workflows/v4c03-04a-phase-a-assembly-recovery.yml"
PACKAGER = ROOT / "scripts/package_v4c03_phase_a_evidence_bundle.py"


def test_recovery_contract_pins_every_successful_work_unit() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert contract["status"] == "FROZEN_EXACT_SUCCESSFUL_WORK_UNITS"
    assert contract["source_run"]["run_id"] == 35497119739
    assert contract["source_run"]["head_sha"] == (
        "1b177f5cc49ac7dab18e02bd51b89a62c989c89a"
    )
    artifacts = contract["successful_artifacts"]
    assert len(artifacts) == 17
    assert len({row["id"] for row in artifacts}) == 17
    assert len({row["name"] for row in artifacts}) == 17
    assert all(str(row["digest"]).startswith("sha256:") for row in artifacts)
    names = {row["name"] for row in artifacts}
    assert "v4c03-phase-a-evidence-inputs-35497119739" in names
    assert {
        f"v4c03-phase-a-fundamental-{shard}-35497119739"
        for shard in range(4)
    }.issubset(names)
    assert {
        f"v4c03-phase-a-prices-{shard}-35497119739"
        for shard in range(4)
    }.issubset(names)
    assert contract["provider_refetch"] is False
    assert contract["successful_work_units_rerun"] is False
    assert contract["new_context_outcome_read"] is False
    assert contract["research_run"] is False
    assert contract["evidence_qualification_semantics_changed"] is False
    assert contract["production_or_trading_authority_changed"] is False


def test_recovery_workflow_is_manual_only_and_never_calls_data_producers() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: v4c03-04a-phase-a-assembly-recovery\n")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for forbidden in ("schedule:", "workflow_run:", "pull_request:", "push:"):
        assert forbidden not in text

    assert "35497119739" in text
    assert "Verify exact successful work-unit artifact identities" in text
    assert "Download exact successful artifacts only" in text
    assert "Mechanically repair frozen scope market column" in text
    assert "assemble_v4a_derived_pit.py" in text
    assert "finalize_v4c03_phase_a_evidence.py" in text
    assert "package_v4c03_phase_a_evidence_bundle.py" in text

    forbidden_producers = (
        "materialize_pit_evidence.py",
        "materialize_v4a_fundamental_earnings_shard.py",
        "materialize_v4a_price_shard.py",
        "materialize_v4a_policy_stage.py",
        "materialize_v4c03_phase_a_capital.py",
    )
    for producer in forbidden_producers:
        assert producer not in text


def test_frozen_scope_market_mapping_is_strict() -> None:
    assert _market_for_symbol("688001") == "SH"
    assert _market_for_symbol("689009") == "SH"
    assert _market_for_symbol("300001") == "SZ"
    assert _market_for_symbol("301001") == "SZ"
    assert _market_for_symbol("302001") == "SZ"
    with pytest.raises(ValueError, match="unsupported V4C-03 frozen-scope symbol market"):
        _market_for_symbol("600000")
    with pytest.raises(ValueError, match="unsupported V4C-03 frozen-scope symbol market"):
        _market_for_symbol("000001")


def test_packager_binds_recovery_workflow_and_contract() -> None:
    text = PACKAGER.read_text(encoding="utf-8")
    assert ".github/workflows/v4c03-04a-phase-a-assembly-recovery.yml" in text
    assert "reference/v4c03_phase_a_assembly_recovery_v1.json" in text
