from __future__ import annotations

from dataclasses import dataclass
import ast
import gzip
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import tarfile
from typing import Iterable, Mapping

from .v4a_stage_artifact import file_sha256, verify_stage_receipt


PERSISTENT_STAGE_SCHEMA = "v4a-persistent-stage-bundle-v1"
RELEASE_TAG = "v4a-stage-bundles-v1"


@dataclass(frozen=True)
class StageSpec:
    family: str
    stage_kind: str
    stage_id: str
    entrypoints: tuple[str, ...]
    extra_files: tuple[str, ...] = ()


STAGE_SPECS: dict[str, StageSpec] = {
    "shared": StageSpec(
        family="shared",
        stage_kind="shared",
        stage_id="shared",
        entrypoints=(
            "scripts/build_v4a_shared_calendar.py",
            "scripts/build_capital_pit_symbol_scope.py",
            "scripts/write_v4a_stage_receipt.py",
        ),
        extra_files=(
            ".github/workflows/v4a-shared-inputs.yml",
            "data/reference/kc50_anchor_2026-09-14.csv",
            "data/reference/kc50_adjustments_2022_2026.csv",
            "data/reference/chinext50_anchor_2026-06-15.csv",
            "data/reference/chinext50_adjustments_2022_2026.csv",
        ),
    ),
    "capital": StageSpec(
        family="capital",
        stage_kind="capital",
        stage_id="capital",
        entrypoints=(
            "scripts/qualify_capital_inputs.py",
            "scripts/assert_v4a_stage_qualifiable.py",
            "scripts/write_v4a_stage_receipt.py",
        ),
        extra_files=(".github/workflows/v4a-capital.yml",),
    ),
    "financing": StageSpec(
        family="financing",
        stage_kind="financing",
        stage_id="financing",
        entrypoints=(
            "scripts/materialize_financing_history.py",
            "scripts/assert_v4a_stage_qualifiable.py",
            "scripts/write_v4a_stage_receipt.py",
        ),
        extra_files=(".github/workflows/v4a-financing.yml",),
    ),
    "issuer_cninfo": StageSpec(
        family="issuer_cninfo",
        stage_kind="issuer_source_group",
        stage_id="issuer-cninfo-all",
        entrypoints=(
            "scripts/materialize_pit_evidence.py",
            "scripts/assert_v4a_stage_qualifiable.py",
            "scripts/write_v4a_stage_receipt.py",
        ),
        extra_files=(".github/workflows/v4a-issuer-source.yml",),
    ),
    "issuer_sse": StageSpec(
        family="issuer_sse",
        stage_kind="issuer_source_group",
        stage_id="issuer-sse-all",
        entrypoints=(
            "scripts/materialize_pit_evidence.py",
            "scripts/assert_v4a_stage_qualifiable.py",
            "scripts/write_v4a_stage_receipt.py",
        ),
        extra_files=(".github/workflows/v4a-issuer-source.yml",),
    ),
    "issuer_szse": StageSpec(
        family="issuer_szse",
        stage_kind="issuer_source_group",
        stage_id="issuer-szse-all",
        entrypoints=(
            "scripts/materialize_v4a_szse_issuer.py",
            "scripts/materialize_pit_evidence.py",
            "scripts/assert_v4a_stage_qualifiable.py",
            "scripts/write_v4a_stage_receipt.py",
        ),
        extra_files=(
            ".github/workflows/v4a-issuer-szse-migration.yml",
            "reference/v4a_szse_security_code_migration_contract_v1.json",
        ),
    ),
    "issuer_aggregate": StageSpec(
        family="issuer_aggregate",
        stage_kind="issuer_aggregate",
        stage_id="issuer_aggregate",
        entrypoints=(
            "scripts/aggregate_v4a_issuer_shards.py",
            "scripts/write_v4a_stage_receipt.py",
        ),
        extra_files=(".github/workflows/v4a-issuer-aggregate.yml",),
    ),
    "fundamental": StageSpec(
        family="fundamental",
        stage_kind="fundamental_group",
        stage_id="fundamental-all",
        entrypoints=(
            "scripts/materialize_v4a_fundamental_earnings_shard.py",
            "scripts/assert_v4a_stage_qualifiable.py",
            "scripts/write_v4a_stage_receipt.py",
        ),
        extra_files=(
            ".github/workflows/v4a-fundamental-earnings.yml",
            "reference/v4a_fundamental_pit_state_contract_v1.json",
            "reference/v4a_fundamental_checkpoint_reuse_contract_v1.json",
        ),
    ),
    "prices": StageSpec(
        family="prices",
        stage_kind="prices_group",
        stage_id="prices-all",
        entrypoints=(
            "scripts/materialize_v4a_price_shard.py",
            "scripts/write_v4a_stage_receipt.py",
        ),
        extra_files=(".github/workflows/v4a-prices.yml",),
    ),
    "policy": StageSpec(
        family="policy",
        stage_kind="policy",
        stage_id="policy",
        entrypoints=(
            "scripts/materialize_v4a_policy_stage.py",
            "scripts/assert_v4a_stage_qualifiable.py",
            "scripts/write_v4a_stage_receipt.py",
        ),
        extra_files=(".github/workflows/v4a-policy.yml",),
    ),
    "derived": StageSpec(
        family="derived",
        stage_kind="derived",
        stage_id="derived",
        entrypoints=(
            "scripts/assemble_v4a_derived_pit.py",
            "scripts/assert_v4a_stage_qualifiable.py",
            "scripts/write_v4a_stage_receipt.py",
        ),
        extra_files=(
            ".github/workflows/v4a-derived.yml",
            "reference/v4a_fundamental_pit_state_contract_v1.json",
        ),
    ),
}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _safe_relative(value: str) -> str:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe relative path: {value!r}")
    return path.as_posix()


