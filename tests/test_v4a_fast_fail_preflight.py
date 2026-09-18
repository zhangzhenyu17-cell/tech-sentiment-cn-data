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
