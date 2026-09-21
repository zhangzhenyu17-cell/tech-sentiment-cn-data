from __future__ import annotations

import gzip
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
from typing import Any, Iterable

import pandas as pd

from .immutable_checkpoint import CheckpointIdentity, ImmutableCheckpointStore


CHECKPOINT_BUNDLE_SCHEMA = "prospective-context-checkpoint-progress-v1"
CHECKPOINT_RELEASE_TAG_PREFIX = "prospective-context-checkpoints"
UNIVERSE_CHECKPOINT_VERSION = "prospective-universe-checkpoint-v1"


_UNIVERSE_SEMANTIC_FILES = {
    "STAR50": (
        "reference/prospective_context_raw_v1.json",
        "data/reference/kc50_anchor_2026-09-14.csv",
        "data/reference/v4c03_kc50_adjustments_2021h2_2026.csv",
        "src/tech_sentiment/data_akshare.py",
        "src/tech_sentiment/index_history.py",
        "src/tech_sentiment/index_price.py",
        "src/tech_sentiment/production_universe.py",
        "src/tech_sentiment/universe.py",
        "src/tech_sentiment/bounded_retry.py",
    ),
    "ChiNext50": (
        "reference/prospective_context_raw_v1.json",
        "data/reference/chinext50_anchor_2026-06-15.csv",
        "data/reference/v4c03_chinext50_adjustments_2021h2_2026.csv",
        "src/tech_sentiment/data_akshare.py",
        "src/tech_sentiment/index_history.py",
        "src/tech_sentiment/index_price.py",
        "src/tech_sentiment/production_universe.py",
        "src/tech_sentiment/universe.py",
        "src/tech_sentiment/bounded_retry.py",
    ),
}

_CAPITAL_SEMANTIC_FILES = (
    "reference/prospective_context_raw_v1.json",
    "src/tech_sentiment/capital_input_data.py",
    "src/tech_sentiment/resumable_capital.py",
    "src/tech_sentiment/immutable_checkpoint.py",
    "src/tech_sentiment/bounded_retry.py",
)