def _module_path(repo_root: Path, module: str) -> Path | None:
    if module == "tech_sentiment":
        candidate = repo_root / "src/tech_sentiment/__init__.py"
        return candidate if candidate.is_file() else None
    if not module.startswith("tech_sentiment."):
        return None
    relative = module.split(".")[1:]
    py = repo_root.joinpath("src", "tech_sentiment", *relative).with_suffix(".py")
    if py.is_file():
        return py
    init = repo_root.joinpath("src", "tech_sentiment", *relative, "__init__.py")
    return init if init.is_file() else None


def _imported_local_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "tech_sentiment" or alias.name.startswith("tech_sentiment."):
                    modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                try:
                    rel = path.relative_to(path.parents[1])
                except ValueError:
                    continue
                if "tech_sentiment" not in rel.parts:
                    continue
                pkg_parts = list(rel.with_suffix("").parts)
                idx = pkg_parts.index("tech_sentiment")
                package = pkg_parts[idx:-1]
                keep = max(0, len(package) - node.level + 1)
                base = package[:keep]
                if node.module:
                    base.extend(node.module.split("."))
                module = ".".join(base)
                if module.startswith("tech_sentiment"):
                    modules.add(module)
            elif node.module and (
                node.module == "tech_sentiment"
                or node.module.startswith("tech_sentiment.")
            ):
                modules.add(node.module)
    return modules


def producer_files(repo_root: str | Path, family: str) -> list[Path]:
    root = Path(repo_root).resolve()
    if family not in STAGE_SPECS:
        raise ValueError(f"unknown V4-A stage family: {family}")
    spec = STAGE_SPECS[family]
    pending: list[Path] = []
    selected: set[Path] = set()

    for relative in ("pyproject.toml", *spec.entrypoints, *spec.extra_files):
        path = (root / relative).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"stage producer file missing: {relative}")
        pending.append(path)

    while pending:
        path = pending.pop()
        if path in selected:
            continue
        selected.add(path)
        if path.suffix != ".py":
            continue
        for module in _imported_local_modules(path):
            module_file = _module_path(root, module)
            if module_file is not None and module_file.resolve() not in selected:
                pending.append(module_file.resolve())

    return sorted(selected, key=lambda item: item.relative_to(root).as_posix())


def producer_fingerprint(repo_root: str | Path, family: str) -> dict[str, object]:
    root = Path(repo_root).resolve()
    rows = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": file_sha256(path),
            "bytes": int(path.stat().st_size),
        }
        for path in producer_files(root, family)
    ]
    payload = {
        "schema_version": PERSISTENT_STAGE_SCHEMA,
        "family": family,
        "files": rows,
    }
    fingerprint = sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return {"producer_fingerprint": fingerprint, "producer_files": rows}


