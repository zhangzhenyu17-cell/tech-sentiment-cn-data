from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.v4a_persistent_stage import (
    _load_manifest,
    _safe_extract,
    _validate_family_stage_contract,
    input_bundle_identities,
    producer_fingerprint,
)
from tech_sentiment.v4a_stage_artifact import file_sha256, verify_stage_receipt


SCHEMA = "v4a-issuer-source-aggregate-compatibility-bridge-v1"
STATUS = "FROZEN_ONE_TIME_ENGINEERING_COMPATIBILITY"
CLASSIFICATION = (
    "FUNDAMENTAL_ONLY_QUALIFICATION_TOLERANCE_AND_DATA_INSUFFICIENCY_LOGIC"
)


def _load_contract(path: Path, *, start_date: str, end_date: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA:
        raise SystemExit("V4-A frozen-stage compatibility bridge schema mismatch")
    if payload.get("status") != STATUS:
        raise SystemExit("V4-A frozen-stage compatibility bridge is not frozen")
    if payload.get("release_tag") != "v4a-stage-bundles-v1":
        raise SystemExit("V4-A frozen-stage compatibility release tag mismatch")
    if int(payload.get("triggering_failure_run_id") or 0) != 35453638482:
        raise SystemExit("V4-A frozen-stage compatibility trigger run mismatch")

    window = payload.get("qualification_window") or {}
    if (
        str(window.get("start_date")) != start_date
        or str(window.get("end_date")) != end_date
    ):
        raise SystemExit("V4-A frozen-stage compatibility window mismatch")

    drift = payload.get("allowed_producer_drift") or {}
    if drift.get("path") != "scripts/assert_v4a_stage_qualifiable.py":
        raise SystemExit("V4-A frozen-stage allowlisted drift path mismatch")
    current_path = Path(str(drift["path"]))
    if (
        file_sha256(current_path) != drift.get("current_sha256")
        or current_path.stat().st_size != int(drift.get("current_bytes") or 0)
    ):
        raise SystemExit("V4-A frozen-stage current allowlisted drift no longer exact")
    if drift.get("classification") != CLASSIFICATION:
        raise SystemExit("V4-A frozen-stage drift classification mismatch")

    invariants = payload.get("invariants") or {}
    for key in (
        "exact_manifest_identity_required",
        "exact_archive_sha256_required",
        "exact_archive_bytes_required",
        "exact_stage_receipt_sha256_required",
        "exact_shared_input_identity_required",
        "current_producer_file_set_must_match_source_manifest",
        "all_non_allowlisted_producer_files_must_match_source_manifest_sha256",
        "allowlisted_drift_requires_exact_old_and_current_sha256",
    ):
        if invariants.get(key) is not True:
            raise SystemExit(f"V4-A frozen-stage compatibility invariant missing: {key}")
    for key in (
        "provider_refetch_allowed",
        "source_bundle_republication_allowed",
        "new_qualification_granted",
        "formal_evidence_handoff",
        "evidence_source_eligibility_changed",
        "pit_no_lookahead_semantics_changed",
        "research_scope_changed",
        "future_outcomes_used",
        "model_thresholds_changed",
        "signal_definitions_changed",
        "production_authority_changed",
        "trading_authority_changed",
    ):
        if invariants.get(key) is not False:
            raise SystemExit(f"V4-A frozen-stage compatibility boundary drift: {key}")
    return payload


def verify(args: argparse.Namespace) -> None:
    contract = _load_contract(
        Path(args.contract),
        start_date=args.start_date,
        end_date=args.end_date,
    )
    stages = contract.get("downstream_stages") or {}
    if args.family not in stages:
        raise SystemExit(f"frozen downstream stage is not contracted: {args.family}")
    expected = stages[args.family]
    if not isinstance(expected, dict):
        raise SystemExit(f"frozen downstream stage contract malformed: {args.family}")

    if args.family == "fundamental":
        stage_drifts = (contract.get("stage_allowed_producer_drifts") or {}).get(
            "fundamental"
        ) or []
        if len(stage_drifts) != 1:
            raise SystemExit("frozen Fundamental producer drift contract mismatch")
        drift = stage_drifts[0]
        if drift.get("classification") != "DERIVED_ONLY_COMPATIBILITY_IDENTITY_BINDING":
            raise SystemExit("frozen Fundamental drift classification mismatch")
        for key in (
            "fundamental_materialization_semantics_changed",
            "fundamental_qualification_semantics_changed",
            "pit_no_lookahead_semantics_changed",
        ):
            if drift.get(key) is not False:
                raise SystemExit(f"frozen Fundamental boundary drift: {key}")
        current_path = Path(str(drift["path"]))
        if (
            file_sha256(current_path) != drift.get("current_sha256")
            or current_path.stat().st_size != int(drift.get("current_bytes") or 0)
        ):
            raise SystemExit("frozen Fundamental current allowlisted drift no longer exact")
        allowed_drifts = {str(drift["path"]): drift}
    else:
        drift = contract["allowed_producer_drift"]
        semantic_flag = f"{args.family}_gate_semantics_changed"
        if drift.get(semantic_flag) is not False:
            raise SystemExit(
                f"frozen downstream stage gate semantics not proven unchanged: {args.family}"
            )
        allowed_drifts = {str(drift["path"]): drift}

    manifest = _load_manifest(args.manifest)
    exact_fields = {
        "family": args.family,
        "stage_kind": expected["stage_kind"],
        "stage_id": expected["stage_id"],
        "start_date": args.start_date,
        "end_date": args.end_date,
        "producer_fingerprint": expected["producer_fingerprint"],
        "compatibility_key": expected["compatibility_key"],
        "bundle_identity": expected["bundle_identity"],
        "archive_sha256": expected["archive_sha256"],
        "archive_bytes": expected["archive_bytes"],
        "stage_receipt_sha256": expected["stage_receipt_sha256"],
        "original_source_commit": expected["source_commit"],
        "release_tag": "v4a-stage-bundles-v1",
    }
    for key, value in exact_fields.items():
        if manifest.get(key) != value:
            raise SystemExit(f"frozen downstream manifest mismatch: {args.family}:{key}")

    shared_identity = input_bundle_identities({"shared": args.shared_manifest})["shared"]
    if manifest.get("input_bundles") != {"shared": shared_identity}:
        raise SystemExit(f"frozen downstream shared lineage mismatch: {args.family}")

    source_rows = {
        row["path"]: row for row in (manifest.get("producer_files") or [])
    }
    current_rows = {
        row["path"]: row
        for row in producer_fingerprint(".", args.family)["producer_files"]
    }
    if set(source_rows) != set(current_rows):
        raise SystemExit(f"frozen downstream producer file set drift: {args.family}")

    for path, old_row in source_rows.items():
        current_row = current_rows[path]
        allowed = allowed_drifts.get(path)
        if allowed is not None:
            if (
                old_row.get("sha256") != allowed["source_sha256"]
                or int(old_row.get("bytes") or 0) != int(allowed["source_bytes"])
                or current_row.get("sha256") != allowed["current_sha256"]
                or int(current_row.get("bytes") or 0) != int(allowed["current_bytes"])
            ):
                raise SystemExit(
                    f"frozen downstream allowlisted producer drift mismatch: "
                    f"{args.family}:{path}"
                )
        elif (
            old_row.get("sha256") != current_row.get("sha256")
            or int(old_row.get("bytes") or 0)
            != int(current_row.get("bytes") or 0)
        ):
            raise SystemExit(
                f"frozen downstream non-allowlisted producer drift: {args.family}:{path}"
            )

    archive = Path(args.archive)
    if file_sha256(archive) != expected["archive_sha256"]:
        raise SystemExit(f"frozen downstream archive SHA256 mismatch: {args.family}")
    if archive.stat().st_size != int(expected["archive_bytes"]):
        raise SystemExit(f"frozen downstream archive byte-size mismatch: {args.family}")

    target = Path(args.extract_to)
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise SystemExit(f"frozen downstream extraction target is not empty: {args.family}")
    _safe_extract(archive, target)
    _validate_family_stage_contract(target, args.family)
    receipt = verify_stage_receipt(
        root=target,
        receipt_path=target / "receipt.json",
        source_commit=expected["source_commit"],
        stage_kind=expected["stage_kind"],
        stage_id=expected["stage_id"],
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if receipt.get("receipt_sha256") != expected["stage_receipt_sha256"]:
        raise SystemExit(f"frozen downstream stage receipt mismatch: {args.family}")

    print(
        json.dumps(
            {
                "family": args.family,
                "asset_base": expected["asset_base"],
                "bundle_identity": expected["bundle_identity"],
                "source_run_id": expected["source_run_id"],
                "source_commit": expected["source_commit"],
                "provider_refetch": False,
                "compatibility_bridge": True,
            },
            sort_keys=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--family",
        required=True,
        choices=("capital", "financing", "fundamental", "policy"),
    )
    parser.add_argument("--contract", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--shared-manifest", required=True)
    parser.add_argument("--extract-to", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()
    verify(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
