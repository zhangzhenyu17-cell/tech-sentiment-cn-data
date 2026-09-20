from __future__ import annotations

import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
import tarfile


SCHEMA = "v4c03-public-bundle-v1"
RELEASE_TAG = "v4c03-public-data-v1"
PHASE_START = "2021-06-15"
PHASE_END = "2021-12-31"

PRODUCER_FILES = (
    ".github/workflows/v4c03-01-phase-a-public-universe.yml",
    "reference/v4c03_phase_a_public_universe_v1.json",
    "data/reference/kc50_anchor_2026-09-14.csv",
    "data/reference/v4c03_kc50_adjustments_2021h2_2026.csv",
    "data/reference/chinext50_anchor_2026-06-15.csv",
    "data/reference/v4c03_chinext50_adjustments_2021h2_2026.csv",
    "scripts/materialize_v4c03_phase_a_public_universe.py",
    "scripts/assemble_v4c03_phase_a_public_bundle.py",
    "scripts/package_v4c03_public_bundle.py",
    "scripts/publish_v4c03_public_bundle.sh",
    "src/tech_sentiment/data_akshare.py",
    "src/tech_sentiment/index_history.py",
    "src/tech_sentiment/index_price.py",
    "src/tech_sentiment/universe.py",
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
    rows = []
    for relative in PRODUCER_FILES:
        path = repo_root / relative
        if not path.is_file():
            raise FileNotFoundError(f"V4C-03 producer file missing: {relative}")
        rows.append(
            {
                "path": relative,
                "sha256": _sha(path),
                "bytes": int(path.stat().st_size),
            }
        )
    fingerprint = sha256(_canonical(rows)).hexdigest()
    return {"producer_fingerprint": fingerprint, "producer_files": rows}


def _deterministic_tar(stage_root: Path, archive: Path) -> None:
    files = sorted(
        (path for path in stage_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(stage_root).as_posix(),
    )
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tf:
                for path in files:
                    relative = path.relative_to(stage_root).as_posix()
                    info = tf.gettarinfo(str(path), arcname=relative)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    with path.open("rb") as handle:
                        tf.addfile(info, handle)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Package the immutable public-only V4C-03 Phase A bundle."
    )
    parser.add_argument("--stage-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    stage_root = args.stage_root.resolve()
    receipt_path = stage_root / "receipt.json"
    if not receipt_path.is_file():
        raise FileNotFoundError("V4C-03 Phase A receipt missing")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "PUBLIC_PHASE_A_ASSEMBLED":
        raise ValueError("V4C-03 Phase A assembly receipt status mismatch")
    if receipt.get("start_date") != PHASE_START or receipt.get("end_date") != PHASE_END:
        raise ValueError("V4C-03 Phase A receipt window mismatch")
    if receipt.get("public_only") is not True:
        raise ValueError("V4C-03 Phase A receipt is not public-only")
    for key in (
        "private_model_semantics_present",
        "portfolio_or_holdings_data_present",
        "forward_result_computation_run",
        "production_or_trading_authority_changed",
    ):
        if receipt.get(key) is not False:
            raise ValueError(f"V4C-03 public boundary drift: {key}")

    producer = _producer_identity(repo_root)
    compatibility_payload = {
        "schema_version": SCHEMA,
        "phase": "A",
        "start_date": PHASE_START,
        "end_date": PHASE_END,
        "producer_fingerprint": producer["producer_fingerprint"],
    }
    compatibility_key = sha256(_canonical(compatibility_payload)).hexdigest()
    base = (
        f"v4c03-phase-a-public-universe-20210615-20211231-"
        f"{compatibility_key[:20]}"
    )

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{base}.tar.gz"
    _deterministic_tar(stage_root, archive)

    stage_files = []
    for path in sorted(
        (p for p in stage_root.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(stage_root).as_posix(),
    ):
        stage_files.append(
            {
                "path": path.relative_to(stage_root).as_posix(),
                "sha256": _sha(path),
                "bytes": int(path.stat().st_size),
            }
        )

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
        "immutable": True,
        "public_only": True,
        "private_model_semantics_present": False,
        "portfolio_or_holdings_data_present": False,
        "forward_result_computation_run": False,
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
        "manifest": manifest_path.name,
        "archive": archive.name,
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
