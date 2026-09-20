from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.exchange_earnings_materialization import (
    materialize_registered_exchange_earnings_directions,
)
from tech_sentiment.pit_public_materialization import validate_materialized_pit_records


SOURCES = ("SSE_ANNOUNCEMENT_ARCHIVE", "SZSE_ANNOUNCEMENT_ARCHIVE")


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def select_representative_rows(issuer: pd.DataFrame) -> pd.DataFrame:
    required = {
        "source_identity",
        "evidence_type",
        "availability_state",
        "entity_id",
        "document_id",
        "evidence_available_date",
    }
    missing = required.difference(issuer.columns)
    if missing:
        raise ValueError(f"issuer evidence missing preflight columns: {sorted(missing)}")

    eligible = issuer[
        issuer["source_identity"].astype(str).isin(SOURCES)
        & issuer["evidence_type"].astype(str).eq("ISSUER_EARNINGS_FORECAST")
        & issuer["availability_state"].astype(str).eq("HISTORICAL_RECONSTRUCTABLE")
    ].copy()
    if eligible.empty:
        raise ValueError("no eligible exchange earnings documents for live preflight")

    parts: list[pd.DataFrame] = []
    for source in SOURCES:
        group = eligible[eligible["source_identity"].astype(str).eq(source)].copy()
        if group.empty:
            raise ValueError(f"live preflight source has no eligible documents: {source}")
        group = group.sort_values(
            ["entity_id", "evidence_available_date", "document_id"]
        )
        distinct = group.drop_duplicates("entity_id", keep="first")
        indices = [0]
        if len(distinct) > 1:
            indices.append(len(distinct) - 1)
        parts.append(distinct.iloc[indices])
    return pd.concat(parts, ignore_index=True, sort=False)


def run_live_preflight(issuer: pd.DataFrame) -> dict[str, object]:
    selected = select_representative_rows(issuer)
    rows: list[dict[str, object]] = []
    hard_errors: list[dict[str, object]] = []

    for _, row in selected.iterrows():
        result = materialize_registered_exchange_earnings_directions(
            pd.DataFrame([row])
        )
        status = (
            "DIRECTION"
            if len(result.evidence)
            else "UNCLASSIFIED"
            if len(result.unclassified)
            else "ERROR"
        )
        item = {
            "source_identity": str(row["source_identity"]),
            "entity_id": str(row["entity_id"]),
            "document_id": str(row["document_id"]),
            "status": status,
            "direction_rows": int(len(result.evidence)),
            "unclassified_rows": int(len(result.unclassified)),
            "error_rows": int(len(result.errors)),
        }
        if len(result.evidence):
            provenance = json.loads(str(result.evidence.iloc[0]["provenance"]))
            item.update(
                {
                    "canonical_document_url": provenance.get("document_url"),
                    "retrieval_url": provenance.get("document_retrieval_url"),
                    "transport_method": provenance.get("transport_method"),
                    "transport_encoding": provenance.get("transport_encoding"),
                    "transport_sha256": provenance.get("transport_sha256"),
                    "document_sha256": provenance.get("document_sha256"),
                }
            )
        if len(result.errors):
            errors = result.errors.to_dict("records")
            item["errors"] = errors
            hard_errors.extend(errors)
        rows.append(item)

    receipt = {
        "status": (
            "LIVE_PROVIDER_DOCUMENT_PREFLIGHT_PASS"
            if not hard_errors
            else "LIVE_PROVIDER_DOCUMENT_PREFLIGHT_FAIL"
        ),
        "formal_evidence_handoff": False,
        "evidence_qualification_changed": False,
        "outcomes_read": False,
        "selected_documents": int(len(selected)),
        "provider_families": list(SOURCES),
        "hard_error_count": int(len(hard_errors)),
        "results": rows,
    }
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    issuer = validate_materialized_pit_records(
        _read_csv(args.artifact_root / "pit_evidence_materialization/pit_evidence.csv")
    )
    receipt = run_live_preflight(issuer)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    if receipt["status"] != "LIVE_PROVIDER_DOCUMENT_PREFLIGHT_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
