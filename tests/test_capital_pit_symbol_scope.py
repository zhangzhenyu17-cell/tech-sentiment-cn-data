from __future__ import annotations

from pathlib import Path

from scripts.build_capital_pit_symbol_scope import build_scope


ROOT = Path(__file__).resolve().parents[1]


def test_v4a_scope_includes_pre2022_star50_carryin_688065() -> None:
    scope, summary = build_scope(ROOT)
    row = scope.loc[scope["symbol"].eq("688065")]
    assert len(row) == 1
    assert row.iloc[0]["market"] == "SH"
    assert "STAR50" in row.iloc[0]["historical_scope"]
    assert summary["scope_version"] == "capital-pit-frozen-universe-v2"
    assert "data/reference/kc50_anchor_2026-06-16.csv" in summary["input_file_sha256"]


def test_scope_correction_is_same_frozen_universes_not_scope_expansion() -> None:
    scope, summary = build_scope(ROOT)
    assert set(scope["historical_scope"].str.split(";").explode()) <= {"STAR50", "CHINEXT50"}
    assert summary["new_universe_created"] is False
    assert summary["bj_symbols"] == 0
    # Prior V1 had 192 symbols and omitted only the frozen STAR50 carry-in 688065.
    assert summary["symbols"] == 193
    assert summary["sh_symbols"] == 103
    assert summary["sz_symbols"] == 90
