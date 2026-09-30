from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "reference/cross_sector_current_fundamental_scope_v1.json"
CONTRACT = ROOT / "reference/cross_sector_current_fundamental_materialization_contract_v1.json"
WORKFLOW = ROOT / ".github/workflows/cross-sector-current-fundamental-v1.yml"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_scope_is_exact_current_official_and_within_frozen_domains() -> None:
    payload = json.loads(SCOPE.read_text(encoding="utf-8"))
    assert payload["status"] == "FROZEN_PUBLIC_CURRENT_MEMBERSHIP_AND_MISSING_SCOPE_NO_QUALIFICATION"
    assert payload["scope_is_within_frozen_cross_sector_domains"] is True
    assert payload["new_domain_added"] is False
    assert payload["counts"] == {
        "priority_unique_missing": 79,
        "a500_remaining_missing_excluding_priority": 358,
        "total_unique_new_symbols": 437,
    }
    expected_rows = {"INNOVATION_DRUG": 50, "DEFENSE": 50, "CORE_BETA": 500}
    for domain, item in payload["membership_snapshots"].items():
        path = ROOT / item["path"]
        assert _sha(path) == item["sha256"]
        frame = pd.read_csv(path, dtype={"symbol": str})
        assert len(frame) == expected_rows[domain]
        assert set(frame["snapshot_source"]) == {"csindex_cons_xls"}
        assert set(frame["snapshot_role"]) == {"official_live_witness"}
        assert frame["point_in_time"].astype(bool).all()


def test_missing_scopes_are_disjoint_and_hash_locked() -> None:
    payload = json.loads(SCOPE.read_text(encoding="utf-8"))
    items = payload["materialization_scopes"]
    p = pd.read_csv(ROOT / items["priority"]["path"], dtype={"symbol": str})
    a = pd.read_csv(ROOT / items["a500_remaining"]["path"], dtype={"symbol": str})
    assert _sha(ROOT / items["priority"]["path"]) == items["priority"]["sha256"]
    assert _sha(ROOT / items["a500_remaining"]["path"]) == items["a500_remaining"]["sha256"]
    assert len(p) == 79
    assert len(a) == 358
    assert set(p["symbol"]).isdisjoint(set(a["symbol"]))
    assert len(set(p["symbol"]) | set(a["symbol"])) == 437


def test_materialization_contract_is_current_only_and_outcome_blind() -> None:
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert c["status"] == "FROZEN_OUTCOME_BLIND_CURRENT_FACT_INPUT_MATERIALIZATION"
    assert c["target_window"] == {
        "target_start_date": "2026-01-01",
        "end_date": "2026-09-30",
        "filing_query_warmup_years": 1,
        "semantic": "LATEST_COMPARABLE_ACCOUNTING_STATE_ONLY_NOT_FULL_HISTORICAL_RESEARCH",
    }
    assert c["inherited_semantics"]["cross_sector_aggregation_coverage_threshold"] == 0.8
    assert c["inherited_semantics"]["parameter_or_threshold_search_allowed"] is False
    for key in (
        "historical_forward_outcome_read",
        "prospective_forward_outcome_read",
        "full_historical_research",
        "clean_holdout_or_oos",
        "parameter_search",
        "threshold_search",
        "weight_search",
        "feature_or_subset_search",
        "ml",
        "new_domain",
        "new_cross_sector_benchmark",
        "evidence_qualification_change",
        "production_change",
        "trading_authority",
        "automatic_execution",
    ):
        assert c["authority"][key] is False


def test_workflow_is_explicitly_manual_and_provider_parallelism_is_bounded() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^  workflow_dispatch:\s*$", text)
    for trigger in ("schedule:", "workflow_run:", "pull_request:", "push:", "repository_dispatch:"):
        assert trigger not in text
    assert text.count("max-parallel: 4") == 2
    assert 'PRIORITY_SHARDS: "16"' in text
    assert 'A500_SHARDS: "48"' in text
    assert "needs: priority_aggregate" in text
    assert "materialize_cross_sector_current_fundamental_shard_v1.py" in text
    assert "aggregate_cross_sector_current_fundamental_v1.py" in text
    assert 'TARGET_START_DATE: "2026-01-01"' in text
    assert 'END_DATE: "2026-09-30"' in text
