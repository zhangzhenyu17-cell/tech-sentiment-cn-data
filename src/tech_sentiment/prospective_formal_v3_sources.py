from __future__ import annotations

import hashlib
import json
import tarfile
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .capital_input_data import combine_sse_szse_a_share_turnover

SHANGHAI = ZoneInfo("Asia/Shanghai")
SOURCE_KEYS = ("SSE_588000", "SZSE_159915", "SSE_TURNOVER", "SZSE_TURNOVER")
REQUIRED_TIMESTAMP_SEMANTICS = "SOURCE_FETCH_COMPLETION_TIME"
FORMAL_DECISION_CUTOFF = time(8, 45)
FORMAL_ARTIFACT_DEADLINE = time(9, 15)
FIRST_ELIGIBLE_EXECUTION = time(9, 30)


@dataclass(frozen=True)
class FormalV3CapitalSources:
    sse_etf: pd.DataFrame
    szse_etf: pd.DataFrame
    sse_turnover: pd.DataFrame
    szse_turnover: pd.DataFrame
    combined_turnover: pd.DataFrame
    provenance: dict[str, dict[str, Any]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("V3 first_observed_at must be timezone-aware")
    return parsed.astimezone(SHANGHAI)



def validate_formal_v3_assembly_window(
    *,
    market_session_date: str,
    decision_date: str,
    assembled_at: datetime,
    client: Any,
) -> dict[str, Any]:
    if assembled_at.tzinfo is None:
        raise ValueError("assembled_at must be timezone-aware")
    raw = client.tool_trade_date_hist_sina()
    if raw is None or len(raw) == 0:
        raise RuntimeError("A-share trading calendar source returned no rows")
    column = "trade_date" if "trade_date" in raw.columns else raw.columns[0]
    dates = (
        pd.DatetimeIndex(pd.to_datetime(raw[column], errors="raise"))
        .normalize()
        .sort_values()
        .unique()
    )
    session = pd.Timestamp(market_session_date).normalize()
    decision = pd.Timestamp(decision_date).normalize()
    if session not in set(dates) or decision not in set(dates):
        raise ValueError("formal V3 pair must use confirmed A-share trading days")
    later = dates[dates > session]
    if len(later) == 0 or later[0] != decision:
        raise ValueError("formal V3 decision_date must be immediate next trading day")
    session_close = datetime.combine(session.date(), time(15, 0), tzinfo=SHANGHAI)
    source_cutoff = datetime.combine(
        decision.date(), FORMAL_DECISION_CUTOFF, tzinfo=SHANGHAI
    )
    artifact_deadline = datetime.combine(
        decision.date(), FORMAL_ARTIFACT_DEADLINE, tzinfo=SHANGHAI
    )
    first_execution = datetime.combine(
        decision.date(), FIRST_ELIGIBLE_EXECUTION, tzinfo=SHANGHAI
    )
    now = assembled_at.astimezone(SHANGHAI)
    if now < session_close:
        raise ValueError("formal V3 assembly began before market-session close")
    if now > artifact_deadline:
        raise ValueError(
            "FORMAL_V3_ARTIFACT_DEADLINE_PASSED: "
            f"assembled_at={now.isoformat()} deadline={artifact_deadline.isoformat()}"
        )
    return {
        "timing_contract": "prospective_capture_timing_v3_formal",
        "market_session_date": market_session_date,
        "decision_date": decision_date,
        "session_close_asia_shanghai": session_close.isoformat(),
        "data_freeze_deadline_asia_shanghai": source_cutoff.isoformat(),
        "formal_source_cutoff_asia_shanghai": source_cutoff.isoformat(),
        "formal_artifact_deadline_asia_shanghai": artifact_deadline.isoformat(),
        "first_eligible_execution_at": first_execution.isoformat(),
        "minimum_preopen_buffer_hours": 0.75,
        "historical_replay_allowed": False,
        "retroactive_evidence_qualification_allowed": False,
        "forward_outcomes_read": False,
    }

def _verify_one(
    *,
    package_dir: Path,
    source_key: str,
    market_session_date: str,
    decision_date: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    tag = f"prospective-source-observation-v3-{market_session_date}-{source_key}"
    archive = package_dir / f"{tag}.tar.gz"
    checksum = package_dir / f"{tag}.sha256"
    manifest_path = package_dir / f"{tag}.manifest.json"
    for path in (archive, checksum, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(f"formal V3 source package missing: {path.name}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "prospective-source-observation-package-v3":
        raise ValueError("unexpected formal V3 source manifest schema")
    for key, expected in (
        ("release_tag", tag),
        ("source_key", source_key),
        ("market_session_date", market_session_date),
        ("decision_date", decision_date),
    ):
        if str(manifest.get(key) or "") != expected:
            raise ValueError(f"formal V3 source manifest identity mismatch: {key}")
    if manifest.get("observation_timestamp_semantics") != REQUIRED_TIMESTAMP_SEMANTICS:
        raise ValueError("formal V3 source does not use source-fetch-completion time")
    for key in (
        "formal_evidence_handoff",
        "historical_backfill_allowed",
        "retroactive_evidence_qualification_allowed",
    ):
        if manifest.get(key) is not False:
            raise ValueError(f"formal V3 source manifest safety drift: {key}")

    fields = checksum.read_text(encoding="utf-8").strip().split()
    if len(fields) != 2 or fields[1] != archive.name:
        raise ValueError("formal V3 source checksum sidecar is malformed")
    archive_sha = _sha256(archive)
    if fields[0] != archive_sha or manifest.get("archive_sha256") != archive_sha:
        raise ValueError("formal V3 source archive checksum mismatch")

    with tarfile.open(archive, mode="r:gz") as bundle:
        names = {member.name for member in bundle.getmembers() if member.isfile()}
        expected_names = {
            "source_observation/SOURCE_OBSERVATION_RECEIPT.json",
            "source_observation/data.csv",
            "source_observation/errors.csv",
        }
        if names != expected_names:
            raise ValueError("formal V3 source archive member set mismatch")
        receipt_member = bundle.getmember(
            "source_observation/SOURCE_OBSERVATION_RECEIPT.json"
        )
        data_member = bundle.getmember("source_observation/data.csv")
        receipt_handle = bundle.extractfile(receipt_member)
        data_handle = bundle.extractfile(data_member)
        if receipt_handle is None or data_handle is None:
            raise ValueError("formal V3 source archive is unreadable")
        receipt_raw = receipt_handle.read()
        data = pd.read_csv(data_handle)

    if hashlib.sha256(receipt_raw).hexdigest() != manifest.get("receipt_sha256"):
        raise ValueError("formal V3 source receipt checksum mismatch")
    receipt = json.loads(receipt_raw.decode("utf-8"))
    if receipt.get("schema_version") != "prospective-source-observation-v3":
        raise ValueError("unexpected formal V3 source receipt schema")
    if receipt.get("state") != "COMPLETE":
        raise ValueError("formal V3 source receipt is not COMPLETE")
    if receipt.get("observation_timestamp_semantics") != REQUIRED_TIMESTAMP_SEMANTICS:
        raise ValueError("formal V3 source receipt timing semantics are invalid")
    for key, expected in (
        ("source_key", source_key),
        ("market_session_date", market_session_date),
        ("decision_date", decision_date),
    ):
        if str(receipt.get(key) or "") != expected:
            raise ValueError(f"formal V3 source receipt identity mismatch: {key}")
    for key in (
        "formal_evidence_handoff",
        "historical_backfill_allowed",
        "retroactive_evidence_qualification_allowed",
        "forward_outcomes_read",
        "production_changed",
        "trading_authority_changed",
    ):
        if receipt.get(key) is not False:
            raise ValueError(f"formal V3 source receipt safety drift: {key}")
    if _identity(receipt) != manifest.get("source_observation_identity"):
        raise ValueError("formal V3 source receipt identity hash mismatch")
    if receipt.get("first_observed_at_asia_shanghai") != manifest.get(
        "first_observed_at_asia_shanghai"
    ):
        raise ValueError("formal V3 source first-observed mismatch")

    first_observed = _parse_time(str(receipt["first_observed_at_asia_shanghai"]))
    cutoff = datetime.combine(
        datetime.fromisoformat(decision_date).date(),
        FORMAL_DECISION_CUTOFF,
        tzinfo=SHANGHAI,
    )
    if first_observed > cutoff:
        raise ValueError(
            "FORMAL_V3_SOURCE_AFTER_DECISION_CUTOFF: "
            f"source={source_key} first_observed={first_observed.isoformat()} "
            f"cutoff={cutoff.isoformat()}"
        )
    if receipt.get("shadow_decision_eligible") is not True:
        raise ValueError("formal V3 source is not decision-eligible")

    dates = pd.to_datetime(data.get("date"), errors="raise").dt.normalize()
    if len(data) != 1 or not dates.eq(pd.Timestamp(market_session_date)).all():
        raise ValueError("formal V3 source must contain exactly one market-session row")
    return data, {
        "release_tag": tag,
        "source_observation_identity": manifest["source_observation_identity"],
        "archive_sha256": archive_sha,
        "receipt_sha256": manifest["receipt_sha256"],
        "first_observed_at_asia_shanghai": receipt["first_observed_at_asia_shanghai"],
        "transport_origin": receipt.get("transport_origin"),
        "producer_git_sha": receipt.get("producer_git_sha"),
        "observation_timestamp_semantics": REQUIRED_TIMESTAMP_SEMANTICS,
    }


def load_formal_v3_capital_sources(
    *,
    package_root: Path,
    market_session_date: str,
    decision_date: str,
) -> FormalV3CapitalSources:
    frames: dict[str, pd.DataFrame] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for source_key in SOURCE_KEYS:
        frame, source_provenance = _verify_one(
            package_dir=package_root / source_key,
            source_key=source_key,
            market_session_date=market_session_date,
            decision_date=decision_date,
        )
        frames[source_key] = frame
        provenance[source_key] = source_provenance
    combined = combine_sse_szse_a_share_turnover(
        frames["SSE_TURNOVER"], frames["SZSE_TURNOVER"]
    )
    return FormalV3CapitalSources(
        sse_etf=frames["SSE_588000"],
        szse_etf=frames["SZSE_159915"],
        sse_turnover=frames["SSE_TURNOVER"],
        szse_turnover=frames["SZSE_TURNOVER"],
        combined_turnover=combined,
        provenance=provenance,
    )


__all__ = [
    "FORMAL_DECISION_CUTOFF",
    "FORMAL_ARTIFACT_DEADLINE",
    "FIRST_ELIGIBLE_EXECUTION",
    "FormalV3CapitalSources",
    "load_formal_v3_capital_sources",
    "validate_formal_v3_assembly_window",
]
