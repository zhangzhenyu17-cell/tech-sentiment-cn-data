from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", "dist", "build"}
FORBIDDEN_PATH_PARTS = {"forward", "holdout", "portfolio", "shadow", "signal"}
FORBIDDEN_IMPORTS = re.compile(
    r"(?:from|import)\s+(?:tech_sentiment\.)?(?:features|buy_low_sell_high_policy|"
    r"confirmation_spectrum|crowding|exit_risk|top_divergence|v2_shadow)\b"
)
CREDENTIAL_MARKERS = ("ghp" + "_", "github" + "_pat_", "AKIA" + "[0-9A-Z]")
GOVERNANCE_CONTROL_PATH = ROOT / "reference" / "public_data_governance_control_plane_v1.json"
QUARANTINE_LEDGER_PATH = ROOT / "reference" / "public_data_quarantine_ledger_v1.json"


def _load_json(path: Path, failures: list[str]) -> dict[str, object] | None:
    if not path.exists():
        failures.append(f"missing governance file: {path.relative_to(ROOT)}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"invalid governance json {path.relative_to(ROOT)}: {exc}")
        return None
    if not isinstance(payload, dict):
        failures.append(f"governance json must be an object: {path.relative_to(ROOT)}")
        return None
    return payload


def _audit_governance_contracts(failures: list[str]) -> None:
    control = _load_json(GOVERNANCE_CONTROL_PATH, failures)
    ledger = _load_json(QUARANTINE_LEDGER_PATH, failures)
    if control is None or ledger is None:
        return

    if control.get("schema_version") != "public-data-governance-control-plane-v1":
        failures.append("unexpected public data governance control-plane schema")
    if control.get("status") != "ENGINEERING_GOVERNANCE_GUARD_NOT_EVIDENCE_AUTHORITY":
        failures.append("public data governance control plane changed authority status")

    authority = control.get("authority")
    if not isinstance(authority, dict):
        failures.append("public data governance control plane missing authority object")
    else:
        must_remain_false = (
            "may_promote_evidence",
            "may_change_evidence_eligibility",
            "may_change_pit_no_lookahead_semantics",
            "may_change_model_factor_threshold_signal",
            "may_change_production",
            "may_authorize_trading",
            "public_workflow_success_grants_private_qualification",
            "semantic_review_grants_private_qualification",
            "quarantine_release_grants_private_qualification",
        )
        for key in must_remain_false:
            if authority.get(key) is not False:
                failures.append(f"governance authority escalation or missing fail-closed flag: {key}")

    lifecycle = control.get("lifecycle")
    expected_gates = [
        "REPOSITORY_CONTRACT",
        "DATA_CONTRACT",
        "LINEAGE_PROVENANCE",
        "STRUCTURAL_QUALITY",
        "PIT_NO_LOOKAHEAD",
        "SEMANTIC_ARTIFACT_REVIEW",
        "QUARANTINE_CHECK",
        "PUBLIC_HANDOFF_READY",
    ]
    if not isinstance(lifecycle, dict) or lifecycle.get("ordered_gates") != expected_gates:
        failures.append("public data governance lifecycle gate ordering drifted")

    quarantine = control.get("quarantine")
    if not isinstance(quarantine, dict):
        failures.append("public data governance control plane missing quarantine contract")
    else:
        if quarantine.get("ledger_path") != "reference/public_data_quarantine_ledger_v1.json":
            failures.append("public data governance quarantine ledger path drifted")
        for key in (
            "active_entry_blocks_public_handoff_ready",
            "active_entry_blocks_private_qualification_intake",
            "release_requires_replacement_or_reproof",
            "release_requires_documented_semantic_review",
        ):
            if quarantine.get(key) is not True:
                failures.append(f"quarantine fail-closed rule disabled or missing: {key}")
        if quarantine.get("release_does_not_grant_private_qualification") is not True:
            failures.append("quarantine release must not grant private qualification")

    products = control.get("registered_products")
    if not isinstance(products, list) or not products:
        failures.append("public data governance registry is empty")
    else:
        seen_product_ids: set[str] = set()
        for product in products:
            if not isinstance(product, dict):
                failures.append("registered public data product must be an object")
                continue
            product_id = product.get("product_id")
            if not isinstance(product_id, str) or not product_id:
                failures.append("registered public data product missing product_id")
                continue
            if product_id in seen_product_ids:
                failures.append(f"duplicate registered public data product: {product_id}")
            seen_product_ids.add(product_id)
            for path_key in ("contract_path", "producer_path"):
                raw_path = product.get(path_key)
                if not isinstance(raw_path, str) or not raw_path:
                    failures.append(f"{product_id} missing {path_key}")
                    continue
                if not (ROOT / raw_path).exists():
                    failures.append(f"{product_id} references missing {path_key}: {raw_path}")
            if product.get("public_workflow_success_grants_private_qualification") is not False:
                failures.append(f"{product_id} must not let public workflow success grant private qualification")
            if product.get("may_promote_evidence") is not False:
                failures.append(f"{product_id} must not promote evidence from the public governance registry")

    if ledger.get("schema_version") != "public-data-quarantine-ledger-v1":
        failures.append("unexpected public data quarantine ledger schema")
    if ledger.get("status") != "ACTIVE_ENGINEERING_QUARANTINE_GUARD_NOT_EVIDENCE_AUTHORITY":
        failures.append("public data quarantine ledger changed authority status")

    ledger_authority = ledger.get("authority")
    if not isinstance(ledger_authority, dict):
        failures.append("public data quarantine ledger missing authority object")
    else:
        for key in (
            "may_promote_evidence",
            "may_change_evidence_eligibility",
            "may_change_pit_no_lookahead_semantics",
            "may_change_production",
            "may_authorize_trading",
        ):
            if ledger_authority.get(key) is not False:
                failures.append(f"quarantine ledger authority escalation or missing flag: {key}")

    entries = ledger.get("entries")
    if not isinstance(entries, list):
        failures.append("public data quarantine ledger entries must be a list")
        return
    seen_quarantine_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            failures.append("quarantine entry must be an object")
            continue
        quarantine_id = entry.get("quarantine_id")
        if not isinstance(quarantine_id, str) or not quarantine_id:
            failures.append("quarantine entry missing quarantine_id")
            continue
        if quarantine_id in seen_quarantine_ids:
            failures.append(f"duplicate quarantine entry: {quarantine_id}")
        seen_quarantine_ids.add(quarantine_id)
        if entry.get("state") == "ACTIVE":
            for key in (
                "downstream_consumption_allowed",
                "private_qualification_intake_allowed",
                "may_promote_evidence",
                "may_change_production",
                "may_authorize_trading",
            ):
                if entry.get(key) is not False:
                    failures.append(f"active quarantine {quarantine_id} is not fail-closed for {key}")
            if entry.get("resolved") is not False:
                failures.append(f"active quarantine {quarantine_id} cannot be resolved")
        source_sha = entry.get("source_head_sha")
        if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            failures.append(f"quarantine {quarantine_id} has invalid source_head_sha")
        digest = entry.get("artifact_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            failures.append(f"quarantine {quarantine_id} has invalid artifact_digest")


def main() -> None:
    failures: list[str] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if not path.is_file():
            continue
        lowered = relative.as_posix().lower()
        if any(part in lowered for part in FORBIDDEN_PATH_PARTS):
            failures.append(f"forbidden public path: {relative}")
        if path == Path(__file__):
            continue
        if path.suffix in {".py", ".yml", ".yaml", ".toml"}:
            text = path.read_text(encoding="utf-8")
            if FORBIDDEN_IMPORTS.search(text):
                failures.append(f"private model import: {relative}")
            for marker in CREDENTIAL_MARKERS:
                if re.search(marker, text):
                    failures.append(f"credential-like content: {relative}")

    _audit_governance_contracts(failures)

    if failures:
        raise SystemExit("public-tree audit failed:\n" + "\n".join(failures))
    print("public-tree audit passed")


if __name__ == "__main__":
    main()
