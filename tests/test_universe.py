import pandas as pd

from tech_sentiment.universe import (
    apply_universe_membership,
    current_snapshot_to_interval,
    infer_board,
    normalize_symbol,
    universe_diagnostics,
)


def test_symbol_and_board_normalization():
    assert normalize_symbol("sh688001") == "688001"
    assert normalize_symbol(300750.0) == "300750"
    assert infer_board("688001") == "star"
    assert infer_board("300750") == "chinext"
    assert infer_board("600000") == "main"


def test_point_in_time_membership_filters_by_inclusive_ranges():
    prices = pd.DataFrame(
        {
            "date": [
                "2026-01-01",
                "2026-01-02",
                "2026-01-03",
                "2026-01-01",
                "2026-01-02",
            ],
            "symbol": ["000001", "000001", "000001", "688001", "688001"],
            "close": [10, 11, 12, 20, 21],
        }
    )
    universe = pd.DataFrame(
        {
            "symbol": ["000001", "688001"],
            "effective_start": ["2026-01-02", "2026-01-01"],
            "effective_end": ["2026-01-03", "2026-01-01"],
            "limit_pct": [10, 20],
            "universe_mode": ["point_in_time", "point_in_time"],
        }
    )

    out = apply_universe_membership(prices, universe)
    observed = list(zip(out["date"].dt.strftime("%Y-%m-%d"), out["symbol"]))

    assert observed == [
        ("2026-01-01", "688001"),
        ("2026-01-02", "000001"),
        ("2026-01-03", "000001"),
    ]
    assert out.loc[out["symbol"] == "688001", "limit_pct"].iloc[0] == 20

    diagnostics = universe_diagnostics(universe)
    assert diagnostics.has_effective_ranges is True
    assert diagnostics.has_point_in_time_history is True


def test_current_snapshot_is_explicitly_labelled_biased_mode():
    snapshot = pd.DataFrame({"symbol": ["000001", "688001"]})
    interval = current_snapshot_to_interval(
        snapshot,
        start_date="2022-01-01",
        end_date="2026-01-01",
    )
    diagnostics = universe_diagnostics(interval)

    assert set(interval["universe_mode"]) == {"current_snapshot"}
    assert diagnostics.mode == "current_snapshot"
    assert diagnostics.has_effective_ranges is True
    assert diagnostics.has_point_in_time_history is False
    assert diagnostics.symbols == 2

