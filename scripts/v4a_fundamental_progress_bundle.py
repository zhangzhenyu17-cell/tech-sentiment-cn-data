from __future__ import annotations

import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
from typing import Iterable, Mapping

import pandas as pd

from tech_sentiment.v4a_persistent_stage import (
    PERSISTENT_STAGE_SCHEMA,
    producer_files,
)
from tech_sentiment.v4a_stage_artifact import (
    build_stage_receipt,
    file_sha256,
    verify_stage_receipt,
    write_stage_receipt,
)


SCHEMA_VERSION = "v4a-fundamental-progress-unit-v1"
RELEASE_TAG = "v4a-stage-bundles-v1"
CONTRACT_ID = "V4A_FUNDAMENTAL_DURABLE_PROGRESS_V1"
EXCLUDED_SEMANTIC_PRODUCER_PATHS = {
    ".github/workflows/v4a-fundamental-earnings.yml",
}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _read_json(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _safe_relative(value: str) -> str:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe progress bundle path: {value!r}")
    return path.as_posix()


def _manifest_identity(payload: Mapping[str, object]) -> str:
    unsigned = dict(payload)
    unsigned.pop("bundle_identity", None)
    return sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()


def _semantic_fingerprint(repo_root: str | Path) -> dict[str, object]:
    root = Path(repo_root).resolve()
    rows: list[dict[str, object]] = []
    for path in producer_files(root, "fundamental"):
        relative = path.relative_to(root).as_posix()
        if relative in EXCLUDED_SEMANTIC_PRODUCER_PATHS:
            continue
        rows.append(
            {
                "path": relative,
                "sha256": file_sha256(path),
                "bytes": int(path.stat().st_size),
            }
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "files": rows,
    }
    return {
        "semantic_fingerprint": sha256(
            _canonical_json(payload).encode("utf-8")
        ).hexdigest(),
        "semantic_files": rows,
    }


def _symbols_for_unit(
    symbols_csv: str | Path,
    *,
    unit_index: int,
    unit_count: int,
) -> list[str]:
    if unit_count < 1 or unit_index < 0 or unit_index >= unit_count:
        raise ValueError("invalid Fundamental progress unit index/count")
    frame = pd.read_csv(symbols_csv, dtype=str)
    if "symbol" not in frame.columns:
        raise ValueError("scope CSV missing symbol")
    values = sorted(
        {str(value).zfill(6) for value in frame["symbol"].dropna().astype(str)}
    )
    return [
        value
        for position, value in enumerate(values)
        if position % unit_count == unit_index
    ]


def _progress_policy(contract_path: str | Path) -> dict[str, object]:
    contract = _read_json(contract_path)
    if contract.get("schema_version") != "v4a-fundamental-checkpoint-reuse-v1":
        raise ValueError("fundamental checkpoint reuse contract schema mismatch")
    progress = contract.get("progress_cache_policy")
    if not isinstance(progress, dict):
        raise ValueError("fundamental progress cache policy missing")
    if progress.get("mode") != "SPLIT_LEGACY_AND_CURRENT_PROGRESS_STORES_V1":
        raise ValueError("fundamental progress cache mode mismatch")
    if progress.get("formal_evidence_handoff") is not False:
        raise ValueError("Fundamental progress cache cannot grant formal evidence")
    if progress.get("partial_progress_never_grants_qualification") is not True:
        raise ValueError("Fundamental progress qualification boundary missing")

    persistent = contract.get("persistent_work_unit_bundle_policy")
    if not isinstance(persistent, dict):
        raise ValueError("Fundamental persistent work-unit policy missing")
    if persistent.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Fundamental persistent work-unit schema mismatch")
    if persistent.get("release_tag") != RELEASE_TAG:
        raise ValueError("Fundamental persistent work-unit release mismatch")
    if persistent.get("restore_before_materialization") is not True:
        raise ValueError("Fundamental persistent work-unit restore policy missing")
    if persistent.get("publish_only_after_work_unit_qualification_gate") is not True:
        raise ValueError("Fundamental persistent work-unit gate policy missing")
    if persistent.get("immutable_no_overwrite") is not True:
        raise ValueError("Fundamental persistent work-unit immutability missing")
    if persistent.get("formal_evidence_handoff") is not False:
        raise ValueError("Fundamental work-unit bundle cannot grant formal evidence")
    if persistent.get("requires_full_group_assembly") is not True:
        raise ValueError("Fundamental work-unit bundle must require full assembly")
    if persistent.get("partial_progress_never_grants_qualification") is not True:
        raise ValueError("Fundamental work-unit qualification boundary missing")
    excluded = set(map(str, persistent.get("semantic_fingerprint_excludes") or []))
    if excluded != EXCLUDED_SEMANTIC_PRODUCER_PATHS:
        raise ValueError("Fundamental semantic fingerprint exclusion drift")
    return progress


def _shared_bundle_identity(path: str | Path) -> dict[str, str]:
    payload = _read_json(path)
    if payload.get("schema_version") != PERSISTENT_STAGE_SCHEMA:
        raise ValueError("shared persistent bundle schema mismatch")
    identity = str(payload.get("bundle_identity") or "")
    if not identity:
        raise ValueError("shared persistent bundle identity missing")
    return {
        "family": str(payload.get("family") or ""),
        "stage_id": str(payload.get("stage_id") or ""),
        "bundle_identity": identity,
        "compatibility_key": str(payload.get("compatibility_key") or ""),
        "archive_sha256": str(payload.get("archive_sha256") or ""),
    }


def descriptor(
    *,
    repo_root: str | Path,
    symbols_csv: str | Path,
    shared_manifest: str | Path,
    contract_path: str | Path,
    start_date: str,
    end_date: str,
    unit_index: int,
    unit_count: int,
) -> dict[str, object]:
    progress = _progress_policy(contract_path)
    contract_count = int(progress.get("work_unit_count") or 0)
    if unit_count != contract_count:
        raise ValueError(
            f"Fundamental progress unit-count mismatch: expected={contract_count} actual={unit_count}"
        )
    symbols = _symbols_for_unit(
        symbols_csv,
        unit_index=unit_index,
        unit_count=unit_count,
    )
    if not symbols:
        raise ValueError("Fundamental progress unit has no symbols")
    fingerprint = _semantic_fingerprint(repo_root)
    shared = _shared_bundle_identity(shared_manifest)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "contract_id": CONTRACT_ID,
        "generation_id": str(progress.get("generation_id") or ""),
        "progress_checkpoint_source_commit": str(
            progress.get("progress_checkpoint_source_commit") or ""
        ),
        "start_date": str(start_date),
        "end_date": str(end_date),
        "unit_index": int(unit_index),
        "unit_count": int(unit_count),
        "symbols": symbols,
        "semantic_fingerprint": fingerprint["semantic_fingerprint"],
        "shared_bundle": shared,
    }
    compatibility_key = sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()
    compact_start = str(start_date).replace("-", "")
    compact_end = str(end_date).replace("-", "")
    asset_base = (
        f"v4a-fund-unit-{compact_start}-{compact_end}-"
        f"u{unit_index:02d}of{unit_count}-{compatibility_key[:16]}"
    )
    return {
        **payload,
        **fingerprint,
        "compatibility_key": compatibility_key,
        "asset_base": asset_base,
        "release_tag": RELEASE_TAG,
        "formal_evidence_handoff": False,
        "requires_full_group_assembly": True,
    }