def _manifest_identity(payload: Mapping[str, object]) -> str:
    unsigned = dict(payload)
    unsigned.pop("bundle_identity", None)
    return sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()


def _load_manifest(path: str | Path) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"persistent bundle manifest must be an object: {path}")
    if payload.get("schema_version") != PERSISTENT_STAGE_SCHEMA:
        raise ValueError(f"persistent bundle manifest schema mismatch: {path}")
    expected = str(payload.get("bundle_identity") or "")
    if not expected or expected != _manifest_identity(payload):
        raise ValueError(f"persistent bundle manifest identity mismatch: {path}")
    return payload


def input_bundle_identities(
    values: Mapping[str, str | Path] | None,
) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for name, path in sorted((values or {}).items()):
        payload = _load_manifest(path)
        identity = str(payload.get("bundle_identity") or "")
        if not identity:
            raise ValueError(f"input bundle identity missing: {name}")
        out[str(name)] = {
            "family": str(payload.get("family") or ""),
            "stage_id": str(payload.get("stage_id") or ""),
            "bundle_identity": identity,
            "compatibility_key": str(payload.get("compatibility_key") or ""),
            "archive_sha256": str(payload.get("archive_sha256") or ""),
        }
    return out


def compatibility_descriptor(
    *,
    repo_root: str | Path,
    family: str,
    start_date: str,
    end_date: str,
    input_manifests: Mapping[str, str | Path] | None = None,
) -> dict[str, object]:
    if family not in STAGE_SPECS:
        raise ValueError(f"unknown V4-A stage family: {family}")
    spec = STAGE_SPECS[family]
    fingerprint = producer_fingerprint(repo_root, family)
    inputs = input_bundle_identities(input_manifests)
    compatibility_payload = {
        "schema_version": PERSISTENT_STAGE_SCHEMA,
        "family": family,
        "stage_kind": spec.stage_kind,
        "stage_id": spec.stage_id,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "producer_fingerprint": fingerprint["producer_fingerprint"],
        "input_bundles": inputs,
    }
    compatibility_key = sha256(
        _canonical_json(compatibility_payload).encode("utf-8")
    ).hexdigest()
    compact_start = str(start_date).replace("-", "")
    compact_end = str(end_date).replace("-", "")
    asset_base = (
        f"v4a-{family}-{compact_start}-{compact_end}-"
        f"{compatibility_key[:20]}"
    )
    return {
        **compatibility_payload,
        **fingerprint,
        "compatibility_key": compatibility_key,
        "asset_base": asset_base,
    }


def _deterministic_tar(archive_path: Path, *, root: Path, files: Iterable[Path]) -> None:
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
                    relative = _safe_relative(path.relative_to(root).as_posix())
                    info = archive.gettarinfo(str(path), arcname=relative)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)


_SZSE_MIGRATION_MARKER = "v4a_szse_security_code_migration_contract_v1.json"


def _validate_family_stage_contract(root: Path, family: str) -> None:
    if family != "issuer_szse":
        return
    marker = root / "shards" / "szse" / _SZSE_MIGRATION_MARKER
    if not marker.is_file():
        raise ValueError(
            "issuer_szse stage missing frozen same-security code-migration contract"
        )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "v4a-szse-security-code-migration-v1":
        raise ValueError("issuer_szse code-migration contract schema mismatch")
    if payload.get("status") != "FROZEN_ENGINEERING_IDENTITY_ALIAS":
        raise ValueError("issuer_szse code-migration contract status mismatch")
    if payload.get("same_listed_security_identity_only") is not True:
        raise ValueError("issuer_szse migration must remain same-security only")
    for key in (
        "evidence_source_eligibility_changed",
        "pit_no_lookahead_semantics_changed",
        "research_scope_changed",
        "future_outcomes_used",
        "production_authority_changed",
        "trading_authority_changed",
    ):
        if payload.get(key) is not False:
            raise ValueError(f"issuer_szse code-migration boundary drift: {key}")


