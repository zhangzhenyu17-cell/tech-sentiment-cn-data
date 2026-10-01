from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pandas as pd

from tech_sentiment.cross_sector_fundamental_date_increment_v1 import (
    FILES, FALSE_FLAGS, GROUPS, VERSION, _digest, _require, build_scope,
    capture_increment, load_contract, probe_document, unit_symbols,
    validate_capture, write_capture,
)

CONTRACT = "reference/cross_sector_fundamental_date_increment_v1.json"
PRODUCER_PATHS = (CONTRACT, "scripts/cross_sector_fundamental_date_increment_v1.py",
                  "src/tech_sentiment/cross_sector_fundamental_date_increment_v1.py")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded public filing-date increment; no private qualification.")
    parser.add_argument("phase", choices=("prepare", "pilot", "capture", "finalize"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--shared-dir", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--unit-index", type=int)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--work-unit-root", type=Path)
    parser.add_argument("--resume-root", type=Path)
    args = parser.parse_args(argv)
    contract_path = args.root / CONTRACT
    contract = load_contract(args.root, contract_path)
    contract_sha = _digest(contract_path)
    producer_sha = sha256(json.dumps({p: _digest(args.root / p) for p in PRODUCER_PATHS}, sort_keys=True).encode()).hexdigest()
    scope, memberships = build_scope(args.root, contract)
    shared = args.shared_dir
    scope_bytes = scope.to_csv(index=False).encode("utf-8")
    scope_sha = sha256(scope_bytes).hexdigest()
    shared_files = ("scope.csv", "trading_calendar.csv", *(f"membership_{group}.csv" for group in GROUPS))
    if args.phase == "prepare":
        _require((shared / "trading_calendar.csv").is_file(), "exact trading calendar missing")
        shared.mkdir(parents=True, exist_ok=True)
        (shared / "scope.csv").write_bytes(scope_bytes)
        for group, frame in memberships.items():
            frame.to_csv(shared / f"membership_{group}.csv", index=False)
        payload = {"source_commit": args.source_commit, "contract_sha256": contract_sha,
                   "producer_sha256": producer_sha, "scope_sha256": scope_sha,
                   "file_sha256": {name: _digest(shared / name) for name in shared_files}}
        (shared / "shared_manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"phase": "prepare", "scope_entities": len(scope), "scope_sha256": scope_sha}))
        return 0
    shared_manifest = json.loads((shared / "shared_manifest.json").read_text(encoding="utf-8"))
    for key, value in (("source_commit", args.source_commit), ("contract_sha256", contract_sha), ("producer_sha256", producer_sha), ("scope_sha256", scope_sha)):
        _require(shared_manifest[key] == value, f"shared input identity drift: {key}")
    _require(set(shared_manifest["file_sha256"]) == set(shared_files), "shared file inventory drift")
    for name in shared_files:
        _require(_digest(shared / name) == shared_manifest["file_sha256"][name], "shared file digest drift")
    _require((shared / "scope.csv").read_bytes() == scope_bytes, "shared scope differs from frozen memberships")
    calendar = pd.read_csv(shared / "trading_calendar.csv")["date"]
    common = dict(source_commit=args.source_commit, contract_sha256=contract_sha,
                  scope_sha256=scope_sha, producer_sha256=producer_sha)
    if args.phase in {"pilot", "capture"}:
        _require(args.checkpoint_dir is not None, "checkpoint path required")
        if args.phase == "pilot":
            probe = probe_document(contract["pilot_document"])
            (shared / "pilot_transport_receipt.json").write_text(json.dumps(probe, indent=2) + "\n", encoding="utf-8")
            symbols = sorted({str(frame.iloc[0]["symbol"]) for frame in memberships.values()})
            out, index, pilot_checkpoint = shared / "pilot", -1, None
        else:
            _require(args.unit_index is not None and args.out_dir is not None, "work unit/output required")
            symbols = unit_symbols(scope, args.unit_index)
            out, index, pilot_checkpoint = args.out_dir, args.unit_index, shared / "pilot_checkpoint"
            if args.resume_root is not None and args.resume_root.exists():
                candidates = sorted(args.resume_root.rglob("stage_manifest.json"))
                previous = []
                for path in candidates:
                    manifest = validate_capture(path.parent, symbols=symbols, unit_index=index, **common)
                    previous.append((path.parent, manifest))
                if previous:
                    _require(all(item[1]["file_sha256"] == previous[0][1]["file_sha256"] for item in previous), "resumed immutable unit content conflict")
                    out.mkdir(parents=True, exist_ok=True)
                    for name in (*FILES, "stage_manifest.json"):
                        shutil.copyfile(previous[0][0] / name, out / name)
                    print(json.dumps({"unit_index": index, "immutable_unit_reused": True, "provider_queries_executed": 0, "documents_executed": 0}))
                    return 0
        result = capture_increment(symbols=symbols, contract=contract, trading_dates=calendar,
            source_commit=args.source_commit, checkpoint_dir=args.checkpoint_dir, pilot_checkpoint_dir=pilot_checkpoint)
        manifest = write_capture(result, out, symbols=symbols, unit_index=index, **common)
        print(json.dumps({"phase": args.phase, "complete": manifest["complete"], "soft_parse_gaps": manifest["soft_parse_gaps"]}))
        return 0 if result["complete"] else 2
    _require(args.work_unit_root is not None and args.out_dir is not None, "finalizer inputs/output required")
    by_unit = {}
    for path in sorted(args.work_unit_root.rglob("stage_manifest.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        index = value.get("unit_index")
        _require(type(index) is int and 0 <= index < 16, "finalizer unexpected unit identity")
        manifest = validate_capture(path.parent, symbols=unit_symbols(scope, index), unit_index=index, **common)
        if index in by_unit:
            _require(by_unit[index][1]["file_sha256"] == manifest["file_sha256"], "duplicate immutable unit conflict")
        else:
            by_unit[index] = (path.parent, manifest)
    _require(set(by_unit) == set(range(16)), "finalizer missing immutable work unit")
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        frames = [pd.read_csv(by_unit[index][0] / name, dtype={"symbol": str, "document_id": str}) for index in range(16)]
        frame = pd.concat(frames, ignore_index=True)
        if name == "entity_window_receipts.csv":
            _require(sorted(frame["symbol"]) == sorted(scope["symbol"]) and not frame["symbol"].duplicated().any(), "final entity scope incomplete")
        if name == "versioned_filing_facts.csv" and len(frame):
            _require(not frame.duplicated(["entity_id", "document_id", "revision_id", "fact_type", "period_end"]).any(), "final fact duplicates")
            _require(pd.to_datetime(frame["evidence_available_date"]).eq(pd.Timestamp(contract["target_date"])).all(), "final fact availability drift")
        frame.to_csv(out / name, index=False)
    for name in shared_files:
        shutil.copyfile(shared / name, out / name)
    shutil.copyfile(contract_path, out / "capture_contract.json")
    allowlist = (*FILES, *shared_files, "capture_contract.json")
    payload = {"schema_version": VERSION, "status": "PUBLIC_DISCLOSURE_INCREMENT_CAPTURED_PRIVATE_INTAKE_REQUIRED",
               **common, "scope_unique_entities": len(scope), "unit_count": 16, "target_date": contract["target_date"],
               "baseline_cutoff_date": contract["baseline_cutoff_date"], "publication_window": contract["publication_window"],
               "file_sha256": {name: _digest(out / name) for name in allowlist},
               "unit_receipts": [{"unit_index": index, "manifest_sha256": _digest(by_unit[index][0] / "stage_manifest.json"),
                                  "file_sha256": by_unit[index][1]["file_sha256"]} for index in range(16)],
               "soft_parse_gaps": sum(item[1]["soft_parse_gaps"] for item in by_unit.values()),
               "baseline_reuse_plan": contract["baseline_reuse_plan"], **{flag: False for flag in FALSE_FLAGS}}
    (out / "bundle_manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"phase": "finalize", "status": payload["status"], "entities": len(scope), "private_qualification_granted": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
