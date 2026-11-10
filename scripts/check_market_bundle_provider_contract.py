"""Check the installed AkShare call contract before starting a bundle download."""

from inspect import signature

import akshare


def main() -> None:
    shared = {
        "start_date": "2026-11-09",
        "end_date": "2026-11-10",
        "adjust": "qfq",
        "timeout": 15.0,
    }
    calls = (
        (akshare.stock_zh_a_hist_tx, {"symbol": "sh688981", **shared}),
        (akshare.stock_zh_a_hist, {"symbol": "688981", "period": "daily", **shared}),
    )
    failures = []
    for endpoint, kwargs in calls:
        try:
            signature(endpoint).bind(**kwargs)
        except TypeError as exc:
            failures.append(f"{endpoint.__name__}: {exc}")
    if failures:
        raise SystemExit(
            f"MARKET_BUNDLE_PROVIDER_CONTRACT_MISMATCH: AkShare {akshare.__version__}: "
            + "; ".join(failures)
        )
    print(f"Market bundle stock-history call contract verified: AkShare {akshare.__version__}")


if __name__ == "__main__":
    main()