def package_stage_bundle(
    *,
    repo_root: str | Path,
    stage_root: str | Path,
    family: str,
    source_commit: str,
    start_date: str,
    end_date: str,
    out_dir: str | Path,
    input_manifests: Mapping[str, str | Path] | None = None,
) -> dict[str, object]:
    if family not in STAGE_SPECS:
        raise ValueError(f"unknown V4-A stage family: {family}")
    spec = STAGE_SPECS[family]
    root = Path(stage_root).resolve()
    _validate_family_stage_contract(root, family)
    receipt_path = root / "receipt.json"
    receipt = verify_stage_receipt(
        root=root,
        receipt_path=receipt_path,
        source_commit=source_commit,
        stage_kind=spec.stage_kind,
        stage_id=spec.stage_id,
        start_date=start_date,
        end_date=end_date,
    )
    descriptor = compatibility_descriptor(
        repo_root=repo_root,
        family=family,
        start_date=start_date,
        end_date=end_date,
        input_manifests=input_manifests,
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = str(descriptor["asset_base"])
    archive = out / f"{base}.tar.gz"
    files = [path for path in root.rglob("*") if path.is_file()]
    _deterministic_tar(archive, root=root, files=files)
    archive_sha = file_sha256(archive)
    manifest: dict[str, object] = {
        **descriptor,
        "original_source_commit": str(source_commit),
        "stage_receipt_sha256": str(receipt.get("receipt_sha256") or ""),
        "archive_sha256": archive_sha,
        "archive_bytes": int(archive.stat().st_size),
        "release_tag": RELEASE_TAG,
        "reuse_semantics": (
            "REUSABLE_WHEN_PRODUCER_FINGERPRINT_AND_INPUT_BUNDLE_IDENTITIES_MATCH"
        ),
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


def _safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r:gz") as handle:
        members = handle.getmembers()
        for member in members:
            relative = _safe_relative(member.name)
            if not member.isfile():
                raise ValueError(f"persistent stage archive contains non-file member: {relative}")
        for member in members:
            relative = _safe_relative(member.name)
            source = handle.extractfile(member)
            if source is None:
                raise ValueError(f"persistent stage member unreadable: {relative}")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())


def verify_and_extract_stage_bundle(
    *,
    repo_root: str | Path,
    archive_path: str | Path,
    manifest_path: str | Path,
    family: str,
    start_date: str,
    end_date: str,
    extract_to: str | Path,
    input_manifests: Mapping[str, str | Path] | None = None,
) -> dict[str, object]:
    manifest = _load_manifest(manifest_path)
    descriptor = compatibility_descriptor(
        repo_root=repo_root,
        family=family,
        start_date=start_date,
        end_date=end_date,
        input_manifests=input_manifests,
    )
    for key in (
        "family",
        "stage_kind",
        "stage_id",
        "start_date",
        "end_date",
        "producer_fingerprint",
        "compatibility_key",
        "input_bundles",
    ):
        if manifest.get(key) != descriptor.get(key):
            raise ValueError(f"persistent stage compatibility mismatch: {key}")
    archive = Path(archive_path)
    if file_sha256(archive) != str(manifest.get("archive_sha256") or ""):
        raise ValueError("persistent stage archive SHA256 mismatch")
    if int(manifest.get("archive_bytes") or -1) != archive.stat().st_size:
        raise ValueError("persistent stage archive size mismatch")
    target = Path(extract_to)
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"persistent stage extraction target is not empty: {target}")
    _safe_extract(archive, target)
    _validate_family_stage_contract(target, family)
    original_commit = str(manifest.get("original_source_commit") or "")
    if not original_commit:
        raise ValueError("persistent stage original source commit missing")
    receipt = verify_stage_receipt(
        root=target,
        receipt_path=target / "receipt.json",
        source_commit=original_commit,
        stage_kind=str(manifest["stage_kind"]),
        stage_id=str(manifest["stage_id"]),
        start_date=start_date,
        end_date=end_date,
    )
    if str(receipt.get("receipt_sha256") or "") != str(
        manifest.get("stage_receipt_sha256") or ""
    ):
        raise ValueError("persistent stage receipt identity mismatch")
    return manifest


__all__ = [
    "PERSISTENT_STAGE_SCHEMA",
    "RELEASE_TAG",
    "STAGE_SPECS",
    "StageSpec",
    "producer_files",
    "producer_fingerprint",
    "compatibility_descriptor",
    "package_stage_bundle",
    "verify_and_extract_stage_bundle",
]
