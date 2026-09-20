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
    progress_semantic_files,
)
from tech_sentiment.v4a_stage_artifact import file_sha256, verify_stage_receipt


SCHEMA = "v4a-qualified-generation-reuse-contract-v1"
STATUS = "FROZEN_POST_QUALIFICATION_ENGINEERING_REUSE"
RELEASE_TAG = "v4a-stage-bundles-v1"


def _parse_input_manifests(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise SystemExit(f"input manifest must be name=path: {raw}")
        name, path = raw.split("=", 1)
        name = name.strip()
        path = path.strip()
        if not name or not path or name in result:
            raise SystemExit(f"invalid input manifest mapping: {raw}")
        result[name] = path
    return result


def _load_contract(path: Path, *, start_date: str, end_date: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("qualified-generation reuse contract must be an object")
    if payload.get("schema_version") != SCHEMA:
        raise SystemExit("qualified-generation reuse contract schema mismatch")
    if payload.get("status") != STATUS:
        raise SystemExit("qualified-generation reuse contract is not frozen")
    if payload.get("release_tag") != RELEASE_TAG:
        raise SystemExit("qualified-generation reuse release tag mismatch")
    window = payload.get("qualification_window") or {}
    if (
        str(window.get("start_date")) != start_date
        or str(window.get("end_date")) != end_date
    ):
        raise SystemExit("qualified-generation reuse window mismatch")

    handoff = payload.get("qualified_handoff") or {}
    if int(handoff.get("public_finalizer_run_id") or 0) != 35485765220:
        raise SystemExit("qualified-generation public finalizer identity mismatch")
    if handoff.get("private_intake_status") != "HISTORICAL_DATA_QUALIFIED":
        raise SystemExit("qualified-generation private qualification missing")
    if handoff.get("new_context_outcome_read") is not False:
        raise SystemExit("qualified-generation outcome-blind boundary drift")

    invariants = payload.get("invariants") or {}
    for key in (
        "exact_frozen_manifest_identity_required",
        "exact_archive_sha256_and_bytes_required",
        "exact_stage_receipt_required",
        "exact_input_bundle_lineage_required",
        "all_current_semantic_files_must_exist_in_frozen_manifest",
        "all_current_semantic_file_sha256_and_bytes_must_match_frozen_manifest",
        "nonsemantic_drift_must_be_allowlisted",
        "old_only_producer_files_forbidden",
        "current_only_producer_files_must_be_allowlisted",
        "public_private_security_boundary_unchanged",
        "private_qualification_result_unchanged",
    ):
        if invariants.get(key) is not True:
            raise SystemExit(f"qualified-generation invariant missing: {key}")
    return payload


def _verify_shared_anchor(
    contract: dict[str, object],
    input_manifests: dict[str, str],
) -> None:
    shared_path = input_manifests.get("shared")
    if shared_path is None:
        return
    manifest = _load_manifest(shared_path)
    expected = contract.get("shared_anchor") or {}
    exact = {
        "family": expected.get("family"),
        "stage_kind": expected.get("stage_kind"),
        "stage_id": expected.get("stage_id"),
        "original_source_commit": expected.get("source_commit"),
        "producer_fingerprint": expected.get("producer_fingerprint"),
        "compatibility_key": expected.get("compatibility_key"),
        "bundle_identity": expected.get("bundle_identity"),
        "archive_sha256": expected.get("archive_sha256"),
        "archive_bytes": expected.get("archive_bytes"),
        "stage_receipt_sha256": expected.get("stage_receipt_sha256"),
        "release_tag": RELEASE_TAG,
    }
    for key, value in exact.items():
        if manifest.get(key) != value:
            raise SystemExit(f"qualified Shared anchor mismatch: {key}")


def verify(args: argparse.Namespace) -> None:
    contract = _load_contract(
        Path(args.contract),
        start_date=args.start_date,
        end_date=args.end_date,
    )
    stages = contract.get("stages") or {}
    expected = stages.get(args.family)
    if not isinstance(expected, dict):
        raise SystemExit(f"family not frozen in qualified generation: {args.family}")

    input_manifests = _parse_input_manifests(args.input_manifest)
    expected_input_keys = list(expected.get("input_keys") or [])
    if sorted(input_manifests) != sorted(expected_input_keys):
        raise SystemExit(
            "qualified-generation input manifest key mismatch: "
            f"{args.family}:{sorted(input_manifests)}!={sorted(expected_input_keys)}"
        )
    _verify_shared_anchor(contract, input_manifests)

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
        "release_tag": RELEASE_TAG,
    }
    for key, value in exact_fields.items():
        if manifest.get(key) != value:
            raise SystemExit(
                f"qualified-generation frozen manifest mismatch: {args.family}:{key}"
            )

    expected_inputs = input_bundle_identities(input_manifests)
    if manifest.get("input_bundles") != expected_inputs:
        raise SystemExit(
            f"qualified-generation frozen input lineage mismatch: {args.family}"
        )

    source_rows = {
        str(row["path"]): row for row in (manifest.get("producer_files") or [])
    }
    current_rows = {
        str(row["path"]): row
        for row in producer_fingerprint(".", args.family)["producer_files"]
    }
    semantic_paths = {
        path.relative_to(Path.cwd()).as_posix()
        for path in progress_semantic_files(".", args.family)
    }
    allowed_operational = {
        str(path) for path in (expected.get("allowed_operational_paths") or [])
    }

    missing_semantic = sorted(semantic_paths - set(source_rows))
    if missing_semantic:
        raise SystemExit(
            "qualified-generation semantic file absent from frozen producer manifest: "
            + ",".join(missing_semantic)
        )

    for path in sorted(semantic_paths):
        old = source_rows[path]
        current = current_rows.get(path)
        if current is None:
            raise SystemExit(
                f"qualified-generation current semantic file missing: {args.family}:{path}"
            )
        if (
            old.get("sha256") != current.get("sha256")
            or int(old.get("bytes") or 0) != int(current.get("bytes") or 0)
        ):
            raise SystemExit(
                f"qualified-generation semantic drift forbidden: {args.family}:{path}"
            )

    old_only = sorted(set(source_rows) - set(current_rows))
    if old_only:
        raise SystemExit(
            "qualified-generation old-only producer files forbidden: "
            f"{args.family}:{','.join(old_only)}"
        )

    current_only = sorted(set(current_rows) - set(source_rows))
    unallowed_current_only = sorted(set(current_only) - allowed_operational)
    if unallowed_current_only:
        raise SystemExit(
            "qualified-generation current-only producer file not allowlisted: "
            f"{args.family}:{','.join(unallowed_current_only)}"
        )

    for path in sorted(set(source_rows) & set(current_rows)):
        if path in semantic_paths:
            continue
        old = source_rows[path]
        current = current_rows[path]
        changed = (
            old.get("sha256") != current.get("sha256")
            or int(old.get("bytes") or 0) != int(current.get("bytes") or 0)
        )
        if changed and path not in allowed_operational:
            raise SystemExit(
                f"qualified-generation nonsemantic drift not allowlisted: "
                f"{args.family}:{path}"
            )

    archive = Path(args.archive)
    if file_sha256(archive) != expected["archive_sha256"]:
        raise SystemExit(
            f"qualified-generation archive SHA256 mismatch: {args.family}"
        )
    if archive.stat().st_size != int(expected["archive_bytes"]):
        raise SystemExit(
            f"qualified-generation archive byte-size mismatch: {args.family}"
        )

    target = Path(args.extract_to)
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise SystemExit(
            f"qualified-generation extraction target is not empty: {args.family}"
        )
    _safe_extract(archive, target)
    _validate_family_stage_contract(target, args.family)
    receipt = verify_stage_receipt(
        root=target,
        receipt_path=target / "receipt.json",
        source_commit=str(expected["source_commit"]),
        stage_kind=str(expected["stage_kind"]),
        stage_id=str(expected["stage_id"]),
        start_date=args.start_date,
        end_date=args.end_date,
    )
    if receipt.get("receipt_sha256") != expected["stage_receipt_sha256"]:
        raise SystemExit(
            f"qualified-generation stage receipt mismatch: {args.family}"
        )

    print(
        json.dumps(
            {
                "family": args.family,
                "asset_base": expected["asset_base"],
                "bundle_identity": expected["bundle_identity"],
                "source_run_id": expected["source_run_id"],
                "source_commit": expected["source_commit"],
                "semantic_files_checked": len(semantic_paths),
                "operational_drift_paths": sorted(allowed_operational),
                "provider_refetch": False,
                "qualified_generation_reuse": True,
                "private_qualification_status": "HISTORICAL_DATA_QUALIFIED",
                "new_context_outcome_read": False,
            },
            sort_keys=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", required=True)
    parser.add_argument(
        "--contract",
        default="reference/v4a_qualified_generation_reuse_contract_v1.json",
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--extract-to", required=True)
    parser.add_argument("--input-manifest", action="append", default=[])
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args()
    verify(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
