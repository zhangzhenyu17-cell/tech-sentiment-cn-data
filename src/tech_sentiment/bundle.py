from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile

import pandas as pd


REQUIRED_FILES = (
    "prices.csv",
    "universe_point_in_time.csv",
    "universe_segments.csv",
    "universe_live_snapshot.csv",
    "download_errors.csv",
    "index_prices.csv",
    "production_universe_meta.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quality_metrics(source: Path) -> dict:
    universe = pd.read_csv(source / "universe_point_in_time.csv", dtype={"symbol": str})
    prices = pd.read_csv(source / "prices.csv", dtype={"symbol": str})
    index_prices = pd.read_csv(source / "index_prices.csv")
    if universe.empty or prices.empty or index_prices.empty:
        raise ValueError("market bundle inputs must not be empty")

    universe["symbol"] = universe["symbol"].str.zfill(6)
    prices["symbol"] = prices["symbol"].str.zfill(6)
    prices["date"] = pd.to_datetime(prices["date"], errors="raise")
    market_date = prices["date"].max().normalize()
    starts = pd.to_datetime(universe["effective_start"], errors="raise")
    ends = pd.to_datetime(universe["effective_end"], errors="coerce")
    active = universe[(starts <= market_date) & (ends.isna() | (ends >= market_date))]
    active_symbols = set(active["symbol"])
    latest_symbols = set(prices.loc[prices["date"].dt.normalize() == market_date, "symbol"])
    all_symbols = set(universe["symbol"])
    downloaded_symbols = set(prices["symbol"])
    history_coverage = len(all_symbols & downloaded_symbols) / len(all_symbols) if all_symbols else 0.0
    active_coverage = len(active_symbols & latest_symbols) / len(active_symbols) if active_symbols else 0.0
    if history_coverage < 0.95:
        raise ValueError(f"historical symbol coverage {history_coverage:.1%} is below 95%")
    if active_coverage < 0.95:
        raise ValueError(f"active latest-day coverage {active_coverage:.1%} is below 95%")
    return {
        "market_date": market_date.date().isoformat(),
        "historical_symbol_coverage": round(history_coverage, 6),
        "active_latest_day_coverage": round(active_coverage, 6),
        "universe_symbols": len(all_symbols),
        "active_symbols": len(active_symbols),
    }


def _source_providers(source: Path) -> list[str]:
    providers: set[str] = set()
    for name in ("prices.csv", "index_prices.csv"):
        frame = pd.read_csv(source / name, nrows=100000)
        if "provider" in frame.columns:
            providers.update(
                value.strip()
                for value in frame["provider"].dropna().astype(str)
                if value.strip()
            )
    return sorted(providers)


def _canonical_json(payload: dict) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _deterministic_tar_gz(files: list[tuple[str, bytes]], destination: Path) -> None:
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name, data in sorted(files, key=lambda row: row[0]):
                    info = tarfile.TarInfo(name=name)
                    info.size = len(data)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(data))


def _dated_manifest(
    *,
    source: Path,
    target_date: str,
    quality: dict,
    public_repo_git_sha: str,
) -> dict:
    file_rows = []
    payloads: list[tuple[str, bytes]] = []
    for name in REQUIRED_FILES:
        data = (source / name).read_bytes()
        payloads.append((f"market_bundle/{name}", data))
        file_rows.append(
            {
                "path": name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return {
        "bundle_kind": "public_market_data_dated_immutable",
        "schema_version": "1.1",
        "target_date": target_date,
        "market_date": quality["market_date"],
        "public_repo_git_sha": public_repo_git_sha,
        "source_providers": _source_providers(source),
        "contains_model_output": False,
        "contains_private_evidence": False,
        "quality": quality,
        "files": file_rows,
    }


def build_market_bundle(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    public_repo_git_sha: str = "UNSPECIFIED",
) -> dict:
    source = Path(input_dir)
    destination = Path(output_dir)
    missing = [name for name in REQUIRED_FILES if not (source / name).is_file()]
    if missing:
        raise FileNotFoundError(f"market bundle is missing required files: {missing}")

    metadata = json.loads((source / "production_universe_meta.json").read_text(encoding="utf-8"))
    target_date = metadata.get("target_date")
    if not isinstance(target_date, str) or len(target_date) != 10:
        raise ValueError("production metadata has no valid target_date")
    if metadata.get("universe_mode") != "point_in_time":
        raise ValueError("only point-in-time universe data may be published")
    quality = _quality_metrics(source)

    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / "market_bundle_latest.tar.gz"
    manifest_path = destination / "market_bundle_manifest.json"
    checksum_path = destination / "market_bundle_latest.sha256"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "market_bundle"
        root.mkdir()
        files = []
        for name in REQUIRED_FILES:
            copied = root / name
            shutil.copy2(source / name, copied)
            files.append({"path": name, "bytes": copied.stat().st_size, "sha256": _sha256(copied)})

        manifest = {
            "bundle_kind": "public_market_data",
            "schema_version": "1.0",
            "target_date": target_date,
            "market_date": quality["market_date"],
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "public_repo_git_sha": public_repo_git_sha,
            "source_providers": _source_providers(source),
            "contains_model_output": False,
            "quality": quality,
            "files": files,
        }
        bundle_manifest = root / "manifest.json"
        bundle_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(root, arcname="market_bundle")

    checksum_path.write_text(
        f"{_sha256(archive_path)}  {archive_path.name}\n",
        encoding="utf-8",
    )

    dated_manifest = _dated_manifest(
        source=source,
        target_date=target_date,
        quality=quality,
        public_repo_git_sha=public_repo_git_sha,
    )
    dated_name = f"market-bundle-{quality['market_date']}"
    dated_manifest_path = destination / f"{dated_name}.manifest.json"
    dated_archive_path = destination / f"{dated_name}.tar.gz"
    dated_checksum_path = destination / f"{dated_name}.sha256"

    dated_manifest_bytes = _canonical_json(dated_manifest)
    dated_payloads = [
        (f"market_bundle/{name}", (source / name).read_bytes())
        for name in REQUIRED_FILES
    ]
    dated_payloads.append(("market_bundle/manifest.json", dated_manifest_bytes))
    _deterministic_tar_gz(dated_payloads, dated_archive_path)
    dated_manifest_path.write_bytes(dated_manifest_bytes)
    dated_checksum_path.write_text(
        f"{_sha256(dated_archive_path)}  {dated_archive_path.name}\n",
        encoding="utf-8",
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a public-only market-data bundle.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--public-repo-git-sha", default="UNSPECIFIED")
    return parser


def main() -> None:
    args = _parser().parse_args()
    manifest = build_market_bundle(
        args.input_dir,
        args.output_dir,
        public_repo_git_sha=args.public_repo_git_sha,
    )
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
