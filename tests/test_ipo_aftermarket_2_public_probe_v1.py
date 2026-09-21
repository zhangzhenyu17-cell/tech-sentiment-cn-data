from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from tech_sentiment.ipo_aftermarket_2_public_probe_v1 import (
    deterministic_filing_probe_sample,
    deterministic_symbol_probe_sample,
    normalize_industry_change_probe,
    normalize_industry_pe_probe,
    public_probe_boundary_check,
    select_probe_trade_dates,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "reference" / "ipo_aftermarket_2_public_source_probe_v1.json"
WORKFLOW = ROOT / ".github" / "workflows" / "ipo-aftermarket-2-public-source-probe-v1.yml"


def test_industry_change_probe_preserves_csrc_identity_without_guessing_code() -> None:
    raw = pd.DataFrame(
        {
            "分类标准": ["证监会行业分类标准", "国证行业分类标准"],
            "分类标准编码": ["008001", "008004"],
            "行业编码": ["C35", "123456"],
            "变更日期": ["2020-01-02", "2020-01-02"],
        }
    )
    out = normalize_industry_change_probe(raw, symbol="601091")
    assert out["required_columns_present"] is True
    assert out["row_count"] == 2
    assert out["csrc_row_count"] == 1
    assert out["industry_codes"] == ["C35"]
    assert "008001" in out["classification_standard_codes"]


def test_industry_pe_probe_requires_exact_date_and_positive_static_median() -> None:
    raw = pd.DataFrame(
        {
            "变动日期": ["2024-06-17", "2024-06-17", "2024-06-14"],
            "行业层级": [1, 2, 1],
            "行业编码": ["C", "C35", "C"],
            "静态市盈率-中位数": [20.0, -1.0, 18.0],
        }
    )
    out = normalize_industry_pe_probe(
        raw,
        requested_date=pd.Timestamp("2024-06-17"),
    )
    assert out["required_columns_present"] is True
    assert out["exact_date_row_count"] == 2
    assert out["positive_static_median_count"] == 1
    assert out["industry_code_count"] == 2


def test_probe_samples_are_deterministic_public_metadata_only() -> None:
    meta = pd.DataFrame(
        [
            ["600001", "SSE_MAIN", "2019-01-02"],
            ["600002", "SSE_MAIN", "2026-01-02"],
            ["000001", "SZSE_MAIN", "2019-02-01"],
            ["000002", "SZSE_MAIN", "2026-02-01"],
            ["688001", "STAR", "2019-07-22"],
            ["688999", "STAR", "2026-03-01"],
            ["300001", "CHINEXT", "2019-01-10"],
            ["301999", "CHINEXT", "2026-04-01"],
        ],
        columns=["symbol", "board", "listing_date"],
    )
    assert deterministic_symbol_probe_sample(meta) == [
        "600001",
        "600002",
        "000001",
        "000002",
        "688001",
        "688999",
        "300001",
        "301999",
    ]
    assert deterministic_filing_probe_sample(meta) == [
        "600002",
        "000002",
        "688999",
        "301999",
    ]


def test_probe_trade_dates_never_look_forward() -> None:
    benchmark = pd.DataFrame(
        {
            "date": [
                "2019-01-03",
                "2022-01-04",
                "2024-01-03",
                "2026-01-05",
                "2026-09-18",
            ]
        }
    )
    dates = select_probe_trade_dates(benchmark, as_of="2026-09-21")
    assert [str(x.date()) for x in dates] == [
        "2019-01-03",
        "2022-01-04",
        "2024-01-03",
        "2026-01-05",
        "2026-09-18",
    ]


def test_public_probe_rejects_private_or_outcome_semantics() -> None:
    public_probe_boundary_check(
        {
            "status": "PUBLIC_SOURCE_PROBE_COMPLETE",
            "industry_pe_state": "DATA_INSUFFICIENT",
        }
    )
    for key in (
        "candidate_a_event_identities",
        "candidate_b_eligibility",
        "forward_return",
        "research_verdict",
        "portfolio_holding",
    ):
        try:
            public_probe_boundary_check({key: True})
        except ValueError:
            pass
        else:
            raise AssertionError(f"{key} must fail public boundary")


def test_probe_contract_is_public_only_and_does_not_compute_research_results() -> None:
    c = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert c["status"] == "PUBLIC_SOURCE_REACHABILITY_PROBE_ONLY"
    assert c["probe_policy"]["deterministic_public_samples_only"] is True
    assert c["probe_policy"]["private_event_identities_allowed"] is False
    assert c["probe_policy"]["research_eligibility_allowed"] is False
    assert c["probe_policy"]["forward_outcomes_allowed"] is False
    assert c["probe_policy"]["research_verdict_allowed"] is False
    assert c["probe_policy"]["parameter_or_threshold_search"] is False
    assert c["probe_policy"]["production_or_trading_authority"] is False


def test_probe_workflow_is_manual_only_and_exact_base_bundle() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: ipo-aftermarket-2-public-source-probe-v1\n")
    assert "\n  workflow_dispatch:" in text
    for forbidden in ("\n  schedule:", "\n  workflow_run:", "\n  pull_request:", "\n  push:"):
        assert forbidden not in text
    assert "permissions:\n  contents: read" in text
    assert "ipo-aftermarket-public-v1-20260921-dd10c3a4f8e3.tar.gz" in text
    assert "961e2f36ad25170323d3d8836fa5e1bf268f9a0057becedffa9a023da833308a" in text
    assert "probe_ipo_aftermarket_2_public_sources_v1.py" in text
    assert "actions/upload-artifact@v4" in text
    assert "gh release upload" not in text
    assert "contents: write" not in text