def _deterministic_tar(
    archive_path: Path,
    *,
    root: Path,
    files: Iterable[Path],
) -> None:
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                for path in sorted(
                    files,
                    key=lambda item: item.relative_to(root).as_posix(),
                ):
                    relative = _safe_relative(path.relative_to(root).as_posix())
                    info = archive.gettarinfo(str(path), arcname=relative)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)


def _safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r:gz") as handle:
        members = handle.getmembers()
        for member in members:
            relative = _safe_relative(member.name)
            if not member.isfile():
                raise ValueError(
                    f"Fundamental progress archive contains non-file member: {relative}"
                )
        for member in members:
            relative = _safe_relative(member.name)
            source = handle.extractfile(member)
            if source is None:
                raise ValueError(
                    f"Fundamental progress archive member unreadable: {relative}"
                )
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())


def package(
    *,
    repo_root: str | Path,
    stage_root: str | Path,
    symbols_csv: str | Path,
    shared_manifest: str | Path,
    contract_path: str | Path,
    start_date: str,
    end_date: str,
    unit_index: int,
    unit_count: int,
    out_dir: str | Path,
) -> dict[str, object]:
    desc = descriptor(
        repo_root=repo_root,
        symbols_csv=symbols_csv,
        shared_manifest=shared_manifest,
        contract_path=contract_path,
        start_date=start_date,
        end_date=end_date,
        unit_index=unit_index,
        unit_count=unit_count,
    )
    root = Path(stage_root).resolve()
    stage_manifest = _read_json(root / "stage_manifest.json")
    if int(stage_manifest.get("shard_index") if stage_manifest.get("shard_index") is not None else -1) != unit_index:
        raise ValueError("Fundamental progress stage unit-index mismatch")
    if int(stage_manifest.get("shard_count") if stage_manifest.get("shard_count") is not None else -1) != unit_count:
        raise ValueError("Fundamental progress stage unit-count mismatch")
    stage_symbols = sorted(
        {str(value).zfill(6) for value in stage_manifest.get("symbols", [])}
    )
    if stage_symbols != list(desc["symbols"]):
        raise ValueError("Fundamental progress stage symbol set mismatch")
    if str(stage_manifest.get("start_date") or "") != str(start_date):
        raise ValueError("Fundamental progress stage start mismatch")
    if str(stage_manifest.get("end_date") or "") != str(end_date):
        raise ValueError("Fundamental progress stage end mismatch")
    source_commit = str(stage_manifest.get("source_commit") or "")
    if not source_commit:
        raise ValueError("Fundamental progress stage source commit missing")
    receipt = verify_stage_receipt(
        root=root,
        receipt_path=root / "receipt.json",
        source_commit=source_commit,
        stage_kind="fundamental_earnings",
        stage_id=f"fundamental-{unit_index}-of-{unit_count}",
        start_date=start_date,
        end_date=end_date,
    )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = str(desc["asset_base"])
    archive = out / f"{base}.tar.gz"
    files = [path for path in root.rglob("*") if path.is_file()]
    _deterministic_tar(archive, root=root, files=files)
    archive_sha = file_sha256(archive)
    manifest: dict[str, object] = {
        **desc,
        "original_source_commit": source_commit,
        "stage_receipt_sha256": str(receipt.get("receipt_sha256") or ""),
        "archive_sha256": archive_sha,
        "archive_bytes": int(archive.stat().st_size),
        "qualification_gate_passed_before_publication": True,
        "reuse_semantics": (
            "REUSABLE_COMPLETE_WORK_UNIT_ONLY_WHEN_SEMANTIC_FINGERPRINT_"
            "SHARED_BUNDLE_IDENTITY_WINDOW_AND_SYMBOL_SET_MATCH"
        ),
        "partial_progress_never_grants_qualification": True,
    }
    manifest["bundle_identity"] = _manifest_identity(manifest)
    manifest_path = out / f"{base}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    sha_path = out / f"{base}.sha256"
    sha_path.write_text(f"{archive_sha}  {archive.name}\n", encoding="utf-8")
    return manifest


