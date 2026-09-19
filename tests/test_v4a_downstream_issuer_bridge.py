from pathlib import Path
import json

from tech_sentiment.v4a_persistent_stage import STAGE_SPECS


WORKFLOW_DIR = Path(".github/workflows")
BRIDGE = "reference/v4a_issuer_source_aggregate_compatibility_bridge_v1.json"
VERIFIER = "scripts/verify_v4a_frozen_stage_compatibility.py"


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


def test_frozen_bridge_locks_downstream_stage_identities() -> None:
    contract = json.loads(Path(BRIDGE).read_text(encoding="utf-8"))
    stages = contract["downstream_stages"]
    assert {
        family: (
            payload["source_run_id"],
            payload["source_commit"],
            payload["asset_base"],
            payload["bundle_identity"],
            payload["compatibility_key"],
            payload["archive_sha256"],
            payload["stage_receipt_sha256"],
        )
        for family, payload in stages.items()
    } == {
        "capital": (
            35384429871,
            "29571066bbf69da5ba252982f71b592e07969314",
            "v4a-capital-20220104-20260917-6f8ed7d3811fa72ae9bd",
            "742ff961f72b72bdecfec4cbe0b517dc3a794669fd05917ee8940dfbb5a8f175",
            "6f8ed7d3811fa72ae9bde7db3de9a0e135c38e836893cdf68e0a33b22ea29f18",
            "44e03c32e73d5638fda7de77a579a24b47bfd5774b05cd26a8d8a93e45bfeb3c",
            "d6c5b693b84748fcf1e7252ca458647ba85d80c439a064b211382431faf09776",
        ),
        "financing": (
            35384448004,
            "29571066bbf69da5ba252982f71b592e07969314",
            "v4a-financing-20220104-20260917-50c5cefbed3574a68989",
            "4856c39a41bfb80c3ffe2e54802363731ef6fd98d9ecbbc4c242bccfe685a50e",
            "50c5cefbed3574a68989c4c3fd30463e6ba2f93ff505f0b0e9f769714eeafacf",
            "b9ddb91409a0e93a68e4248ed40baa1ac8f7e9db4bdd5c2860fa9e1f9772ed3b",
            "84a5765143271405857235434f8548dbf739fed86b65fb2be1caf22854e57710",
        ),
        "fundamental": (
            35451946515,
            "f55033f31e00bfed5bda798929dc5dccecbceac7",
            "v4a-fundamental-20220104-20260917-56e8c44ca678668bfc17",
            "f558db45af7a9e8b5e69dad1275f98e93e6ee49c09914fa591edd4348cfde4c4",
            "56e8c44ca678668bfc17fd7ef5da5a9c32503dcbac03eb86fa3963ecd0cf751b",
            "c1df2ec495657f52cf34935feb5ab94c7159f43e4cbf6f24bb8fbcf43ffdb54e",
            "2b27a25584e22521811ceebfdc873363d6a8fc43cf82d1b442d8ced121b91770",
        ),
        "policy": (
            35384604608,
            "29571066bbf69da5ba252982f71b592e07969314",
            "v4a-policy-20220104-20260917-fbd881d3d6b58def27a3",
            "094e67bfeb2052f2b3c8783f7cb2caccdff48f225c4cdfe1b1b5c1d59aa04550",
            "fbd881d3d6b58def27a3aee0628d94971eaa7ebd6f5a03da0924c0ed048109fb",
            "1c284766cd1edf134b134355d955068e89182e63c7a9e743246b8f77cc6b1eb8",
            "e45cd1f8ce23cfac9955ee07e844050a609525f70abd1b3ce8f00d2004745741",
        ),
    }
    drift = contract["allowed_producer_drift"]
    for key in (
        "capital_gate_semantics_changed",
        "financing_gate_semantics_changed",
        "policy_gate_semantics_changed",
    ):
        assert drift[key] is False
    fundamental_drift = contract["stage_allowed_producer_drifts"]["fundamental"]
    assert fundamental_drift == [
        {
            "path": "src/tech_sentiment/v4a_persistent_stage.py",
            "source_sha256": "8d14c01b31007ef68e2d3a8e5ea1e3b36256ddd4eefbcd13dab9045dae9acfa7",
            "source_bytes": 20123,
            "current_sha256": "d7b77125fcd85c67ef19aa9067acc9bbaf3ccba2a58390519fb71f0f2644cd02",
            "current_bytes": 20269,
            "classification": "DERIVED_ONLY_COMPATIBILITY_IDENTITY_BINDING",
            "fundamental_materialization_semantics_changed": False,
            "fundamental_qualification_semantics_changed": False,
            "pit_no_lookahead_semantics_changed": False,
        }
    ]


def test_derived_exact_reuses_frozen_fundamental_policy_and_binds_verifier_identity() -> None:
    text = _text("v4a-derived.yml")
    assert "Download and exact-verify frozen Fundamental bundle" in text
    assert "Download and verify Prices bundle" in text
    assert "Download and exact-verify frozen Policy bundle" in text
    assert "for family in fundamental prices; do" not in text
    assert "for family in fundamental prices policy; do" not in text
    assert VERIFIER in text
    assert BRIDGE in STAGE_SPECS["derived"].extra_files
    assert VERIFIER in STAGE_SPECS["derived"].extra_files


def test_finalizer_exact_reuses_frozen_capital_financing_fundamental_policy() -> None:
    text = _text("qualify-capital-inputs.yml")
    assert "Download and exact-verify frozen Capital and Financing bundles" in text
    assert "Download and exact-verify frozen Fundamental bundle" in text
    assert "Download and verify Prices bundle" in text
    assert "Download and exact-verify frozen Policy bundle" in text
    assert "for family in capital financing; do" in text
    assert "for family in fundamental prices; do" not in text
    assert "for family in fundamental prices policy; do" not in text
    assert VERIFIER in text
    assert "qualify_capital_inputs.py" not in text
    assert "materialize_financing_history.py" not in text
    assert "materialize_v4a_policy_stage.py" not in text


def test_frozen_stage_verifier_is_fail_closed_and_refetch_free() -> None:
    text = Path(VERIFIER).read_text(encoding="utf-8")
    for needle in (
        "exact_manifest_identity_required",
        "exact_archive_sha256_required",
        "exact_archive_bytes_required",
        "exact_stage_receipt_sha256_required",
        "exact_shared_input_identity_required",
        "current_producer_file_set_must_match_source_manifest",
        "all_non_allowlisted_producer_files_must_match_source_manifest_sha256",
        "allowlisted_drift_requires_exact_old_and_current_sha256",
        "producer_fingerprint",
        "verify_stage_receipt",
        "provider_refetch_allowed",
        "formal_evidence_handoff",
        "pit_no_lookahead_semantics_changed",
        "model_thresholds_changed",
        "production_authority_changed",
        "trading_authority_changed",
    ):
        assert needle in text
    for forbidden in (
        "materialize_financing_history.py",
        "qualify_capital_inputs.py",
        "materialize_v4a_policy_stage.py",
        "gh workflow run",
    ):
        assert forbidden not in text
