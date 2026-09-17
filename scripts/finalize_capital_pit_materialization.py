from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.materialization_manifest import (
    build_materialization_manifest,
    build_readiness_matrix,
    write_manifest,
)


SCHEMA_VERSION = "capital-pit-materialization-v1"


def _read_json(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Finalize deterministic identities for manual Capital/PIT materialization outputs."
    )
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--out-dir", default="output/materialization_identity")
    args = parser.parse_args()

    root = Path(args.output_root)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    capital_dir = root / "capital_input_qualification"
    financing_dir = root / "financing_materialization"
    pit_dir = root / "pit_evidence_materialization"

    capital_summary = _read_json(capital_dir / "qualification_summary.json")
    if capital_summary is None:
        raise SystemExit("capital qualification summary is required")
    financing_summary = _read_json(financing_dir / "financing_manifest.json")
    pit_summary = _read_json(pit_dir / "pit_materialization_manifest.json")

    readiness = build_readiness_matrix(
        capital_summary=capital_summary,
        financing_summary=financing_summary,
        pit_summary=pit_summary,
    )

    files: list[Path] = []
    for directory in (capital_dir, financing_dir, pit_dir):
        if directory.is_dir():
            files.extend(path for path in directory.rglob("*") if path.is_file())

    target_start = str(capital_summary["start_date"])
    target_end = str(capital_summary["end_date"])
    source_identities = [
        "SSE_ETF_SCALE_DAILY",
        "SSE_DAILY_STOCK_OVERVIEW",
        "SZSE_MARKET_OVERVIEW_DAILY",
        "SSE_MARGIN_SUMMARY",
        "SZSE_MARGIN_SUMMARY",
    ]
    if pit_summary is not None:
        source_identities.append(str(pit_summary.get("source_identity") or "CNINFO_ANNOUNCEMENT_ARCHIVE"))

    query_identities: dict[str, object] = {}
    if financing_summary is not None:
        query_identities["financing_source_query_identity"] = financing_summary.get(
            "source_query_identity"
        )
    if pit_summary is not None:
        query_identities["pit_materialization_identity"] = pit_summary.get(
            "materialization_identity"
        )

    manifest = build_materialization_manifest(
        schema_version=SCHEMA_VERSION,
        target_start=target_start,
        target_end=target_end,
        root=root,
        files=files,
        readiness_matrix=readiness,
        source_identities=source_identities,
        query_identities=query_identities,
    )
    write_manifest(out / "capital_pit_materialization_manifest.json", manifest)
    (out / "readiness_matrix.json").write_text(
        json.dumps(readiness, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
