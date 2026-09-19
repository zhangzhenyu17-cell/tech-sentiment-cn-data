from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from . import official_pit_archives as official


CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "reference"
    / "v4a_szse_security_code_migration_contract_v1.json"
)


def _digits(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit()).zfill(6)


def _code_set(value: object) -> set[str]:
    if isinstance(value, list):
        return {_digits(item) for item in value}
    code = _digits(value)
    return {code} if code.strip("0") else set()


def load_migration_contract(path: str | Path = CONTRACT_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "v4a-szse-security-code-migration-v1":
        raise ValueError("unexpected SZSE code-migration contract schema")
    if payload.get("status") != "FROZEN_ENGINEERING_IDENTITY_ALIAS":
        raise ValueError("unexpected SZSE code-migration contract status")
    for key in (
        "evidence_source_eligibility_changed",
        "pit_no_lookahead_semantics_changed",
        "research_scope_changed",
        "future_outcomes_used",
        "production_authority_changed",
        "trading_authority_changed",
    ):
        if payload.get(key) is not False:
            raise ValueError(f"SZSE code-migration contract boundary drift: {key}")
    if payload.get("same_listed_security_identity_only") is not True:
        raise ValueError("SZSE code-migration contract must be same-security only")
    migrations = payload.get("migrations")
    if not isinstance(migrations, list) or not migrations:
        raise ValueError("SZSE code-migration contract must define migrations")
    return payload


def _migration_for_target(
    target_code: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    for row in payload["migrations"]:
        if not isinstance(row, dict):
            raise ValueError("SZSE code-migration row must be an object")
        old_code = _digits(row.get("old_code"))
        new_code = _digits(row.get("new_code"))
        effective = pd.Timestamp(row.get("effective_date")).normalize()
        if old_code == new_code or not old_code.strip("0") or not new_code.strip("0"):
            raise ValueError("invalid SZSE code-migration alias pair")
        if pd.isna(effective):
            raise ValueError("invalid SZSE code-migration effective date")
        if target_code in {old_code, new_code}:
            return {
                **row,
                "old_code": old_code,
                "new_code": new_code,
                "effective_date": effective,
            }
    return None


def _rewrite_payload_alias(
    payload: dict[str, object],
    *,
    target_code: str,
    aliases: set[str],
) -> dict[str, object]:
    data = payload.get("data")
    if not isinstance(data, list):
        return payload
    rewritten: list[object] = []
    for item in data:
        if not isinstance(item, dict):
            rewritten.append(item)
            continue
        raw_code = item.get("secCode") or item.get("securityCode")
        codes = _code_set(raw_code)
        if codes.intersection(aliases):
            copied = dict(item)
            copied["secCode"] = target_code
            copied.pop("securityCode", None)
            rewritten.append(copied)
        else:
            rewritten.append(item)
    result = dict(payload)
    result["data"] = rewritten
    return result


def _publication_date(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(pd.to_datetime(value, errors="raise"))
    if ts.tzinfo is not None:
        ts = ts.tz_convert("Asia/Shanghai").tz_localize(None)
    return ts.normalize()


def fetch_szse_announcements_with_code_migration(
    *,
    symbol: str,
    start_date: object,
    end_date: object,
    timeout: float = 30.0,
    browser_session_factory: Callable[[], object] | None = None,
    contract_path: str | Path = CONTRACT_PATH,
) -> pd.DataFrame:
    """Fetch official SZSE announcements with one frozen same-security code alias."""

    target_code = _digits(symbol)
    contract = load_migration_contract(contract_path)
    migration = _migration_for_target(target_code, contract)
    if migration is None:
        return official.fetch_szse_announcements(
            symbol=target_code,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
            browser_session_factory=browser_session_factory,
        )

    aliases = {migration["old_code"], migration["new_code"]}
    original_post = official._post_json
    original_browser_post = official._szse_browser_post_json

    def patched_post(*args: object, **kwargs: object) -> dict[str, object]:
        payload = original_post(*args, **kwargs)
        return _rewrite_payload_alias(
            payload,
            target_code=target_code,
            aliases=aliases,
        )

    def patched_browser_post(*args: object, **kwargs: object) -> dict[str, object]:
        payload = original_browser_post(*args, **kwargs)
        return _rewrite_payload_alias(
            payload,
            target_code=target_code,
            aliases=aliases,
        )

    official._post_json = patched_post
    official._szse_browser_post_json = patched_browser_post
    try:
        frame = official.fetch_szse_announcements(
            symbol=target_code,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
            browser_session_factory=browser_session_factory,
        )
    finally:
        official._post_json = original_post
        official._szse_browser_post_json = original_browser_post

    if frame.empty:
        return frame

    dates = frame["publication_time"].map(_publication_date)
    effective = migration["effective_date"]
    if target_code == migration["old_code"]:
        keep = dates < effective
    else:
        keep = dates >= effective
    return frame.loc[keep].reset_index(drop=True)


__all__ = [
    "CONTRACT_PATH",
    "fetch_szse_announcements_with_code_migration",
    "load_migration_contract",
]
