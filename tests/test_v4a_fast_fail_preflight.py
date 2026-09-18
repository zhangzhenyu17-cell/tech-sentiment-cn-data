import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "relative",
    [
        "scripts/check_cninfo_connectivity.py",
        "scripts/check_csrc_policy_connectivity.py",
        "scripts/check_issuer_archive_connectivity.py",
        "scripts/check_v4a_source_freshness.py",
        "scripts/check_v4a_reachability.py",
    ],
)
def test_fast_fail_preflight_scripts_import_without_side_effects(relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(
        "v4a_preflight_" + path.stem,
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def test_issuer_preflight_covers_both_exchanges_and_exact_historical_disclosures():
    path = ROOT / "scripts" / "check_issuer_archive_connectivity.py"
    spec = importlib.util.spec_from_file_location("v4a_issuer_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    probes = list(module.PROBES)
    assert {probe["market"] for probe in probes} == {"SSE", "SZSE"}
    assert all(probe["expected_title_token"] for probe in probes)
    assert all(probe["start_date"] <= probe["end_date"] for probe in probes)


def test_cninfo_preflight_requires_two_annual_report_fact_probes():
    path = ROOT / "scripts" / "check_cninfo_connectivity.py"
    spec = importlib.util.spec_from_file_location("v4a_cninfo_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    probes = list(module.PROBES)
    assert {probe["symbol"] for probe in probes} == {"600519", "000538"}

    source = path.read_text(encoding="utf-8")
    assert "annual_report_fact_probes.append(parsed_probe)" in source
    assert "critical_filing_facts_verified" in source


def test_source_freshness_preflight_checks_window_boundaries_and_historical_prices():
    source = (ROOT / "scripts" / "check_v4a_source_freshness.py").read_text(
        encoding="utf-8"
    )
    assert "earliest = pd.Timestamp(calendar.min()).normalize()" in source
    assert "latest = pd.Timestamp(calendar.max()).normalize()" in source
    assert "boundary_dates = pd.DatetimeIndex([earliest, latest])" in source
    assert "materialize_financing_history(boundary_dates)" in source
    assert '"historical_price_symbol": historical_price_symbol' in source


def test_csrc_preflight_checks_second_page_identity_and_order():
    source = (ROOT / "scripts" / "check_csrc_policy_connectivity.py").read_text(
        encoding="utf-8"
    )
    assert "requested_page=2" in source
    assert "duplicate manuscript across pages" in source
    assert "cross-page order drift" in source
    assert "total drift across pages" in source


def test_cninfo_preflight_exercises_frozen_fundamental_and_valuation_paths():
    source = (ROOT / "scripts" / "check_cninfo_connectivity.py").read_text(
        encoding="utf-8"
    )
    assert 'set(REQUIRED_FACTS) | {"BASIC_EPS"}' in source
    assert '"600519_PRIOR"' in source
    assert "materialize_fundamental_state_evidence(" in source
    assert '"readiness_state") != "QUALIFIED_INPUT"' in source
    assert "latest_required_comparable_coverage_complete" in source
    assert "build_trailing_valuation_rail(" in source
    assert "valuation_rail_to_pit_evidence(" in source
    assert "real_input_integration_verified" in source
    assert "forward_outcome" not in source.lower()
