from pathlib import Path

from tech_sentiment.official_filing_extended_pit import EXTENDED_FILING_PARSER_VERSION

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/fundamental-v9-reconciled-zero-target.yml"
SCRIPT = ROOT / "scripts/verify_fundamental_v9_reconciled_zero_688001.py"

EXPECTED_PARSER = (
    "official-filing-extended-pit-primitives-v9-subtotal-reconciled-zero-safe"
)
EXPECTED_URL = (
    "https://static.cninfo.com.cn/finalpage/2025-04-30/1223423562.PDF"
)
EXPECTED_SHA256 = (
    "5389cb110666f96a8d75968c2b11d07a851e1c671eaa623e7da4128dc1a3fa43"
)


def test_target_verification_workflow_is_manual_only_and_fixed_scope() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "\n  schedule:" not in text
    assert "\n  push:" not in text
    assert "\n  pull_request:" not in text
    assert "\n  workflow_run:" not in text
    assert "verify_fundamental_v9_reconciled_zero_688001.py" in text
    assert "fundamental-v9-reconciled-zero-688001-2025q1" in text


def test_target_verification_script_pins_exact_document_and_v9_identity() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert EXTENDED_FILING_PARSER_VERSION == EXPECTED_PARSER
    assert EXPECTED_PARSER in text
    assert EXPECTED_URL in text
    assert EXPECTED_SHA256 in text
    assert 'TARGET_ENTITY = "688001.SH"' in text
    assert 'TARGET_PERIOD = "2025-03-31"' in text
    assert '"LONG_TERM_BORROWINGS": 0.0' in text
    assert '"blank_or_dash_directly_mapped_to_zero": False' in text
    assert '"outcome_read": False' in text
    assert '"public_artifact_automatically_grants_private_qualification": False' in text
