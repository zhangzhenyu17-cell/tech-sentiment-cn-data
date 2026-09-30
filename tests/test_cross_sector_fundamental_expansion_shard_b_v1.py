from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "data/reference/cross_sector_fundamental_expansion_shard_b_scope.csv"
CONTRACT = ROOT / "reference/cross_sector_fundamental_expansion_shard_b_v1.json"
WORKFLOW = ROOT / ".github/workflows/cross-sector-fundamental-expansion-shard-b-v1.yml"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _truth(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true")


def test_shard_b_scope_reconstructs_exact_frozen_missing_universe() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    scope = pd.read_csv(SCOPE, dtype={"symbol": str})
    assert contract["shard_id"] == "B"
    assert contract["shard_definition"] == "sorted(M)[1::2]"
    assert len(scope) == 218
    assert _sha(SCOPE) == contract["scope_csv_sha256"]
    assert set(scope["shard_id"]) == {"B"}
    assert set(scope["scope_decision_date"]) == {"2026-09-30"}

    membership = {}
    for domain, item in contract["official_membership_witnesses"].items():
        path = ROOT / item["path"]
        assert _sha(path) == item["sha256"]
        frame = pd.read_csv(path, dtype={"symbol": str})
        assert len(frame) == item["rows"]
        assert set(frame["snapshot_source"]) == {"csindex_cons_xls"}
        assert set(frame["snapshot_role"]) == {"official_live_witness"}
        assert _truth(frame["point_in_time"]).all()
        membership[domain] = frame

    all_sets = {
        domain: set(frame["entity_id"].astype(str))
        for domain, frame in membership.items()
    }
    baseline = contract["frozen_v4a_fundamental_input"]
    baseline_path = ROOT / baseline["entity_identity_path"]
    assert _sha(baseline_path) == baseline["entity_identity_sha256"]
    baseline_entities = set(
        pd.read_csv(baseline_path, dtype=str)["entity_id"].astype(str)
    )
    assert len(baseline_entities) == baseline["unique_entity_ids"] == 192

    union = set().union(*all_sets.values())
    missing_sets = {
        domain: entities - baseline_entities
        for domain, entities in all_sets.items()
    }
    missing = sorted(union - baseline_entities)
    assert len(union) == 552
    assert len(missing) == 437
    assert len(missing_sets["INNOVATION_DRUG"]) == 37
    assert len(missing_sets["DEFENSE"]) == 42
    assert len(missing_sets["CORE_BETA"]) == 391
    assert len(all_sets["CORE_BETA"] & all_sets["INNOVATION_DRUG"]) == 26
    assert len(all_sets["CORE_BETA"] & all_sets["DEFENSE"]) == 22
    assert len(all_sets["INNOVATION_DRUG"] & all_sets["DEFENSE"]) == 0

    expected = missing[1::2]
    assert len(expected) == 218
    assert scope["entity_id"].astype(str).tolist() == expected
    assert int(_truth(scope["in_innovation_drug"]).sum()) == 13
    assert int(_truth(scope["in_defense"]).sum()) == 25
    assert int(_truth(scope["in_core_beta"]).sum()) == 197
    assert contract["counts"]["shard_b_innovation_drug_entities"] == 13
    assert contract["counts"]["shard_b_defense_entities"] == 25
    assert contract["counts"]["shard_b_core_beta_entities"] == 197


def test_shard_b_scope_has_required_domain_routing_columns() -> None:
    scope = pd.read_csv(SCOPE, dtype={"symbol": str})
    required = {
        "entity_id",
        "symbol",
        "exchange",
        "in_innovation_drug",
        "in_defense",
        "in_core_beta",
        "shard_id",
        "scope_decision_date",
        "source_membership_identity",
    }
    assert required.issubset(scope.columns)
    expected_entity = scope["symbol"].str.zfill(6) + "." + scope["exchange"]
    assert expected_entity.tolist() == scope["entity_id"].tolist()
    assert scope["entity_id"].is_unique


def test_shard_b_contract_preserves_v4a_identity_and_authority_boundary() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    v4a = contract["frozen_v4a_fundamental_input"]
    assert v4a["run_id"] == 35485765220
    assert v4a["artifact_id"] == 10596634296
    assert v4a["artifact_name"] == "capital-pit-input-materialization"
    assert v4a["artifact_sha256"] == "0faffd88a611beee8412de8b5f4f23f5d0cfd0e3068f7c311206b4829fc7faac"
    assert v4a["member_path"] == "pit_evidence_materialization/fundamental_state_evidence.csv"
    assert v4a["member_sha256"] == "b56e6a1e8bdf32890abe8234dfc7ea989780c8103eebe48d2e5cdec2a1a758d0"
    assert v4a["unique_entity_ids"] == 192
    assert _sha(ROOT / v4a["entity_identity_path"]) == v4a["entity_identity_sha256"]
    assert contract["materialization"]["decision_end_date"] == "2026-09-29"
    assert contract["frozen_implementation_versions"] == {
        "filing_materializer_version": "cninfo-versioned-filing-materializer-v2-publication-order",
        "filing_parser_version": "official-filing-facts-v11-balance-sheet-parent-equity",
        "fundamental_state_contract_id": "FUNDAMENTAL_PIT_STATE_CONTRACT_V1",
        "fundamental_state_formula_version": "fundamental-pit-state-v1",
    }
    for key, value in contract["authority"].items():
        assert value is False, key


def test_shard_b_workflow_is_manual_only_and_bounded() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for trigger in ("schedule:", "workflow_run:", "pull_request:", "push:", "repository_dispatch:"):
        assert trigger not in text
    assert 'WORK_UNITS: "32"' in text
    assert "max-parallel: 4" in text
    assert 'DECISION_DATE: "2026-09-30"' in text
    assert "cross_sector_fundamental_expansion_shard_b_scope.csv" in text
    assert "--scope-contract reference/cross_sector_fundamental_expansion_shard_b_v1.json" in text
    assert "materialize_cross_sector_fundamental_expansion_shard_b_v1.py" in text
    assert "aggregate_cross_sector_fundamental_expansion_shard_b_v1.py" in text
    assert "tar --sort=name --mtime='UTC 1970-01-01'" in text
    assert "bundle_manifest.json.sha256" in text
    assert 'ARCHIVE="cross-sector-fundamental-expansion-shard-b-v1.tar.gz"' in text
    assert '"$ARCHIVE.sha256"' in text
