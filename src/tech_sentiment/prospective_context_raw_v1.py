from __future__ import annotations

import hashlib
import json
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from .capital_input_data import (
    fetch_sse_etf_share_history,
    fetch_sse_szse_a_share_turnover_history,
    qualify_trailing_etf_coverage,
)
from .data_akshare import download_universe_history, fetch_current_csindex_universe
from .immutable_checkpoint import ImmutableCheckpointStore
from .index_history import read_adjustments_csv, read_anchor_csv, reconstruct_index_history
from .index_price import fetch_index_history
from .production_universe import active_symbols_on
from .prospective_context_checkpoint_v1 import (
    semantic_fingerprint,
    universe_checkpoint_identity,
)
from .prospective_preopen_timing_v2 import validate_preopen_capture_window
from .resumable_capital import (
    expected_capital_checkpoint_identities,
    expected_szse_etf_checkpoint_identities,
    materialize_capital_monthly,
    materialize_szse_etf_monthly,
)
from .v4c03_szse_etf_shares import fetch_szse_etf_share_history


CONTRACT_ID = "prospective_context_raw_v1"
RECEIPT_NAME = "PUBLIC_RAW_CONTEXT_RECEIPT.json"


@dataclass(frozen=True)
class CaptureResult:
    output_root: Path
    receipt: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("contract_id") != CONTRACT_ID:
        raise ValueError("unexpected prospective raw-context contract")
    if payload.get("status") != "FROZEN_PUBLIC_DATA_SCOPE":
        raise ValueError("prospective raw-context contract is not frozen public scope")
    firewall = payload.get("privacy_and_research_firewall") or {}
    for key in (
        "private_model_semantics_allowed",
        "private_thresholds_or_signals_allowed",
        "portfolio_or_holdings_data_allowed",
        "forward_outcomes_allowed",
        "research_result_allowed",
        "evidence_tier_allowed",
    ):
        if firewall.get(key) is not False:
            raise ValueError(f"public raw-context firewall drifted: {key}")
    if (payload.get("workflow") or {}).get("automatic_trigger_allowed") is not False:
        raise ValueError("public raw-context workflow may not add automatic triggers")
    return payload


def _client(client: Any | None = None) -> Any:
    if client is not None:
        return client
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "AKShare is required for prospective public raw capture"
        ) from exc
    return ak


