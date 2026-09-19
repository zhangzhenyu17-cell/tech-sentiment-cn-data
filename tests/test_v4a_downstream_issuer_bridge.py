from pathlib import Path

from tech_sentiment.v4a_persistent_stage import STAGE_SPECS


WORKFLOW_DIR = Path(".github/workflows")
BRIDGE = "reference/v4a_issuer_source_aggregate_compatibility_bridge_v1.json"


def _text(name: str) -> str:
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def _issuer_block(text: str) -> str:
    start = text.index("frozen issuer")
    end = text.index("      - name: Download and verify issuer aggregate", start)
    return text[start:end]


def test_derived_identity_binds_frozen_issuer_bridge() -> None:
    assert BRIDGE in STAGE_SPECS["derived"].extra_files


def test_derived_and_finalizer_resolve_issuer_sources_from_exact_bridge_not_current_key() -> None:
    for name in ("v4a-derived.yml", "qualify-capital-inputs.yml"):
        block = _issuer_block(_text(name))
        assert BRIDGE in block
        assert "issuer-compat-plan.json" in block
        assert "provider_refetch" in block
        assert "producer_fingerprint(\".\", family)" in block
        assert "file_sha256(archive_path)" in block
        assert "archive byte-size mismatch" in block
        assert 'manifest.get("input_bundles") != {"shared": shared_identity}' in block
        assert "all_non_allowlisted_producer_files_must_match_source_manifest_sha256" in block
        assert "allowlisted_drift_requires_exact_old_and_current_sha256" in block
        assert "provider_refetch_allowed" in block
        assert "formal_evidence_handoff" in block
        assert "pit_no_lookahead_semantics_changed" in block
        assert "model_thresholds_changed" in block
        assert "production_authority_changed" in block
        assert "trading_authority_changed" in block
        assert 'v4a_persistent_stage_bundle.py key --family "$family"' not in block
        assert "materialize_pit_evidence.py" not in block
        assert "materialize_v4a_szse_issuer.py" not in block


def test_aggregate_bridge_remains_provider_refetch_free() -> None:
    text = _text("v4a-issuer-aggregate.yml")
    assert BRIDGE in text
    assert '"provider_refetch": False' in text
    assert "materialize_pit_evidence.py" not in text
    assert "materialize_v4a_szse_issuer.py" not in text
