from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Iterable

import pandas as pd

from .cninfo_direct import fetch_cninfo_announcements_direct
from .immutable_checkpoint import CheckpointIdentity, ImmutableCheckpointStore
from .official_filing_facts import (
    DERIVED_FUNDAMENTAL_SOURCE_ID,
    FILING_FACT_COLUMNS,
    FILING_PARSER_VERSION,
    FILING_PRESENTATION_UNKNOWN,
    build_filing_fact_rows,
    classify_official_filing_presentation,
    derive_fundamental_trend_evidence,
    download_official_document,
    extract_pdf_text,
)
from .pit_public_materialization import (
    CNINFO_SOURCE_ID,
    _market_available_date,
    _parse_document_identity,
    _real_trading_calendar,
)


FILING_MATERIALIZER_VERSION = "cninfo-versioned-filing-materializer-v2-publication-order"


@dataclass(frozen=True)
class FilingMaterializationResult:
    facts: pd.DataFrame
    trends: pd.DataFrame
    coverage: pd.DataFrame
    errors: pd.DataFrame
    summary: dict[str, object]


def _entity_id(symbol: str) -> str:
    code = "".join(ch for ch in str(symbol) if ch.isdigit()).zfill(6)
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "1", "2", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    raise ValueError(f"unsupported symbol: {symbol}")


def is_numeric_financial_filing_title(title: object) -> bool:
    text = re.sub(r"\s+", "", str(title or ""))
    if not re.search(r"20\d{2}年", text):
        return False
    if not any(
        token in text
        for token in (
            "年度报告",
            "第一季度报告",
            "一季度报告",
            "半年度报告",
            "第三季度报告",
            "三季度报告",
        )
    ):
        return False
    if any(
        token in text
        for token in (
            "摘要",
            "英文版",
            "审计报告",
            "问询",
            "回复",
            "提示性公告",
            "关于",
        )
    ):
        return False
    return True


def _numeric_filing_title_family(title: object) -> str:
    """Collapse only presentation variants of the same public filing title."""

    text = re.sub(r"\s+", "", str(title or ""))
    return re.sub(r"(?:（?(?:正文|全文)）?)$", "", text)


def _numeric_filing_variant_priority(title: object) -> int:
    """Prefer the complete/canonical filing when body and full variants coexist."""

    text = re.sub(r"\s+", "", str(title or ""))
    return 0 if re.search(r"(?:（?正文）?)$", text) else 1


