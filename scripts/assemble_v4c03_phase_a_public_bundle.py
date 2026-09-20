from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pandas as pd


UNIVERSES = ("STAR50", "ChiNext50")


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_receipt(
    root: Path,
    universe: str,
    *,
    start_date: str,
    end_date: str,
    expected_sample_eligibility: bool,
) -> dict[str, object]:
    path = root / "receipt.json"
    if not path.is_file():
        raise FileNotFoundError(f"{universe} work-unit receipt missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "PUBLIC_PIT_UNIVERSE_AND_PRICES_MATERIALIZED":
        raise ValueError(f"{universe} work-unit status mismatch")
    if payload.get("universe") != universe:
        raise ValueError(f"{universe} work-unit identity mismatch")
    if payload.get("start_date") != start_date or payload.get("end_date") != end_date:
        raise ValueError(f"{universe} work-unit window mismatch")
    if bool(payload.get("sample_eligibility", True)) is not expected_sample_eligibility:
        raise ValueError(f"{universe} work-unit sample eligibility mismatch")
    if payload.get("public_only") is not True:
        raise ValueError(f"{universe} work-unit is not public-only")
    for key in (
        "private_model_semantics_present",
        "portfolio_or_holdings_data_present",
        "forward_result_computation_run",
        "production_or_trading_authority_changed",
    ):
        if payload.get(key) is not False:
            raise ValueError(f"{universe} work-unit boundary drift: {key}")
    return payload


def _copy_universe(source_root: Path, destination: Path, universe: str) -> None:
    source = source_root / universe
    if not source.is_dir():
        raise FileNotFoundError(f"{universe} work-unit directory missing")
    target = destination / universe
    if target.exists():
        raise ValueError(f"assembly target already exists: {target}")
    shutil.copytree(source, target)


def _file_inventory(root: Path) -> list[dict[str, object]]:
    rows = []
    for path in sorted(
        (p for p in root.rglob("*") if p.is_file() and p.name != "receipt.json"),
        key=lambda p: p.relative_to(root).as_posix(),
    ):
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha(path),
                "bytes": int(path.stat().st_size),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assemble reusable V4C-03 Phase A public universe work units."
    )
    parser.add_argument("--star-root", type=Path, required=True)
    parser.add_argument("--chinext-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--start-date", default="2021-06-15")
    parser.add_argument("--end-date", default="2021-12-31")
    parser.add_argument("--phase", default="A")
    parser.add_argument("--sample-eligibility", choices=("true", "false"), default="true")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    roots = {
        "STAR50": args.star_root.resolve(),
        "ChiNext50": args.chinext_root.resolve(),
    }
    expected_sample_eligibility = args.sample_eligibility == "true"
    receipts = {
        universe: _load_receipt(
            root,
            universe,
            start_date=args.start_date,
            end_date=args.end_date,
            expected_sample_eligibility=expected_sample_eligibility,
        )
        for universe, root in roots.items()
    }
    source_commits = {str(receipt["source_commit"]) for receipt in receipts.values()}
    if len(source_commits) != 1 or args.source_commit not in source_commits:
        raise ValueError("Phase A work units do not share the current source commit")

    out = args.out_dir.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Phase A assembly output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)

    for universe in UNIVERSES:
        _copy_universe(roots[universe], out, universe)

    scope_membership: dict[str, set[str]] = {}
    for universe in UNIVERSES:
        membership = pd.read_csv(
            out / universe / "universe_point_in_time.csv",
            dtype={"symbol": str},
        )
        for symbol in membership["symbol"].astype(str).str.zfill(6):
            scope_membership.setdefault(symbol, set()).add(universe)
    scope = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "market": "SH" if symbol.startswith(("6", "9")) else "SZ",
                "historical_scope": ";".join(sorted(universes)),
            }
            for symbol, universes in sorted(scope_membership.items())
        ]
    )
    scope_dir = out / "pit_symbol_scope"
    scope_dir.mkdir(parents=True, exist_ok=True)
    scope.to_csv(scope_dir / "capital_pit_symbols.csv", index=False)

    lineage_dir = out / "lineage"
    lineage_dir.mkdir(parents=True, exist_ok=True)
    for universe in UNIVERSES:
        source_receipt = roots[universe] / "receipt.json"
        shutil.copy2(source_receipt, lineage_dir / f"{universe}_work_unit_receipt.json")

    receipt = {
        "schema_version": "v4c03-phase-a-public-assembly-receipt-v1",
        "status": "PUBLIC_PHASE_A_ASSEMBLED" if expected_sample_eligibility else "PUBLIC_PHASE_A_WARMUP_ASSEMBLED",
        "source_commit": args.source_commit,
        "phase": args.phase,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "sample_eligibility": expected_sample_eligibility,
        "universes": {
            universe: receipts[universe]["universes"][universe]
            for universe in UNIVERSES
        },
        "symbol_scope": {
            "symbols": int(len(scope)),
            "sh_symbols": int(scope["market"].eq("SH").sum()),
            "sz_symbols": int(scope["market"].eq("SZ").sum()),
        },
        "work_unit_receipt_sha256": {
            universe: _sha(roots[universe] / "receipt.json")
            for universe in UNIVERSES
        },
        "files": _file_inventory(out),
        "public_only": True,
        "private_model_semantics_present": False,
        "portfolio_or_holdings_data_present": False,
        "forward_result_computation_run": False,
        "production_or_trading_authority_changed": False,
    }
    (out / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