def verify(
    *,
    repo_root: str | Path,
    archive_path: str | Path,
    manifest_path: str | Path,
    symbols_csv: str | Path,
    shared_manifest: str | Path,
    contract_path: str | Path,
    start_date: str,
    end_date: str,
    unit_index: int,
    unit_count: int,
    extract_to: str | Path,
) -> dict[str, object]:
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Fundamental progress bundle schema mismatch")
    if str(manifest.get("bundle_identity") or "") != _manifest_identity(manifest):
        raise ValueError("Fundamental progress bundle identity mismatch")
    desc = descriptor(
        repo_root=repo_root,
        symbols_csv=symbols_csv,
        shared_manifest=shared_manifest,
        contract_path=contract_path,
        start_date=start_date,
        end_date=end_date,
        unit_index=unit_index,
        unit_count=unit_count,
    )
    for key in (
        "contract_id",
        "generation_id",
        "progress_checkpoint_source_commit",
        "start_date",
        "end_date",
        "unit_index",
        "unit_count",
        "symbols",
        "semantic_fingerprint",
        "shared_bundle",
        "compatibility_key",
    ):
        if manifest.get(key) != desc.get(key):
            raise ValueError(f"Fundamental progress compatibility mismatch: {key}")
    if manifest.get("formal_evidence_handoff") is not False:
        raise ValueError("Fundamental progress bundle cannot be formal evidence")
    if manifest.get("requires_full_group_assembly") is not True:
        raise ValueError("Fundamental progress bundle must require full group assembly")
    if manifest.get("qualification_gate_passed_before_publication") is not True:
        raise ValueError("Fundamental progress bundle was not gate-qualified")
    if manifest.get("partial_progress_never_grants_qualification") is not True:
        raise ValueError("Fundamental progress qualification boundary missing")

    archive = Path(archive_path)
    if file_sha256(archive) != str(manifest.get("archive_sha256") or ""):
        raise ValueError("Fundamental progress archive SHA256 mismatch")
    if int(manifest.get("archive_bytes") or -1) != archive.stat().st_size:
        raise ValueError("Fundamental progress archive size mismatch")

    target = Path(extract_to)
    if target.exists() and any(target.iterdir()):
        raise ValueError("Fundamental progress extraction target is not empty")
    _safe_extract(archive, target)
    stage_manifest = _read_json(target / "stage_manifest.json")
    source_commit = str(manifest.get("original_source_commit") or "")
    if str(stage_manifest.get("source_commit") or "") != source_commit:
        raise ValueError("Fundamental progress stage source commit mismatch")
    receipt = verify_stage_receipt(
        root=target,
        receipt_path=target / "receipt.json",
        source_commit=source_commit,
        stage_kind="fundamental_earnings",
        stage_id=f"fundamental-{unit_index}-of-{unit_count}",
        start_date=start_date,
        end_date=end_date,
    )
    if str(receipt.get("receipt_sha256") or "") != str(
        manifest.get("stage_receipt_sha256") or ""
    ):
        raise ValueError("Fundamental progress receipt identity mismatch")
    return manifest