def _select_primary_numeric_filing_candidates(raw: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicate presentation variants without dropping a sole body filing.

    CNINFO historically publishes quarterly filings as both a short "正文"
    document and a complete "报告"/"全文" document at the same official
    publication timestamp. They are parallel presentation carriers, not filing
    revisions. When both exist, standardized numeric facts must come from the
    complete/canonical carrier. If only the body carrier exists, retain it so
    historical coverage does not silently disappear.
    """

    candidates = raw[raw["公告标题"].map(is_numeric_financial_filing_title)].copy()
    if candidates.empty:
        return candidates
    candidates["_filing_title_family"] = candidates["公告标题"].map(
        _numeric_filing_title_family
    )
    candidates["_filing_variant_priority"] = candidates["公告标题"].map(
        _numeric_filing_variant_priority
    )
    group_cols = ["公告时间", "_filing_title_family"]
    best = candidates.groupby(group_cols, dropna=False)["_filing_variant_priority"].transform(
        "max"
    )
    return candidates[candidates["_filing_variant_priority"].eq(best)].drop(
        columns=["_filing_title_family", "_filing_variant_priority"]
    )


def _enrich_conflicting_presentation_variants(
    facts: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    """Recheck only unresolved conflicting documents against exact official bytes."""

    if facts.empty:
        return facts, 0
    out = facts.copy()
    if "document_presentation_variant" not in out.columns:
        out["document_presentation_variant"] = ""
    group_cols = [
        "entity_id",
        "period_end",
        "fact_type",
        "evidence_available_date",
        "publication_timestamp",
    ]
    document_variants: dict[str, str] = {}
    rechecked = 0
    for _, group in out.groupby(group_cols, dropna=False, sort=False):
        if group["document_id"].astype(str).nunique() < 2:
            continue
        signatures = {
            (float(row.value), str(row.unit))
            for row in group.itertuples()
        }
        if len(signatures) < 2:
            continue
        for document_id in sorted(group["document_id"].astype(str).unique()):
            if document_id in document_variants:
                continue
            rows = out[out["document_id"].astype(str).eq(document_id)]
            existing = {
                str(value)
                for value in rows["document_presentation_variant"].dropna().astype(str)
                if str(value).strip()
            }
            if len(existing) == 1:
                document_variants[document_id] = next(iter(existing))
                continue
            if len(existing) > 1:
                raise ValueError(
                    f"filing document presentation metadata conflict: {document_id}"
                )
            row = rows.iloc[0]
            downloaded = download_official_document(str(row["document_url"]))
            expected_sha = str(row["document_sha256"])
            if downloaded.sha256 != expected_sha:
                raise ValueError(
                    "official filing bytes changed during conflict recheck: "
                    f"{document_id}: expected={expected_sha} actual={downloaded.sha256}"
                )
            text = extract_pdf_text(downloaded.content)
            variant = classify_official_filing_presentation(text)
            if variant == FILING_PRESENTATION_UNKNOWN:
                raise ValueError(
                    f"official filing presentation cannot be classified: {document_id}"
                )
            document_variants[document_id] = variant
            rechecked += 1

    for document_id, variant in document_variants.items():
        mask = out["document_id"].astype(str).eq(document_id)
        out.loc[mask, "document_presentation_variant"] = variant
    return out, rechecked


def _symbol_query_identity(
    *,
    source_commit: str,
    symbol: str,
    query_start: str,
    query_end: str,
) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="cninfo-filing-index",
        producer_version=FILING_MATERIALIZER_VERSION,
        source_commit=source_commit,
        source_identities=(CNINFO_SOURCE_ID,),
        query_identity={"symbol": symbol, "category": "ALL_DISCLOSURES"},
        scope={"start_date": query_start, "end_date": query_end},
    )


def _document_identity(
    *,
    source_commit: str,
    symbol: str,
    document_id: str,
    attachment_url: str,
) -> CheckpointIdentity:
    return CheckpointIdentity(
        producer="official-filing-facts",
        producer_version=FILING_PARSER_VERSION,
        source_commit=source_commit,
        source_identities=(CNINFO_SOURCE_ID,),
        query_identity={
            "symbol": symbol,
            "document_id": document_id,
            "attachment_url": attachment_url,
        },
        scope={"document_id": document_id},
    )


def materialize_versioned_filing_facts(
    symbols: Iterable[str],
    *,
    target_start_date: object,
    end_date: object,
    trading_dates: Iterable[object],
    source_commit: str,
    checkpoint_dir: str | Path,
    warmup_years: int = 2,
    checkpoint_source_commit: str | None = None,
) -> FilingMaterializationResult:
    """Reconstruct filing facts from exact versioned CNINFO attachments."""

    if warmup_years < 1:
        raise ValueError("warmup_years must be >= 1")
    target_start = pd.Timestamp(target_start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < target_start:
        raise ValueError("end_date must not precede target_start_date")
    query_start = target_start - pd.DateOffset(years=warmup_years)
    calendar = _real_trading_calendar(trading_dates)
    store = ImmutableCheckpointStore(checkpoint_dir)
    checkpoint_commit = str(checkpoint_source_commit or source_commit)
    unique_symbols = sorted({str(value).zfill(6) for value in symbols})
    if not unique_symbols:
        raise ValueError("at least one symbol is required")

    fact_parts: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    resumed_symbol_queries = 0
    executed_symbol_queries = 0
    resumed_documents = 0
    executed_documents = 0

    for symbol in unique_symbols:
        entity = _entity_id(symbol)
        query_identity = _symbol_query_identity(
            source_commit=checkpoint_commit,
            symbol=symbol,
            query_start=str(query_start.date()),
            query_end=str(end.date()),
        )
        loaded_query = store.load(query_identity)
        if loaded_query is not None:
            raw = loaded_query.frames["announcements"]
            resumed_symbol_queries += 1
        else:
            try:
                raw = fetch_cninfo_announcements_direct(
                    symbol=symbol,
                    start_date=query_start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
                store.save(
                    query_identity,
                    frames={"announcements": raw},
                    metadata={"entity_id": entity},
                )
                executed_symbol_queries += 1
            except Exception as exc:
                errors.append(
                    {
                        "entity_id": entity,
                        "document_id": "QUERY",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                coverage_rows.append(
                    {
                        "source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
                        "entity_id": entity,
                        "coverage_start": target_start,
                        "coverage_end": end,
                        "query_status": "FAILED",
                        "financial_documents": 0,
                        "parsed_documents": 0,
                    }
                )
                continue

        candidates = _select_primary_numeric_filing_candidates(raw)
        financial_documents = 0
        parsed_documents = 0
        symbol_failed = False
        for _, announcement in candidates.iterrows():
            publication = announcement["公告时间"]
            try:
                available_date, _ = _market_available_date(
                    publication, trading_dates=calendar
                )
            except ValueError:
                continue
            if available_date > end:
                continue
            financial_documents += 1
            attachment_value = announcement.get("公告附件链接")
            attachment = "" if pd.isna(attachment_value) else str(attachment_value).strip()
            if not attachment:
                errors.append(
                    {
                        "entity_id": entity,
                        "document_id": "UNKNOWN",
                        "error": "FINANCIAL_FILING_MISSING_IMMUTABLE_ATTACHMENT_URL",
                    }
                )
                symbol_failed = True
                continue
            document_id = "UNKNOWN"
            try:
                document_id, _ = _parse_document_identity(announcement["公告链接"])
                doc_identity = _document_identity(
                    source_commit=checkpoint_commit,
                    symbol=symbol,
                    document_id=document_id,
                    attachment_url=attachment,
                )
                loaded_doc = store.load(doc_identity)
                presentation_variant = ""
                if loaded_doc is not None:
                    facts = loaded_doc.frames["facts"]
                    metadata = loaded_doc.receipt.get("metadata")
                    if isinstance(metadata, dict):
                        presentation_variant = str(
                            metadata.get("document_presentation_variant") or ""
                        )
                    resumed_documents += 1
                else:
                    downloaded = download_official_document(attachment)
                    text = extract_pdf_text(downloaded.content)
                    presentation_variant = classify_official_filing_presentation(text)
                    facts = build_filing_fact_rows(
                        entity_id=entity,
                        title=str(announcement["公告标题"]),
                        evidence_available_date=available_date,
                        publication_timestamp=publication,
                        source_identity=CNINFO_SOURCE_ID,
                        provider="CNINFO",
                        document_id=document_id,
                        revision_id=f"DOCUMENT:{document_id}:SHA256:{downloaded.sha256}",
                        document_url=downloaded.url,
                        document_sha256=downloaded.sha256,
                        text=text,
                    )
                    if facts.empty:
                        raise ValueError("filing parser produced no standardized facts")
                    store.save(
                        doc_identity,
                        frames={"facts": facts},
                        metadata={
                            "entity_id": entity,
                            "document_id": document_id,
                            "publication_timestamp": str(publication),
                            "document_url": downloaded.url,
                            "document_retrieval_url": downloaded.retrieval_url,
                            "document_sha256": downloaded.sha256,
                            "document_presentation_variant": presentation_variant,
                        },
                    )
                    executed_documents += 1
                if len(facts):
                    if "publication_timestamp" not in facts.columns:
                        raise ValueError("filing facts checkpoint lacks publication_timestamp")
                    facts = facts.copy()
                    facts["filing_title"] = str(announcement["公告标题"])
                    facts["document_presentation_variant"] = presentation_variant
                    fact_parts.append(facts)
                    parsed_documents += 1
            except Exception as exc:
                errors.append(
                    {
                        "entity_id": entity,
                        "document_id": document_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                symbol_failed = True

        coverage_rows.append(
            {
                "source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
                "entity_id": entity,
                "coverage_start": target_start,
                "coverage_end": end,
                "query_status": (
                    "COMPLETE_WINDOW"
                    if not symbol_failed and parsed_documents == financial_documents and financial_documents > 0
                    else "FAILED"
                ),
                "financial_documents": int(financial_documents),
                "parsed_documents": int(parsed_documents),
            }
        )

    facts = (
        pd.concat(fact_parts, ignore_index=True, sort=False)
        if fact_parts
        else pd.DataFrame(columns=list(FILING_FACT_COLUMNS))
    )
    if len(facts):
        facts["period_end"] = pd.to_datetime(facts["period_end"], errors="raise").dt.normalize()
        facts["evidence_available_date"] = pd.to_datetime(
            facts["evidence_available_date"], errors="raise"
        ).dt.normalize()
        facts["publication_timestamp_order"] = pd.to_datetime(
            facts["publication_timestamp"], errors="raise", utc=True
        )
        facts = facts.sort_values(
            [
                "entity_id",
                "period_end",
                "evidence_available_date",
                "publication_timestamp_order",
                "document_id",
                "fact_type",
            ]
        ).drop(columns=["publication_timestamp_order"]).drop_duplicates(
            ["entity_id", "document_id", "revision_id", "fact_type"], keep="last"
        ).reset_index(drop=True)
    facts, presentation_recheck_documents = _enrich_conflicting_presentation_variants(facts)
    trends = derive_fundamental_trend_evidence(facts)
    coverage = pd.DataFrame(coverage_rows)
    complete_entities = int(
        coverage["query_status"].astype(str).eq("COMPLETE_WINDOW").sum()
    ) if len(coverage) else 0
    summary = {
        "source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
        "target_start_date": str(target_start.date()),
        "query_warmup_start_date": str(query_start.date()),
        "end_date": str(end.date()),
        "warmup_years": int(warmup_years),
        "checkpoint_source_commit": checkpoint_commit,
        "checkpoint_reuse_mode": (
            "CURRENT_SOURCE_COMMIT"
            if checkpoint_commit == str(source_commit)
            else "FROZEN_COMPATIBLE_LEGACY_SOURCE_COMMIT"
        ),
        "symbols": len(unique_symbols),
        "complete_entities": complete_entities,
        "filing_fact_rows": int(len(facts)),
        "derived_trend_records": int(len(trends)),
        "resumed_symbol_queries": resumed_symbol_queries,
        "executed_symbol_queries": executed_symbol_queries,
        "resumed_documents": resumed_documents,
        "executed_documents": executed_documents,
        "numerical_trend_materialization_state": (
            "QUALIFIED_INPUT"
            if complete_entities == len(unique_symbols) and len(trends)
            else "PARTIAL_COVERAGE" if len(trends) else "DATA_INSUFFICIENT"
        ),
        "fundamental_state_mapping_state": "FUNDAMENTAL_PIT_STATE_CONTRACT_V1_DEFINED_SEPARATELY",
        "document_variant_selection": (
            "ANNOUNCEMENT_TITLE_FAMILY_PREFER_COMPLETE_OR_CANONICAL_OVER_BODY_"
            "THEN_CONFLICT_ONLY_OFFICIAL_PDF_INTERNAL_TITLE_SHA_VERIFIED"
        ),
        "presentation_conflict_recheck_documents": int(presentation_recheck_documents),
        "revision_ordering": (
            "EVIDENCE_AVAILABLE_DATE_THEN_OFFICIAL_PUBLICATION_TIMESTAMP_"
            "THEN_EXPLICIT_REVISION_TITLE_THEN_SEMANTIC_EQUIVALENCE_"
            "FAIL_ON_CONFLICT"
        ),
        "fundamental_state_thresholds_invented": False,
        "future_prices_or_returns_used": False,
        "hindsight_backfill": False,
    }
    return FilingMaterializationResult(
        facts=facts,
        trends=trends,
        coverage=coverage,
        errors=pd.DataFrame(errors, columns=["entity_id", "document_id", "error"]),
        summary=summary,
    )


__all__ = [
    "FILING_MATERIALIZER_VERSION",
    "FilingMaterializationResult",
    "is_numeric_financial_filing_title",
    "materialize_versioned_filing_facts",
]
