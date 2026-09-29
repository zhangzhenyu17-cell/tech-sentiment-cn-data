from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_receipt_from_archive(archive: Path) -> tuple[dict, str]:
    with gzip.open(archive, "rb") as zipped:
        with tarfile.open(fileobj=zipped, mode="r:") as tar:
            member = tar.getmember(
                "source_observation/SOURCE_OBSERVATION_RECEIPT.json"
            )
            handle = tar.extractfile(member)
            if handle is None:
                raise ValueError("source observation archive receipt is unreadable")
            raw = handle.read()
    receipt = json.loads(raw.decode("utf-8"))
    if receipt.get("schema_version") != "prospective-source-observation-v3":
        raise ValueError("unexpected source observation receipt schema")
    if receipt.get("state") != "COMPLETE":
        raise ValueError("source observation archive is not COMPLETE")
    return receipt, hashlib.sha256(raw).hexdigest()


def heal_sidecars(archive: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt, receipt_sha256 = _read_receipt_from_archive(archive)
    tag = (
        "prospective-source-observation-v3-"
        f"{receipt['market_session_date']}-{receipt['source_key']}"
    )
    if archive.name != f"{tag}.tar.gz":
        raise ValueError("archive name does not match receipt identity")

    archive_sha256 = _sha256(archive)
    (output_dir / f"{tag}.sha256").write_text(
        f"{archive_sha256}  {archive.name}\n",
        encoding="utf-8",
    )
    identity = hashlib.sha256(
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    manifest = {
        "schema_version": "prospective-source-observation-package-v3",
        "release_tag": tag,
        "source_key": receipt["source_key"],
        "market_session_date": receipt["market_session_date"],
        "decision_date": receipt["decision_date"],
        "source_observation_identity": identity,
        "archive": archive.name,
        "archive_sha256": archive_sha256,
        "receipt_sha256": receipt_sha256,
        "first_observed_at_asia_shanghai": receipt[
            "first_observed_at_asia_shanghai"
        ],
        "observation_timestamp_semantics": receipt[
            "observation_timestamp_semantics"
        ],
        "shadow_decision_eligible": receipt["shadow_decision_eligible"],
        "formal_evidence_handoff": False,
        "historical_backfill_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
    }
    (output_dir / f"{tag}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            heal_sidecars(args.archive, args.output_dir),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
