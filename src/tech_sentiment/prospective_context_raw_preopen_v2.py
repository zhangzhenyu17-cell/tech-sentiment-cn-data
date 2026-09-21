from __future__ import annotations

import gzip
import hashlib
import json
import tarfile
from pathlib import Path
from typing import Any

from .prospective_context_raw_v1 import (
    RECEIPT_NAME,
    CaptureResult,
    materialize_public_raw_capture,
)

TIMING_CONTRACT_ID = "prospective_context_raw_preopen_v2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_timing_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("contract_id") != TIMING_CONTRACT_ID:
        raise ValueError("unexpected public pre-open timing contract")
    if payload.get("status") != "FROZEN_PUBLIC_TIMING_SCOPE":
        raise ValueError("public pre-open timing contract is not frozen")
    timing = payload.get("timing") or {}
    if timing.get("data_freeze_deadline_asia_shanghai") != "05:30":
        raise ValueError("public pre-open data-freeze deadline drifted")
    if timing.get("first_eligible_execution_asia_shanghai") != "09:30":
        raise ValueError("public pre-open first-execution time drifted")
    if int(timing.get("minimum_preopen_buffer_hours") or 0) < 4:
        raise ValueError("public pre-open buffer may not be less than four hours")
    if timing.get("arbitrary_historical_backfill_allowed") is not False:
        raise ValueError("public pre-open contract may not allow historical backfill")
    if (payload.get("workflow") or {}).get("automatic_trigger_allowed") is not False:
        raise ValueError("public pre-open workflow may not add automatic triggers")
    return payload


def materialize_preopen_public_raw_capture(
    *,
    repo_root: Path,
    data_contract_path: Path,
    timing_contract_path: Path,
    market_session_date: str,
    decision_date: str,
    source_commit: str,
    output_root: Path,
    checkpoint_dir: Path,
    client: Any | None = None,
) -> CaptureResult:
    _read_timing_contract(timing_contract_path)
    return materialize_public_raw_capture(
        repo_root=repo_root,
        contract_path=data_contract_path,
        operation_date=market_session_date,
        source_commit=source_commit,
        output_root=output_root,
        checkpoint_dir=checkpoint_dir,
        client=client,
        timing_mode="PREOPEN_DUAL_CLOCK_V2",
        decision_date=decision_date,
    )


def package_preopen_capture(
    capture_root: Path,
    *,
    output_dir: Path,
    market_session_date: str,
    decision_date: str,
) -> dict[str, Any]:
    receipt_path = capture_root / RECEIPT_NAME
    if not receipt_path.is_file():
        raise FileNotFoundError(RECEIPT_NAME)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "PUBLIC_RAW_FORWARD_CAPTURE_COMPLETE":
        raise ValueError("public raw capture is not complete")
    if receipt.get("operation_date") != market_session_date:
        raise ValueError("public raw market-session date mismatch")
    timing = receipt.get("preopen_timing") or {}
    if timing.get("decision_date") != decision_date:
        raise ValueError("public raw decision date mismatch")
    if timing.get("minimum_preopen_buffer_hours") != 4:
        raise ValueError("public raw pre-open buffer identity mismatch")

    output_dir.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    base = (
        f"prospective-context-raw-preopen-v2-"
        f"{market_session_date}-for-{decision_date}"
    )
    archive = output_dir / f"{base}.tar.gz"
    with archive.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as tar:
                for path in sorted(capture_root.rglob("*")):
                    if not path.is_file():
                        continue
                    info = tar.gettarinfo(
                        str(path),
                        arcname=(
                            "prospective_context_raw/"
                            + path.relative_to(capture_root).as_posix()
                        ),
                    )
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    with path.open("rb") as handle:
                        tar.addfile(info, handle)

    checksum = _sha256(archive)
    checksum_path = output_dir / f"{base}.sha256"
    checksum_path.write_text(
        f"{checksum}  {archive.name}\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "prospective-context-public-raw-preopen-package-v2",
        "market_session_date": market_session_date,
        "decision_date": decision_date,
        "bundle_identity": identity,
        "release_tag": base,
        "archive": archive.name,
        "archive_sha256": checksum,
        "capture_receipt_sha256": _sha256(receipt_path),
        "data_freeze_deadline_asia_shanghai": timing[
            "data_freeze_deadline_asia_shanghai"
        ],
        "first_eligible_execution_at": timing["first_eligible_execution_at"],
        "minimum_preopen_buffer_hours": 4,
        "historical_replay_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
        "private_model_semantics_materialized": False,
    }
    (output_dir / f"{base}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "TIMING_CONTRACT_ID",
    "materialize_preopen_public_raw_capture",
    "package_preopen_capture",
]