def rebase_verified_unit(
    *,
    stage_root: str | Path,
    manifest_path: str | Path,
    source_commit: str,
) -> dict[str, object]:
    """Re-seal a verified reusable unit onto the current producer commit.

    Only stage identity/provenance files are rewritten. Data payload files are
    unchanged, and the persistent bundle identity remains recorded explicitly.
    """

    root = Path(stage_root).resolve()
    bundle = _read_json(manifest_path)
    if bundle.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Fundamental progress bundle schema mismatch during rebase")
    unit_index = int(bundle.get("unit_index"))
    unit_count = int(bundle.get("unit_count"))
    original_commit = str(bundle.get("original_source_commit") or "")
    if not original_commit:
        raise ValueError("Fundamental progress original source commit missing")

    stage_manifest_path = root / "stage_manifest.json"
    stage_manifest = _read_json(stage_manifest_path)
    if str(stage_manifest.get("source_commit") or "") != original_commit:
        raise ValueError("Fundamental progress stage was not verified against original commit")
    if int(stage_manifest.get("shard_index") if stage_manifest.get("shard_index") is not None else -1) != unit_index:
        raise ValueError("Fundamental progress rebase unit-index mismatch")
    if int(stage_manifest.get("shard_count") if stage_manifest.get("shard_count") is not None else -1) != unit_count:
        raise ValueError("Fundamental progress rebase unit-count mismatch")

    stage_manifest["source_commit"] = str(source_commit)
    stage_manifest["progress_bundle_reuse"] = {
        "reused": True,
        "bundle_identity": str(bundle.get("bundle_identity") or ""),
        "compatibility_key": str(bundle.get("compatibility_key") or ""),
        "semantic_fingerprint": str(bundle.get("semantic_fingerprint") or ""),
        "original_source_commit": original_commit,
        "rebased_source_commit": str(source_commit),
        "data_payload_files_modified": False,
        "formal_evidence_handoff": False,
        "requires_full_group_assembly": True,
    }
    stage_manifest_path.write_text(
        json.dumps(stage_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    receipt_path = root / "receipt.json"
    files = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.resolve() != receipt_path.resolve()
    ]
    receipt = build_stage_receipt(
        root=root,
        files=files,
        stage_kind="fundamental_earnings",
        stage_id=f"fundamental-{unit_index}-of-{unit_count}",
        source_commit=str(source_commit),
        start_date=str(bundle.get("start_date") or ""),
        end_date=str(bundle.get("end_date") or ""),
        metadata={
            "reused_progress_bundle_identity": str(bundle.get("bundle_identity") or ""),
            "original_source_commit": original_commit,
            "data_payload_files_modified": False,
        },
    )
    write_stage_receipt(receipt_path, receipt)
    return {
        "unit_index": unit_index,
        "unit_count": unit_count,
        "original_source_commit": original_commit,
        "source_commit": str(source_commit),
        "bundle_identity": str(bundle.get("bundle_identity") or ""),
        "receipt_sha256": str(receipt.get("receipt_sha256") or ""),
        "data_payload_files_modified": False,
    }


def _emit(payload: Mapping[str, object], path: str | None) -> None:
    rendered = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        default=str,
    )
    if path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Package and verify durable V4-A Fundamental work-unit bundles."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("key", "package", "verify"):
        item = sub.add_parser(name)
        item.add_argument("--repo-root", default=".")
        item.add_argument("--symbols-csv", required=True)
        item.add_argument("--shared-manifest", required=True)
        item.add_argument(
            "--contract",
            default="reference/v4a_fundamental_checkpoint_reuse_contract_v1.json",
        )
        item.add_argument("--start-date", required=True)
        item.add_argument("--end-date", required=True)
        item.add_argument("--unit-index", type=int, required=True)
        item.add_argument("--unit-count", type=int, required=True)
        item.add_argument("--out", default="")

    sub.choices["package"].add_argument("--stage-root", required=True)
    sub.choices["package"].add_argument("--out-dir", required=True)
    sub.choices["verify"].add_argument("--archive", required=True)
    sub.choices["verify"].add_argument("--manifest", required=True)
    sub.choices["verify"].add_argument("--extract-to", required=True)

    rebase = sub.add_parser("rebase")
    rebase.add_argument("--stage-root", required=True)
    rebase.add_argument("--manifest", required=True)
    rebase.add_argument("--source-commit", required=True)
    rebase.add_argument("--out", default="")

    args = parser.parse_args()
    if args.command == "rebase":
        payload = rebase_verified_unit(
            stage_root=args.stage_root,
            manifest_path=args.manifest,
            source_commit=args.source_commit,
        )
        _emit(payload, args.out or None)
        return

    common = {
        "repo_root": args.repo_root,
        "symbols_csv": args.symbols_csv,
        "shared_manifest": args.shared_manifest,
        "contract_path": args.contract,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "unit_index": args.unit_index,
        "unit_count": args.unit_count,
    }
    if args.command == "key":
        payload = descriptor(**common)
    elif args.command == "package":
        payload = package(
            **common,
            stage_root=args.stage_root,
            out_dir=args.out_dir,
        )
    else:
        payload = verify(
            **common,
            archive_path=args.archive,
            manifest_path=args.manifest,
            extract_to=args.extract_to,
        )
    _emit(payload, args.out or None)


if __name__ == "__main__":
    main()
