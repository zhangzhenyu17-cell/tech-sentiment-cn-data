from __future__ import annotations

import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
import tarfile

from tech_sentiment.v4a_persistent_stage import producer_files


SCHEMA = "v4c03-phase-a-public-evidence-bundle-v1"
RELEASE_TAG = "v4c03-public-evidence-v1"

FAMILIES = (
    "issuer_cninfo",
    "issuer_sse",
    "issuer_szse",
    "fundamental",
    "prices",
    "policy",
    "capital",
)

CUSTOM_PRODUCER_FILES = (
    ".github/workflows/v4c03-04-phase-a-pit-evidence.yml",
    ".github/workflows/v4c03-04a-phase-a-pit-evidence-recovery.yml",
    ".github/workflows/v4c03-04b-phase-a-turnover-repair.yml",
    "reference/v4c03_phase_a_pit_evidence_v1.json",
    "reference/v4c03_phase_a_pit_evidence_recovery_v1.json",
    "reference/v4c03_phase_a_turnover_repair_v1.json",
    "scripts/repair_v4c03_phase_a_turnover.py",
    "scripts/prepare_v4c03_phase_a_evidence_inputs.py",
    "scripts/materialize_v4c03_phase_a_capital.py",
    "scripts/aggregate_v4a_issuer_shards.py",
    "scripts/assemble_v4a_derived_pit.py",
    "scripts/finalize_v4c03_phase_a_evidence.py",
    "scripts/package_v4c03_phase_a_evidence_bundle.py",
    "scripts/publish_v4c03_public_bundle.sh",
    "src/tech_sentiment/v4c03_szse_etf_shares.py",
)


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _producer_identity(repo_root: Path) -> dict[str, object]:
    paths: set[Path] = set()
    for family in FAMILIES:
        paths.update(path.resolve() for path in producer_files(repo_root, family))
    for relative in CUSTOM_PRODUCER_FILES:
        path = (repo_root / relative).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"V4C-03 evidence producer file missing: {relative}")
        paths.add(path)

    rows = [
        {
            "path": path.relative_to(repo_root).as_posix(),
            "sha256": _sha(path),
            "bytes": int(path.stat().st_size),
        }
        for path in sorted(paths, key=lambda item: item.relative_to(repo_root).as_posix())
    ]
    payload = {
        "schema_version": SCHEMA,
        "producer_files": rows,
    }
    return {
        "producer_fingerprint": sha256(_canonical(payload)).hexdigest(),
        "producer_files": rows,
    }


def _deterministic_tar(stage_root: Path, archive: Path) -> None:
    files = sorted(
        (path for path in stage_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(stage_root).as_posix(),
    )
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as handle:
                for path in files:
                    relative = path.relative_to(stage_root).as_posix()
                    info = handle.gettarinfo(str(path), arcname=relative)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    with path.open("rb") as source:
                        handle.addfile(info, source)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Package immutable V4C-03 Phase A public PIT evidence."
    )
    parser.add_argument("--stage-root", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    stage_root = args.stage_root.resolve()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("contract_id") != "v4c03_phase_a_pit_evidence_v1":
        raise ValueError("unexpected V4C-03 PIT evidence contract")
    if contract.get("status") != "FROZEN_PUBLIC_DATA_SCOPE":
        raise ValueError("V4C-03 PIT evidence contract is not frozen")

    receipt_path = stage_root / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "V4C03_PHASE_A_PUBLIC_EVIDENCE_MATERIALIZED_OUTCOME_BLIND":
        raise ValueError("V4C-03 public evidence receipt status mismatch")
    if receipt.get("source_commit") != args.source_commit:
        raise ValueError("V4C-03 public evidence receipt source commit mismatch")
    if receipt.get("public_only") is not True:
        raise ValueError("V4C-03 public evidence is not marked public-only")
    for key in (
        "private_model_semantics_present",
        "portfolio_or_holdings_data_present",
        "new_context_outcome_read",
        "new_context_forward_outcomes_read",
        "outcome_columns_materialized",
        "real_outcome_study_executed",
        "parameter_search_run",
        "feature_search_run",
        "threshold_search_run",
        "ml_run",
        "context_or_cause_semantics_changed",
        "universe_changed",
        "evidence_qualification_semantics_changed",
        "score_mapping_changed",
        "production_authority_changed",
        "trading_authority_changed",
    ):
        if receipt.get(key) is not False:
            raise ValueError(f"V4C-03 public evidence boundary drift: {key}")

    producer = _producer_identity(repo_root)
    public_inputs = contract["public_inputs"]
    compatibility_payload = {
        "schema_version": SCHEMA,
        "evidence_start": "2021-01-04",
        "sample_start": "2021-06-15",
        "end_date": "2021-12-31",
        "pre_sample_evidence_sample_eligible": False,
        "phase_a_bundle_identity": public_inputs["phase_a"]["bundle_identity"],
        "warmup_bundle_identity": public_inputs["warmup"]["bundle_identity"],
        "producer_fingerprint": producer["producer_fingerprint"],
    }
    compatibility_key = sha256(_canonical(compatibility_payload)).hexdigest()
    base = (
        "v4c03-phase-a-pit-evidence-20210104-20211231-"
        + compatibility_key[:20]
    )

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{base}.tar.gz"
    _deterministic_tar(stage_root, archive)

    stage_files = [
        {
            "path": path.relative_to(stage_root).as_posix(),
            "sha256": _sha(path),
            "bytes": int(path.stat().st_size),
        }
        for path in sorted(
            (item for item in stage_root.rglob("*") if item.is_file()),
            key=lambda item: item.relative_to(stage_root).as_posix(),
        )
    ]
    manifest = {
        **compatibility_payload,
        **producer,
        "asset_base": base,
        "source_commit": args.source_commit,
        "release_tag": RELEASE_TAG,
        "archive_sha256": _sha(archive),
        "archive_bytes": int(archive.stat().st_size),
        "stage_files": stage_files,
        "receipt_sha256": _sha(receipt_path),
        "contract_sha256": _sha(args.contract),
        "immutable": True,
        "public_only": True,
        "private_model_semantics_present": False,
        "portfolio_or_holdings_data_present": False,
        "new_context_outcome_read": False,
        "outcome_columns_materialized": False,
        "research_result_present": False,
        "production_or_trading_authority_changed": False,
    }
    manifest["bundle_identity"] = sha256(_canonical(manifest)).hexdigest()

    manifest_path = out_dir / f"{base}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    sha_path = out_dir / f"{base}.sha256"
    sha_path.write_text(
        f"{manifest['archive_sha256']}  {archive.name}\n",
        encoding="utf-8",
    )
    result = {
        "asset_base": base,
        "bundle_identity": manifest["bundle_identity"],
        "producer_fingerprint": producer["producer_fingerprint"],
        "archive": archive.name,
        "manifest": manifest_path.name,
        "sha256": sha_path.name,
        "release_tag": RELEASE_TAG,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
