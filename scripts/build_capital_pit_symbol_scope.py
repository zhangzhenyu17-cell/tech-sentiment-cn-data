from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


DEFAULT_INPUTS = (
    # 2026-06-16 is an in-window carry-in anchor. It is required because
    # 688065 entered STAR50 before the 2022 adjustment file begins and only
    # exited on 2026-09-14. Using the post-exit 2026-09-14 anchor plus
    # 2022+ adjustments alone silently drops this exact frozen member.
    ("STAR50", "data/reference/kc50_anchor_2026-06-16.csv", "symbol"),
    ("STAR50", "data/reference/kc50_anchor_2026-09-14.csv", "symbol"),
    ("STAR50", "data/reference/kc50_adjustments_2022_2026.csv", "out_symbol"),
    ("STAR50", "data/reference/kc50_adjustments_2022_2026.csv", "in_symbol"),
    ("CHINEXT50", "data/reference/chinext50_anchor_2026-06-15.csv", "symbol"),
    ("CHINEXT50", "data/reference/chinext50_adjustments_2022_2026.csv", "out_symbol"),
    ("CHINEXT50", "data/reference/chinext50_adjustments_2022_2026.csv", "in_symbol"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _market(symbol: str) -> str:
    if symbol.startswith(("5", "6", "9")):
        return "SH"
    if symbol.startswith(("0", "1", "2", "3")):
        return "SZ"
    if symbol.startswith(("4", "8")):
        return "BJ"
    return "UNKNOWN"


def build_scope(root: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    membership: dict[str, set[str]] = {}
    input_files: dict[str, str] = {}
    for scope_name, relative, column in DEFAULT_INPUTS:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"frozen symbol-scope input missing: {relative}")
        frame = pd.read_csv(path, dtype=str)
        if column not in frame.columns:
            raise ValueError(f"{relative} missing symbol column {column}")
        values = frame[column].dropna().astype(str).str.extract(r"(\d{6})", expand=False).dropna()
        for symbol in values:
            membership.setdefault(str(symbol), set()).add(scope_name)
        input_files[relative] = _sha256(path)
    if not membership:
        raise ValueError("frozen Capital/PIT symbol scope is empty")
    rows = []
    for symbol in sorted(membership):
        rows.append(
            {
                "symbol": symbol,
                "market": _market(symbol),
                "historical_scope": ";".join(sorted(membership[symbol])),
            }
        )
    scope = pd.DataFrame(rows)
    if scope["market"].eq("UNKNOWN").any():
        bad = scope.loc[scope["market"].eq("UNKNOWN"), "symbol"].tolist()
        raise ValueError(f"unknown market symbols in frozen scope: {bad}")
    summary = {
        "scope_version": "capital-pit-frozen-universe-v2",
        "symbols": int(len(scope)),
        "sh_symbols": int(scope["market"].eq("SH").sum()),
        "sz_symbols": int(scope["market"].eq("SZ").sum()),
        "bj_symbols": int(scope["market"].eq("BJ").sum()),
        "input_file_sha256": dict(sorted(input_files.items())),
        "new_universe_created": False,
        "scope_semantics": "UNION_OF_ALREADY_FROZEN_STAR50_AND_CHINEXT50_HISTORICAL_MEMBERS",
    }
    canonical = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    summary["scope_identity"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return scope, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the frozen, non-researched Capital/PIT symbol scope.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--out-dir", default="output/pit_symbol_scope")
    args = parser.parse_args()
    scope, summary = build_scope(Path(args.repo_root))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    scope.to_csv(out / "capital_pit_symbols.csv", index=False)
    (out / "capital_pit_symbol_scope.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