def capture_trading_dates(
    operation_date: str,
    *,
    warmup_trading_days: int,
    client: Any | None = None,
) -> pd.DatetimeIndex:
    if warmup_trading_days < 313:
        raise ValueError("public warm-up must retain at least 313 trading days")
    ak = _client(client)
    raw = ak.tool_trade_date_hist_sina()
    if raw is None or len(raw) == 0:
        raise RuntimeError("A-share trading calendar source returned no rows")
    column = "trade_date" if "trade_date" in raw.columns else raw.columns[0]
    dates = (
        pd.DatetimeIndex(pd.to_datetime(raw[column], errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    target = pd.Timestamp(operation_date).normalize()
    eligible = dates[dates <= target]
    if target not in set(eligible):
        raise ValueError("operation_date is not a confirmed A-share trading day")
    if len(eligible) < warmup_trading_days:
        raise ValueError("insufficient trading-calendar history for frozen warm-up")
    return eligible[-warmup_trading_days:]


def _stamp_capture(
    frame: pd.DataFrame,
    *,
    capture_date: str,
) -> pd.DataFrame:
    """Stamp canonical forward-capture semantics without volatile run time.

    Exact wall-clock provenance lives in immutable GitHub Actions run metadata.
    Keeping it out of canonical CSV bytes makes same-day exact retries
    byte-deterministic while still requiring operation_date == actual Shanghai
    capture date before any data is written.
    """
    out = frame.copy()
    out["forward_capture_date"] = capture_date
    out["historical_replay_allowed"] = False
    return out


def _validate_live_snapshot_exact(
    *,
    membership: pd.DataFrame,
    live_snapshot: pd.DataFrame,
    operation_date: str,
    expected_constituents: int,
    universe: str,
) -> tuple[set[str], set[str]]:
    active = active_symbols_on(membership, pd.Timestamp(operation_date))
    live = set(live_snapshot["symbol"].astype(str).str.zfill(6))
    if len(active) != expected_constituents:
        raise ValueError(
            f"{universe} reconstructed active membership size {len(active)} "
            f"!= {expected_constituents}"
        )
    if len(live) != expected_constituents:
        raise ValueError(
            f"{universe} live snapshot size {len(live)} != {expected_constituents}"
        )
    if active != live:
        missing = sorted(active - live)
        extra = sorted(live - active)
        raise ValueError(
            f"{universe} live snapshot does not exactly match reconstructed PIT "
            f"membership; missing={missing[:5]} extra={extra[:5]}"
        )
    return active, live


def _validate_window_symbol_coverage(
    *,
    prices: pd.DataFrame,
    membership: pd.DataFrame,
    minimum_coverage: float,
    universe: str,
) -> float:
    if prices.empty:
        raise ValueError(f"{universe} constituent price history is empty")
    expected = set(membership["symbol"].astype(str).str.zfill(6))
    seen = set(prices["symbol"].astype(str).str.zfill(6))
    coverage = len(seen & expected) / len(expected) if expected else 0.0
    if coverage < minimum_coverage:
        raise ValueError(
            f"{universe} constituent-price symbol coverage {coverage:.3f} "
            f"< {minimum_coverage:.3f}"
        )
    return float(coverage)


def _require_operation_date_row(
    frame: pd.DataFrame,
    *,
    operation_date: str,
    label: str,
    date_column: str = "date",
) -> None:
    if date_column not in frame.columns:
        raise ValueError(f"{label} missing {date_column}")
    dates = pd.to_datetime(frame[date_column], errors="raise").dt.normalize()
    if pd.Timestamp(operation_date).normalize() not in set(dates):
        raise ValueError(f"{label} lacks exact operation-date observation")


def _classify_same_day_publication_readiness(
    *,
    missing: list[str],
    diagnostics: dict[str, Any],
) -> str:
    """Separate ordinary publication lag from transport/schema failures.

    This classification is diagnostic only. It never permits stale substitution,
    forward-fill, interpolation, alternate dates, or evidence qualification.
    """
    detail = json.dumps(
        diagnostics,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    expected_unpublished = {
        "588000": "NO_MATCHING_ETF_ROW",
        "159915": "SZSE ETF source returned no rows",
    }
    if missing and all(
        source in expected_unpublished
        and expected_unpublished[source] in detail
        for source in missing
    ):
        return "NOT_YET_PUBLISHED"
    return "SOURCE_FAILURE_OR_INCOMPLETE"


def _validate_same_day_capital_preflight(
    *,
    operation_date: str,
    sse_etf_data: pd.DataFrame,
    szse_etf_data: pd.DataFrame,
    turnover_data: pd.DataFrame,
    diagnostics: dict[str, Any] | None = None,
) -> None:
    """Fail fast when same-day public capital inputs are not yet published.

    This is only a freshness gate. It does not evaluate trailing coverage or
    evidence qualification; the full canonical materialization still reruns
    and applies the frozen 60-day / 80% rules later.
    """
    target = pd.Timestamp(operation_date).normalize()
    missing: list[str] = []

    def _has_fund(frame: pd.DataFrame, code: str) -> bool:
        if frame.empty or not {"date", "fund_code"} <= set(frame.columns):
            return False
        dates = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        codes = frame["fund_code"].astype(str).str.extract(r"(\d+)", expand=False).str.zfill(6)
        return bool(((dates == target) & (codes == code)).any())

    if not _has_fund(sse_etf_data, "588000"):
        missing.append("588000")
    if not _has_fund(szse_etf_data, "159915"):
        missing.append("159915")

    turnover_ok = False
    if not turnover_data.empty and "date" in turnover_data.columns:
        turnover_dates = pd.to_datetime(
            turnover_data["date"], errors="coerce"
        ).dt.normalize()
        turnover_ok = bool((turnover_dates == target).any())
    if not turnover_ok:
        missing.append("SSE_SZSE_TURNOVER")

    if missing:
        diagnostic_payload = diagnostics or {}
        detail = json.dumps(
            diagnostic_payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        readiness_class = _classify_same_day_publication_readiness(
            missing=missing,
            diagnostics=diagnostic_payload,
        )
        raise ValueError(
            "SAME_DAY_PUBLIC_SOURCE_NOT_READY: "
            f"readiness_class={readiness_class} "
            f"operation_date={operation_date} missing={missing}; diagnostics={detail}"
        )


def _same_day_capital_preflight(
    *,
    operation_date: str,
    client: Any | None = None,
    publication_retry_attempts: int = 1,
    publication_retry_backoff_seconds: float = 0.0,
    sleeper: Any = time.sleep,
    sse_history_fetcher: Any = fetch_sse_etf_share_history,
    szse_history_fetcher: Any = fetch_szse_etf_share_history,
    turnover_history_fetcher: Any = fetch_sse_szse_a_share_turnover_history,
) -> None:
    if publication_retry_attempts < 1:
        raise ValueError("publication_retry_attempts must be >= 1")
    if publication_retry_backoff_seconds < 0:
        raise ValueError("publication_retry_backoff_seconds must be >= 0")

    target_dates = pd.DatetimeIndex([pd.Timestamp(operation_date).normalize()])
    last_error: ValueError | None = None

    for attempt in range(1, publication_retry_attempts + 1):
        sse = sse_history_fetcher(
            trading_dates=target_dates,
            fund_codes=["588000"],
            sleep_seconds=0,
        )
        szse = szse_history_fetcher(
            start_date=operation_date,
            end_date=operation_date,
            trading_dates=target_dates,
            fund_codes=["159915"],
            sleep_seconds=0,
            client=client,
        )
        turnover = turnover_history_fetcher(
            trading_dates=target_dates,
            sleep_seconds=0,
        )

        try:
            _validate_same_day_capital_preflight(
                operation_date=operation_date,
                sse_etf_data=sse.data,
                szse_etf_data=szse.data,
                turnover_data=turnover.combined,
                diagnostics={
                    "sse_588000_errors": sse.errors.to_dict("records"),
                    "szse_159915_errors": szse.errors.to_dict("records"),
                    "turnover_errors": turnover.errors.to_dict("records"),
                    "publication_probe_attempt": attempt,
                    "publication_probe_attempts": publication_retry_attempts,
                },
            )
            return
        except ValueError as exc:
            last_error = exc
            if "readiness_class=NOT_YET_PUBLISHED" not in str(exc):
                raise
            if attempt >= publication_retry_attempts:
                break
            sleeper(publication_retry_backoff_seconds)

    assert last_error is not None
    raise ValueError(
        f"{last_error}; publication_probes_exhausted={publication_retry_attempts}"
    ) from last_error


def _same_day_capital_checkpoint_preflight_ready(
    *,
    repo_root: Path,
    checkpoint_dir: Path,
    trading_dates: pd.DatetimeIndex,
    operation_date: str,
) -> bool:
    """Return True only when exact same-capture durable chunks prove freshness.

    This never bridges operation dates. capture_date is part of every capital
    checkpoint identity, so a prior day cannot suppress today's live-source
    freshness gate.
    """
    capital_revision = str(
        semantic_fingerprint(repo_root, family="capital:sse")[
            "checkpoint_revision"
        ]
    )
    szse_revision = str(
        semantic_fingerprint(repo_root, family="capital:szse")[
            "checkpoint_revision"
        ]
    )
    sse_expected = expected_capital_checkpoint_identities(
        trading_dates=trading_dates,
        fund_codes=["588000"],
        checkpoint_revision=capital_revision,
        capture_date=operation_date,
    )
    szse_expected = expected_szse_etf_checkpoint_identities(
        trading_dates=trading_dates,
        fund_codes=["159915"],
        checkpoint_revision=szse_revision,
        capture_date=operation_date,
    )

    target = pd.Timestamp(operation_date).normalize()
    sse_store = ImmutableCheckpointStore(checkpoint_dir / "sse")
    szse_store = ImmutableCheckpointStore(checkpoint_dir / "szse")
    etf_data = pd.DataFrame()
    turnover_data = pd.DataFrame()
    szse_data = pd.DataFrame()

    for _store_name, identity in sse_expected:
        if pd.Timestamp(identity.scope["end_date"]).normalize() != target:
            continue
        loaded = sse_store.load(identity)
        if loaded is None:
            return False
        if identity.producer == "sse-etf-share-history":
            etf_data = loaded.frames["data"]
        elif identity.producer == "sse-szse-a-share-turnover-history":
            turnover_data = loaded.frames["combined"]

    for _store_name, identity in szse_expected:
        if pd.Timestamp(identity.scope["end_date"]).normalize() != target:
            continue
        loaded = szse_store.load(identity)
        if loaded is None:
            return False
        szse_data = loaded.frames["data"]

    if etf_data.empty or turnover_data.empty or szse_data.empty:
        return False
    _validate_same_day_capital_preflight(
        operation_date=operation_date,
        sse_etf_data=etf_data,
        szse_etf_data=szse_data,
        turnover_data=turnover_data,
        diagnostics={"source": "EXACT_SAME_CAPTURE_IMMUTABLE_CHECKPOINT"},
    )
    return True

def _file_record(root: Path, path: Path) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    return {
        "path": rel,
        "bytes": int(path.stat().st_size),
        "sha256": _sha256(path),
    }


def _write_csv(
    frame: pd.DataFrame,
    path: Path,
    *,
    capture_date: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _stamp_capture(
        frame,
        capture_date=capture_date,
    ).to_csv(path, index=False, date_format="%Y-%m-%d")


def materialize_public_raw_capture(
    *,
    repo_root: Path,
    contract_path: Path,
    operation_date: str,
    source_commit: str,
    output_root: Path,
    checkpoint_dir: Path,
    captured_at: datetime | None = None,
    client: Any | None = None,
    timing_mode: str = "SAME_DAY_V1",
    decision_date: str | None = None,
) -> CaptureResult:
    contract = _read_contract(contract_path)
    now = captured_at or datetime.now(ZoneInfo("UTC"))
    if now.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    capture_date = now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    timing_receipt: dict[str, Any] | None = None
    checkpoint_capture_date = capture_date
    if timing_mode == "SAME_DAY_V1":
        if operation_date != capture_date:
            raise ValueError(
                "forward raw capture rejects historical/future operation dates; "
                f"operation_date={operation_date} capture_date={capture_date}"
            )
    elif timing_mode == "PREOPEN_DUAL_CLOCK_V2":
        if not decision_date:
            raise ValueError("PREOPEN_DUAL_CLOCK_V2 requires decision_date")
        timing_receipt = validate_preopen_capture_window(
            market_session_date=operation_date,
            decision_date=decision_date,
            captured_at=now,
            client=_client(client),
        )
        checkpoint_capture_date = operation_date
    else:
        raise ValueError(f"unsupported capture timing_mode: {timing_mode}")
    warmup_days = int(contract["warmup"]["trading_days"])
    trading_dates = capture_trading_dates(
        operation_date,
        warmup_trading_days=warmup_days,
        client=client,
    )
    start_date = str(pd.Timestamp(trading_dates.min()).date())
    end_date = operation_date
    output_root.mkdir(parents=True, exist_ok=True)

    calendar = pd.DataFrame({"date": trading_dates})
    _write_csv(
        calendar,
        output_root / "trading_calendar.csv",
        capture_date=capture_date,
    )

    # Prefer an exact same-capture durable checkpoint over touching the
    # provider again. Otherwise fail fast before heavy history downloads when
    # same-day capital sources have not yet published.
    freshness_from_checkpoint = _same_day_capital_checkpoint_preflight_ready(
        repo_root=repo_root,
        checkpoint_dir=checkpoint_dir,
        trading_dates=trading_dates,
        operation_date=operation_date,
    )
    if not freshness_from_checkpoint:
        _same_day_capital_preflight(
            operation_date=operation_date,
            client=client,
            publication_retry_attempts=(
                4 if timing_mode == "PREOPEN_DUAL_CLOCK_V2" else 1
            ),
            publication_retry_backoff_seconds=(
                20.0 if timing_mode == "PREOPEN_DUAL_CLOCK_V2" else 0.0
            ),
        )

    universe_receipts: dict[str, Any] = {}
    minimum_coverage = float(
        contract["quality"][
            "minimum_constituent_symbol_coverage_over_capture_window"
        ]
    )

    # Restore exact same-capture immutable universe work units before touching
    # the network. Missing units alone proceed to live witness + history fetch.
    universe_store = ImmutableCheckpointStore(checkpoint_dir / "universe")
    completed_universes: dict[str, dict[str, Any]] = {}
    pending_universes: dict[str, dict[str, Any]] = {}

    for universe, cfg in contract["universes"].items():
        identity = universe_checkpoint_identity(
            repo_root,
            universe=universe,
            index_code=cfg["index_code"],
            operation_date=operation_date,
            start_date=start_date,
            trading_dates=trading_dates,
        )
        loaded = universe_store.load(identity)
        if loaded is not None:
            metadata = dict(loaded.receipt.get("metadata") or {})
            completed_universes[universe] = {
                "cfg": cfg,
                "identity": identity,
                "membership": loaded.frames["membership"],
                "segments": loaded.frames["segments"],
                "live": loaded.frames["live"],
                "prices": loaded.frames["prices"],
                "download_errors": loaded.frames["download_errors"],
                "index_prices": loaded.frames["index_prices"],
                "reconstruction": dict(metadata.get("reconstruction") or {}),
                "resumed": True,
            }
            continue

        anchor_path = repo_root / cfg["anchor_path"]
        adjustments_path = repo_root / cfg["adjustments_path"]
        anchor = read_anchor_csv(anchor_path)
        adjustments = read_adjustments_csv(adjustments_path)
        membership, segments, diagnostics = reconstruct_index_history(
            anchor,
            adjustments,
            history_start=start_date,
            history_end=end_date,
            anchor_effective_date=cfg["anchor_effective_date"],
            expected_constituents=int(cfg["expected_constituents"]),
            index_code=cfg["index_code"],
        )
        pending_universes[universe] = {
            "cfg": cfg,
            "identity": identity,
            "membership": membership,
            "segments": segments,
            "diagnostics": diagnostics,
        }

    live_all = pd.DataFrame()
    if pending_universes:
        live_all = fetch_current_csindex_universe(
            [
                prepared["cfg"]["index_code"]
                for prepared in pending_universes.values()
            ],
            retries=3,
            retry_backoff_seconds=1.5,
            client=client,
        )
        if live_all.empty:
            raise ValueError(
                "pending universe live constituent snapshot is empty"
            )

    for universe, prepared in pending_universes.items():
        cfg = prepared["cfg"]
        membership = prepared["membership"]
        segments = prepared["segments"]
        diagnostics = prepared["diagnostics"]
        index_code = str(cfg["index_code"]).zfill(6)
        live = live_all[
            live_all["source_index"].astype(str).str.split(",").map(
                lambda values: index_code in values
            )
        ].copy()
        if live.empty:
            raise ValueError(f"{universe} live constituent snapshot is empty")
        _validate_live_snapshot_exact(
            membership=membership,
            live_snapshot=live,
            operation_date=operation_date,
            expected_constituents=int(cfg["expected_constituents"]),
            universe=universe,
        )

        downloaded = download_universe_history(
            membership,
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
            providers=("tencent", "eastmoney"),
            retries=1,
            retry_backoff_seconds=0.5,
            sleep_seconds=0.05,
            timeout_seconds=15.0,
            client=client,
        )
        index_prices = fetch_index_history(
            cfg["index_code"],
            start_date=start_date,
            end_date=end_date,
            client=client,
        )
        _require_operation_date_row(
            index_prices,
            operation_date=operation_date,
            label=f"{universe} official index rail",
        )
        _validate_window_symbol_coverage(
            prices=downloaded.prices,
            membership=membership,
            minimum_coverage=minimum_coverage,
            universe=universe,
        )
        reconstruction = {
            "history_start": str(diagnostics.history_start.date()),
            "history_end": str(diagnostics.history_end.date()),
            "adjustment_dates": int(diagnostics.adjustment_dates),
            "adjustment_rows": int(diagnostics.adjustment_rows),
            "segments": int(diagnostics.segments),
            "unique_symbols": int(diagnostics.unique_symbols),
        }
        universe_store.save(
            prepared["identity"],
            frames={
                "membership": membership,
                "segments": segments,
                "live": live,
                "prices": downloaded.prices,
                "download_errors": downloaded.errors,
                "index_prices": index_prices,
            },
            metadata={
                "universe": universe,
                "actual_source_commit": source_commit,
                "operation_date": operation_date,
                "reconstruction": reconstruction,
                # Only transport-complete universe stages become permanent
                # release assets. A 95% canonical coverage pass with transient
                # download errors may still finish this capture, but is not
                # promoted to a durable reusable work unit.
                "permanent_reuse_eligible": bool(downloaded.errors.empty),
            },
        )
        completed_universes[universe] = {
            "cfg": cfg,
            "identity": prepared["identity"],
            "membership": membership,
            "segments": segments,
            "live": live,
            "prices": downloaded.prices,
            "download_errors": downloaded.errors,
            "index_prices": index_prices,
            "reconstruction": reconstruction,
            "resumed": False,
        }

    runtime_universe: dict[str, Any] = {}
    for universe, prepared in completed_universes.items():
        cfg = prepared["cfg"]
        membership = prepared["membership"]
        segments = prepared["segments"]
        live = prepared["live"]
        prices = prepared["prices"]
        download_errors = prepared["download_errors"]
        index_prices = prepared["index_prices"]
        reconstruction = prepared["reconstruction"]

        active, _ = _validate_live_snapshot_exact(
            membership=membership,
            live_snapshot=live,
            operation_date=operation_date,
            expected_constituents=int(cfg["expected_constituents"]),
            universe=universe,
        )
        _require_operation_date_row(
            index_prices,
            operation_date=operation_date,
            label=f"{universe} official index rail",
        )
        coverage = _validate_window_symbol_coverage(
            prices=prices,
            membership=membership,
            minimum_coverage=minimum_coverage,
            universe=universe,
        )

        root = output_root / universe
        _write_csv(
            membership,
            root / "universe_point_in_time.csv",
            capture_date=capture_date,
        )
        _write_csv(
            segments,
            root / "universe_segments.csv",
            capture_date=capture_date,
        )
        _write_csv(
            live,
            root / "universe_live_snapshot.csv",
            capture_date=capture_date,
        )
        _write_csv(
            prices,
            root / "prices.csv",
            capture_date=capture_date,
        )
        _write_csv(
            download_errors,
            root / "download_errors.csv",
            capture_date=capture_date,
        )
        _write_csv(
            index_prices,
            root / "index_prices.csv",
            capture_date=capture_date,
        )
        universe_receipts[universe] = {
            "index_code": cfg["index_code"],
            "anchor_effective_date": cfg["anchor_effective_date"],
            "active_constituents": len(active),
            "live_snapshot_exact_match": True,
            "live_snapshot_source_variants": sorted(
                set(live["snapshot_source"].astype(str))
            ),
            "live_snapshot_roles": sorted(
                set(live["snapshot_role"].astype(str))
            ),
            "live_snapshot_membership_authority": (
                "RECONSTRUCTED_PIT_ANCHOR_PLUS_OFFICIAL_ADJUSTMENTS"
            ),
            "capture_window_constituent_symbol_coverage": coverage,
            "download_error_rows": int(len(download_errors)),
            "reconstruction": reconstruction,
        }
        runtime_universe[universe] = {
            "resumed": bool(prepared["resumed"]),
            "checkpoint_fingerprint": prepared["identity"].fingerprint,
        }

    capital_descriptor = semantic_fingerprint(
        repo_root,
        family="capital:sse",
    )
    szse_descriptor = semantic_fingerprint(
        repo_root,
        family="capital:szse",
    )
    capital = materialize_capital_monthly(
        trading_dates=trading_dates,
        fund_codes=["588000"],
        source_commit=source_commit,
        checkpoint_dir=checkpoint_dir / "sse",
        checkpoint_revision=str(capital_descriptor["checkpoint_revision"]),
        capture_date=checkpoint_capture_date,
        sleep_seconds=0.05,
    )
    szse_chunked = materialize_szse_etf_monthly(
        trading_dates=trading_dates,
        fund_codes=["159915"],
        source_commit=source_commit,
        checkpoint_dir=checkpoint_dir / "szse",
        checkpoint_revision=str(szse_descriptor["checkpoint_revision"]),
        capture_date=checkpoint_capture_date,
        sleep_seconds=0.05,
    )
    szse = szse_chunked.result

    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (checkpoint_dir / "runtime_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "prospective-context-checkpoint-runtime-v1",
                "operation_date": operation_date,
                "same_day_freshness_from_checkpoint": bool(
                    freshness_from_checkpoint
                ),
                "universe": runtime_universe,
                "sse_capital": {
                    "resumed_chunks": int(capital.resumed_chunks),
                    "executed_chunks": int(capital.executed_chunks),
                },
                "szse_etf": {
                    "resumed_chunks": int(szse_chunked.resumed_chunks),
                    "executed_chunks": int(szse_chunked.executed_chunks),
                },
                "formal_evidence_handoff": False,
                "qualification_granted": False,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    shares = pd.concat(
        [capital.etf.data.copy(), szse.data.copy()],
        ignore_index=True,
        sort=False,
    )
    if not shares.empty:
        shares["date"] = pd.to_datetime(shares["date"], errors="raise").dt.normalize()
        shares["fund_code"] = shares["fund_code"].astype(str).str.zfill(6)
        if shares.duplicated(["date", "fund_code"]).any():
            raise ValueError("public raw ETF share rail contains duplicate date/fund rows")

    coverage_rows = []
    for code in ("588000", "159915"):
        qualified = qualify_trailing_etf_coverage(
            shares,
            trading_dates=trading_dates,
            fund_code=code,
            window=60,
            min_coverage=0.80,
        )
        coverage_rows.append(qualified)
        today = qualified[
            pd.to_datetime(qualified["date"], errors="raise")
            .dt.normalize()
            .eq(pd.Timestamp(operation_date))
        ]
        if today.empty or not bool(today.iloc[-1]["observed"]):
            raise ValueError(f"ETF share source lacks operation-date row for {code}")
    coverage_frame = pd.concat(coverage_rows, ignore_index=True, sort=False)

    turnover = capital.turnover.combined.copy()
    _require_operation_date_row(
        turnover,
        operation_date=operation_date,
        label="SSE+SZSE A-share turnover",
    )

    capital_root = output_root / "capital"
    _write_csv(
        shares,
        capital_root / "etf_shares.csv",
        capture_date=capture_date,
    )
    _write_csv(
        coverage_frame,
        capital_root / "etf_share_coverage.csv",
        capture_date=capture_date,
    )
    _write_csv(
        capital.etf.errors,
        capital_root / "sse_etf_share_errors.csv",
        capture_date=capture_date,
    )
    _write_csv(
        szse.errors,
        capital_root / "szse_etf_share_errors.csv",
        capture_date=capture_date,
    )
    _write_csv(
        capital.turnover.sse,
        capital_root / "sse_a_share_turnover.csv",
        capture_date=capture_date,
    )
    _write_csv(
        capital.turnover.szse,
        capital_root / "szse_a_share_turnover.csv",
        capture_date=capture_date,
    )
    _write_csv(
        turnover,
        capital_root / "sse_szse_a_share_turnover.csv",
        capture_date=capture_date,
    )
    _write_csv(
        capital.turnover.errors,
        capital_root / "sse_szse_turnover_errors.csv",
        capture_date=capture_date,
    )

    etf_state: dict[str, Any] = {}
    for code in ("588000", "159915"):
        x = coverage_frame[
            coverage_frame["fund_code"].astype(str).str.zfill(6).eq(code)
        ].copy()
        today = x[
            pd.to_datetime(x["date"], errors="raise")
            .dt.normalize()
            .eq(pd.Timestamp(operation_date))
        ]
        row = today.iloc[-1]
        etf_state[code] = {
            "operation_date_observed": bool(row["observed"]),
            "trailing_60_observed_count": int(row["observed_count"]),
            "trailing_60_coverage": float(row["coverage"]),
            "frozen_public_coverage_gate_passed": bool(row["eligible"]),
        }

    files = [
        _file_record(output_root, path)
        for path in sorted(output_root.rglob("*"))
        if path.is_file() and path.name != RECEIPT_NAME
    ]
    receipt = {
        "schema_version": "prospective-context-public-raw-receipt-v1",
        "status": "PUBLIC_RAW_FORWARD_CAPTURE_COMPLETE",
        "contract_id": CONTRACT_ID,
        "operation_date": operation_date,
        "capture_date_asia_shanghai": capture_date,
        "capture_clock_provenance": "GITHUB_ACTIONS_RUN_METADATA_EXTERNAL_TO_CANONICAL_PAYLOAD",
        "source_commit": source_commit,
        "timing_mode": timing_mode,
        **(
            {"preopen_timing": timing_receipt}
            if timing_receipt is not None
            else {}
        ),
        "start_date": start_date,
        "end_date": end_date,
        "trading_days": int(len(trading_dates)),
        "warmup_rows_semantics": "HISTORICAL_ROWS_KNOWN_AT_FORWARD_CAPTURE_ONLY",
        "historical_replay_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
        "outcome_read": False,
        "private_model_semantics_materialized": False,
        "portfolio_or_holdings_data_included": False,
        "interpolation_used": False,
        "forward_fill_used": False,
        "natural_day_approximation_used": False,
        "universes": universe_receipts,
        "etf_share_state": etf_state,
        "turnover_operation_date_present": True,
        "files": files,
    }
    receipt_path = output_root / RECEIPT_NAME
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return CaptureResult(output_root=output_root, receipt=receipt)


def package_capture(
    capture_root: Path,
    *,
    output_dir: Path,
    operation_date: str,
) -> dict[str, Any]:
    receipt_path = capture_root / RECEIPT_NAME
    if not receipt_path.is_file():
        raise FileNotFoundError(RECEIPT_NAME)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "PUBLIC_RAW_FORWARD_CAPTURE_COMPLETE":
        raise ValueError("public raw capture is not complete")
    if receipt.get("operation_date") != operation_date:
        raise ValueError("public raw capture operation date mismatch")

    output_dir.mkdir(parents=True, exist_ok=True)
    digest_material = json.dumps(
        receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    identity = hashlib.sha256(digest_material).hexdigest()
    base = f"prospective-context-raw-{operation_date}"
    archive = output_dir / f"{base}.tar.gz"

    with archive.open("wb") as raw:
        import gzip

        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as tar:
                for path in sorted(capture_root.rglob("*")):
                    if not path.is_file():
                        continue
                    info = tar.gettarinfo(
                        str(path),
                        arcname=f"prospective_context_raw/{path.relative_to(capture_root).as_posix()}",
                    )
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    with path.open("rb") as handle:
                        tar.addfile(info, handle)

    checksum = _sha256(archive)
    checksum_path = output_dir / f"{base}.sha256"
    checksum_path.write_text(f"{checksum}  {archive.name}\n", encoding="utf-8")
    manifest = {
        "schema_version": "prospective-context-public-raw-package-v1",
        "operation_date": operation_date,
        "bundle_identity": identity,
        "release_tag": base,
        "archive": archive.name,
        "archive_sha256": checksum,
        "capture_receipt_sha256": _sha256(receipt_path),
        "historical_replay_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
        "private_model_semantics_materialized": False,
    }
    manifest_path = output_dir / f"{base}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "CONTRACT_ID",
    "RECEIPT_NAME",
    "CaptureResult",
    "_validate_same_day_capital_preflight",
    "capture_trading_dates",
    "materialize_public_raw_capture",
    "package_capture",
]