_SZSE_ETF_SEMANTIC_FILES = (
    "reference/prospective_context_raw_v1.json",
    "src/tech_sentiment/v4c03_szse_etf_shares.py",
    "src/tech_sentiment/resumable_capital.py",
    "src/tech_sentiment/immutable_checkpoint.py",
    "src/tech_sentiment/bounded_retry.py",
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checkpoint_release_tag(operation_date: str) -> str:
    date = pd.Timestamp(operation_date).date().isoformat()
    return f"{CHECKPOINT_RELEASE_TAG_PREFIX}-{date}"


def semantic_fingerprint(
    repo_root: str | Path,
    *,
    family: str,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    if family.startswith("universe:"):
        universe = family.split(":", 1)[1]
        if universe not in _UNIVERSE_SEMANTIC_FILES:
            raise ValueError(f"unknown prospective universe family: {universe}")
        files = _UNIVERSE_SEMANTIC_FILES[universe]
    elif family == "capital:sse":
        files = _CAPITAL_SEMANTIC_FILES
    elif family == "capital:szse":
        files = _SZSE_ETF_SEMANTIC_FILES
    else:
        raise ValueError(f"unknown prospective checkpoint family: {family}")

    rows: list[dict[str, Any]] = []
    for relative in files:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(relative)
        rows.append(
            {
                "path": relative,
                "sha256": file_sha256(path),
                "bytes": int(path.stat().st_size),
            }
        )
    payload = {
        "schema_version": CHECKPOINT_BUNDLE_SCHEMA,
        "family": family,
        "files": rows,
    }
    fingerprint = _sha256_bytes(_canonical_json(payload).encode("utf-8"))
    return {
        "family": family,
        "producer_fingerprint": fingerprint,
        "producer_files": rows,
        "checkpoint_revision": f"semantic:{fingerprint}",
    }


def universe_checkpoint_identity(
    repo_root: str | Path,
    *,
    universe: str,
    index_code: str,
    operation_date: str,
    start_date: str,
    trading_dates: Iterable[object],
) -> CheckpointIdentity:
    descriptor = semantic_fingerprint(repo_root, family=f"universe:{universe}")
    dates = pd.DatetimeIndex(
        pd.to_datetime(list(trading_dates), errors="raise")
    ).normalize()
    return CheckpointIdentity(
        producer=f"prospective-universe-{universe.lower()}",
        producer_version=UNIVERSE_CHECKPOINT_VERSION,
        source_commit=str(descriptor["checkpoint_revision"]),
        source_identities=(
            "PIT_ANCHOR_PLUS_OFFICIAL_ADJUSTMENTS",
            "CURRENT_COMPONENT_LIVE_WITNESS",
            "A_SHARE_DAILY_HISTORY",
            "INDEX_DAILY_HISTORY",
        ),
        query_identity={
            "universe": universe,
            "index_code": str(index_code).zfill(6),
            "operation_date": operation_date,
            "adjust": "qfq",
            "stock_history_providers": ["tencent", "eastmoney"],
        },
        scope={
            "start_date": start_date,
            "end_date": operation_date,
            "trading_dates": [
                str(pd.Timestamp(value).date()) for value in dates
            ],
        },
    )


def expected_checkpoint_assets(
    repo_root: str | Path,
    *,
    contract: dict[str, Any],
    operation_date: str,
    trading_dates: Iterable[object],
) -> list[dict[str, Any]]:
    """Resolve exact checkpoint fingerprints eligible for one capture."""
    from .resumable_capital import (
        expected_capital_checkpoint_identities,
        expected_szse_etf_checkpoint_identities,
    )

    dates = pd.DatetimeIndex(
        pd.to_datetime(list(trading_dates), errors="raise")
    ).normalize()
    start_date = str(pd.Timestamp(dates.min()).date())
    rows: list[dict[str, Any]] = []

    for universe, cfg in contract["universes"].items():
        identity = universe_checkpoint_identity(
            repo_root,
            universe=universe,
            index_code=cfg["index_code"],
            operation_date=operation_date,
            start_date=start_date,
            trading_dates=dates,
        )
        rows.append(
            {
                "store_name": "universe",
                "producer": identity.producer,
                "fingerprint": identity.fingerprint,
            }
        )

    capital_revision = str(
        semantic_fingerprint(repo_root, family="capital:sse")[
            "checkpoint_revision"
        ]
    )
    for store_name, identity in expected_capital_checkpoint_identities(
        trading_dates=dates,
        fund_codes=["588000"],
        checkpoint_revision=capital_revision,
        capture_date=operation_date,
    ):
        rows.append(
            {
                "store_name": store_name,
                "producer": identity.producer,
                "fingerprint": identity.fingerprint,
            }
        )

    szse_revision = str(
        semantic_fingerprint(repo_root, family="capital:szse")[
            "checkpoint_revision"
        ]
    )
    for store_name, identity in expected_szse_etf_checkpoint_identities(
        trading_dates=dates,
        fund_codes=["159915"],
        checkpoint_revision=szse_revision,
        capture_date=operation_date,
    ):
        rows.append(
            {
                "store_name": store_name,
                "producer": identity.producer,
                "fingerprint": identity.fingerprint,
            }
        )
    return rows


def _safe_relative(value: str) -> str:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe relative path: {value!r}")
    return path.as_posix()


def _deterministic_progress_tar(
    archive_path: Path,
    *,
    cache_root: Path,
    checkpoint_dirs: list[Path],
) -> None:
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as tar:
                files: list[Path] = []
                for directory in checkpoint_dirs:
                    files.extend(path for path in directory.rglob("*") if path.is_file())
                for path in sorted(
                    files,
                    key=lambda p: p.relative_to(cache_root).as_posix(),
                ):
                    rel = _safe_relative(path.relative_to(cache_root).as_posix())
                    info = tar.gettarinfo(str(path), arcname=rel)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    with path.open("rb") as handle:
                        tar.addfile(info, handle)


def _progress_identity_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": manifest["schema_version"],
        "operation_date": manifest["operation_date"],
        "release_tag": manifest["release_tag"],
        "archive_sha256": manifest["archive_sha256"],
        "archive_bytes": manifest["archive_bytes"],
        "checkpoint_units": manifest["checkpoint_units"],
        "formal_evidence_handoff": manifest["formal_evidence_handoff"],
        "qualification_granted": manifest["qualification_granted"],
        "reuse_semantics": manifest["reuse_semantics"],
    }


def _validate_progress_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != CHECKPOINT_BUNDLE_SCHEMA:
        raise ValueError("prospective checkpoint manifest schema mismatch")
    expected = _sha256_bytes(
        _canonical_json(_progress_identity_payload(manifest)).encode("utf-8")
    )
    if manifest.get("bundle_identity") != expected:
        raise ValueError("prospective checkpoint manifest identity mismatch")
    if manifest.get("release_tag") != checkpoint_release_tag(
        str(manifest.get("operation_date") or "")
    ):
        raise ValueError("prospective checkpoint release tag mismatch")


def package_complete_checkpoints(
    cache_root: str | Path,
    *,
    out_dir: str | Path,
    operation_date: str,
) -> dict[str, Any]:
    root = Path(cache_root)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    release_tag = checkpoint_release_tag(operation_date)
    checkpoint_dirs: list[Path] = []
    units: list[dict[str, Any]] = []

    if root.exists():
        for receipt_path in sorted(root.glob("*/*/receipt.json")):
            checkpoint_dir = receipt_path.parent
            store_name = checkpoint_dir.parent.name
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("completion_state") != "COMPLETE_CHUNK":
                continue
            metadata = dict(receipt.get("metadata") or {})
            if metadata.get("permanent_reuse_eligible") is not True:
                continue
            if metadata.get("capture_date") not in (None, operation_date) and metadata.get(
                "operation_date"
            ) not in (None, operation_date):
                continue
            fingerprint = str(receipt.get("fingerprint") or "")
            if not fingerprint or checkpoint_dir.name != fingerprint:
                raise ValueError("checkpoint directory fingerprint mismatch")
            checkpoint_dirs.append(checkpoint_dir)
            units.append(
                {
                    "store_name": store_name,
                    "checkpoint_fingerprint": fingerprint,
                    "checkpoint_receipt_sha256": str(
                        receipt.get("receipt_sha256") or ""
                    ),
                    "checkpoint_identity": receipt.get("identity"),
                    "checkpoint_metadata": metadata,
                }
            )

    if not units:
        index = {
            "schema_version": CHECKPOINT_BUNDLE_SCHEMA,
            "release_tag": release_tag,
            "operation_date": operation_date,
            "bundles": [],
        }
        (out / "checkpoint-index.json").write_text(
            json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return index

    provisional = out / ".progress.tmp.tar.gz"
    _deterministic_progress_tar(
        provisional,
        cache_root=root,
        checkpoint_dirs=checkpoint_dirs,
    )
    archive_sha = file_sha256(provisional)
    manifest: dict[str, Any] = {
        "schema_version": CHECKPOINT_BUNDLE_SCHEMA,
        "operation_date": operation_date,
        "release_tag": release_tag,
        "archive_sha256": archive_sha,
        "archive_bytes": int(provisional.stat().st_size),
        "checkpoint_units": sorted(
            units,
            key=lambda item: (
                str(item["store_name"]),
                str(item["checkpoint_fingerprint"]),
            ),
        ),
        "formal_evidence_handoff": False,
        "qualification_granted": False,
        "reuse_semantics": "EXACT_SAME_CAPTURE_CHECKPOINT_IDENTITY_ONLY",
    }
    manifest["bundle_identity"] = _sha256_bytes(
        _canonical_json(_progress_identity_payload(manifest)).encode("utf-8")
    )
    base = (
        f"pcraw-progress-v1-"
        f"{operation_date.replace('-', '')}-"
        f"{manifest['bundle_identity'][:20]}"
    )
    manifest["asset_base"] = base
    archive = out / f"{base}.tar.gz"
    provisional.replace(archive)
    manifest["archive"] = archive.name

    manifest_path = out / f"{base}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    sha_path = out / f"{base}.sha256"
    sha_path.write_text(
        f"{archive_sha}  {archive.name}\n",
        encoding="utf-8",
    )
    index = {
        "schema_version": CHECKPOINT_BUNDLE_SCHEMA,
        "release_tag": release_tag,
        "operation_date": operation_date,
        "bundles": [manifest],
    }
    (out / "checkpoint-index.json").write_text(
        json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return index


def plan_available_checkpoint_bundles(
    *,
    expected: dict[str, Any],
    manifests: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    operation_date = str(expected["operation_date"])
    expected_rows = expected.get("assets") or []
    expected_fingerprints = {
        str(item["fingerprint"]) for item in expected_rows
    }
    candidates: list[dict[str, Any]] = []

    for manifest in manifests:
        _validate_progress_manifest(manifest)
        if str(manifest["operation_date"]) != operation_date:
            continue
        unit_fingerprints = {
            str(item["checkpoint_fingerprint"])
            for item in manifest.get("checkpoint_units") or []
        }
        useful = unit_fingerprints & expected_fingerprints
        if useful:
            candidates.append(
                {
                    "asset_base": str(manifest["asset_base"]),
                    "bundle_identity": str(manifest["bundle_identity"]),
                    "useful_fingerprints": sorted(useful),
                    "useful_count": len(useful),
                    "unit_count": len(unit_fingerprints),
                }
            )

    # Prefer snapshots that add the most not-yet-covered expected work. Multiple
    # immutable snapshots may be selected; exact checkpoint identities make
    # overlapping restores byte-verifiable and harmless.
    selected: list[dict[str, Any]] = []
    covered: set[str] = set()
    remaining = sorted(
        candidates,
        key=lambda row: (
            -int(row["useful_count"]),
            str(row["bundle_identity"]),
        ),
    )
    while True:
        best = None
        best_gain: set[str] = set()
        for candidate in remaining:
            gain = set(candidate["useful_fingerprints"]) - covered
            if len(gain) > len(best_gain):
                best = candidate
                best_gain = gain
        if best is None or not best_gain:
            break
        selected.append(best)
        covered.update(best_gain)
        remaining.remove(best)

    missing = expected_fingerprints - covered
    return {
        "schema_version": CHECKPOINT_BUNDLE_SCHEMA,
        "release_tag": checkpoint_release_tag(operation_date),
        "operation_date": operation_date,
        "selected": selected,
        "selected_count": len(selected),
        "covered_checkpoint_count": len(covered),
        "expected_checkpoint_count": len(expected_fingerprints),
        "missing_checkpoint_count": len(missing),
        "missing_fingerprints": sorted(missing),
    }


def _identity_from_payload(payload: dict[str, Any]) -> CheckpointIdentity:
    identities = tuple(str(x) for x in payload.get("source_identities") or [])
    return CheckpointIdentity(
        producer=str(payload["producer"]),
        producer_version=str(payload["producer_version"]),
        source_commit=str(payload["source_commit"]),
        source_identities=identities,
        query_identity=dict(payload.get("query_identity") or {}),
        scope=dict(payload.get("scope") or {}),
        schema_version=str(payload.get("schema_version") or "v4a-checkpoint-v2"),
    )


def restore_checkpoint_bundle(
    *,
    archive_path: str | Path,
    manifest_path: str | Path,
    cache_root: str | Path,
) -> dict[str, Any]:
    archive = Path(archive_path)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    _validate_progress_manifest(manifest)
    if file_sha256(archive) != manifest.get("archive_sha256"):
        raise ValueError("prospective checkpoint archive SHA256 mismatch")
    if int(manifest.get("archive_bytes") or -1) != archive.stat().st_size:
        raise ValueError("prospective checkpoint archive size mismatch")

    root = Path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r:gz") as handle:
        members = handle.getmembers()
        for member in members:
            if not member.isfile():
                raise ValueError("checkpoint bundle contains non-file member")
            rel = _safe_relative(member.name)
            source = handle.extractfile(member)
            if source is None:
                raise ValueError(f"checkpoint member unreadable: {rel}")
            destination = root / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            incoming = source.read()
            if destination.exists() and destination.read_bytes() != incoming:
                raise ValueError(f"checkpoint restore conflict: {rel}")
            destination.write_bytes(incoming)

    for item in manifest.get("checkpoint_units") or []:
        store_name = _safe_relative(str(item["store_name"]))
        fingerprint = str(item["checkpoint_fingerprint"])
        identity = _identity_from_payload(dict(item["checkpoint_identity"]))
        if identity.fingerprint != fingerprint:
            raise ValueError("restored checkpoint identity/fingerprint mismatch")
        loaded = ImmutableCheckpointStore(root / store_name).load(identity)
        if loaded is None:
            raise ValueError("restored checkpoint cannot be loaded")
        if str(loaded.receipt.get("receipt_sha256") or "") != str(
            item.get("checkpoint_receipt_sha256") or ""
        ):
            raise ValueError("restored checkpoint receipt SHA mismatch")
    return manifest


__all__ = [
    "CHECKPOINT_BUNDLE_SCHEMA",
    "CHECKPOINT_RELEASE_TAG_PREFIX",
    "UNIVERSE_CHECKPOINT_VERSION",
    "checkpoint_release_tag",
    "semantic_fingerprint",
    "expected_checkpoint_assets",
    "universe_checkpoint_identity",
    "package_complete_checkpoints",
    "plan_available_checkpoint_bundles",
    "restore_checkpoint_bundle",
]
