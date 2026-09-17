from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import json
import re
from typing import Callable, Iterable, Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pandas as pd

from .pit_public_materialization import _stable_hash, validate_materialized_pit_records


DERIVED_FUNDAMENTAL_SOURCE_ID = "DERIVED_PIT_FUNDAMENTAL_TRENDS"
DERIVED_FUNDAMENTAL_PROVIDER = "DERIVED_VERSIONED_OFFICIAL_FILINGS"
FILING_PARSER_VERSION = "official-filing-facts-v1"

_OFFICIAL_ATTACHMENT_HOSTS = {
    "static.cninfo.com.cn",
    "www.cninfo.com.cn",
    "www.sse.com.cn",
    "static.sse.com.cn",
    "disc.static.szse.cn",
    "www.szse.cn",
}

_FACT_LABELS: dict[str, tuple[str, ...]] = {
    "OPERATING_REVENUE": ("营业收入",),
    "NET_PROFIT_PARENT": (
        "归属于上市公司股东的净利润",
        "归属于母公司股东的净利润",
    ),
    "OPERATING_CASH_FLOW_NET": ("经营活动产生的现金流量净额",),
    "TOTAL_ASSETS": ("总资产",),
    "EQUITY_PARENT": (
        "归属于上市公司股东的所有者权益",
        "归属于母公司所有者权益",
    ),
    "BASIC_EPS": ("基本每股收益",),
}


@dataclass(frozen=True)
class DownloadedOfficialDocument:
    url: str
    sha256: str
    content: bytes


def _canonical_host(url: str) -> str:
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("official filing URL must be absolute HTTPS")
    host = parsed.hostname.lower()
    if host not in _OFFICIAL_ATTACHMENT_HOSTS:
        raise ValueError(f"filing attachment host is not allowlisted: {host}")
    return host


def download_official_document(
    url: str,
    *,
    timeout: float = 30.0,
    opener: Callable[..., object] = urlopen,
) -> DownloadedOfficialDocument:
    """Download one exact official filing version and bind it to a content hash."""

    _canonical_host(url)
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )
    with opener(request, timeout=timeout) as response:  # nosec B310 - host allowlist above
        content = response.read()
    if not content:
        raise ValueError("official filing attachment is empty")
    return DownloadedOfficialDocument(
        url=url,
        sha256=sha256(content).hexdigest(),
        content=content,
    )


def extract_pdf_text(content: bytes) -> str:
    """Extract the embedded text layer; OCR is deliberately not used."""

    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pypdf is required for official filing parsing") from exc
    reader = PdfReader(io.BytesIO(content), strict=False)
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text(extraction_mode="layout") or ""
        if text.strip():
            parts.append(text)
    result = "\n".join(parts).strip()
    if not result:
        raise ValueError("official filing has no extractable text layer")
    return result


def filing_period_end_from_title(title: object) -> pd.Timestamp:
    text = re.sub(r"\s+", "", str(title or ""))
    match = re.search(r"(20\d{2})年", text)
    if not match:
        raise ValueError("filing title lacks a report year")
    year = int(match.group(1))
    if any(token in text for token in ("第一季度", "一季度")):
        return pd.Timestamp(year=year, month=3, day=31)
    if any(token in text for token in ("半年度", "半年报", "中期报告")):
        return pd.Timestamp(year=year, month=6, day=30)
    if any(token in text for token in ("第三季度", "三季度")):
        return pd.Timestamp(year=year, month=9, day=30)
    if any(token in text for token in ("年度报告", "年报")):
        return pd.Timestamp(year=year, month=12, day=31)
    raise ValueError("filing title does not identify a supported report period")


def _normalize_text_lines(text: str) -> list[str]:
    clean = (
        str(text)
        .replace("，", ",")
        .replace("：", ":")
        .replace("（", "(")
        .replace("）", ")")
        .replace("−", "-")
        .replace("—", "-")
    )
    return [" ".join(line.split()) for line in clean.splitlines() if line.strip()]


def _parse_numeric_token(token: str) -> float:
    text = token.strip().replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    value = float(text)
    return -value if negative else value


def _first_value_after_label(lines: list[str], labels: Iterable[str]) -> float | None:
    token_re = re.compile(r"(?<![\d.])(?:-?\d[\d,]*(?:\.\d+)?|\(\d[\d,]*(?:\.\d+)?\))(?![\d.])")
    for index, line in enumerate(lines):
        label = next((candidate for candidate in labels if candidate in line), None)
        if label is None:
            continue
        suffix = line.split(label, 1)[1]
        candidates = token_re.findall(suffix)
        if not candidates:
            lookahead = " ".join(lines[index + 1 : index + 3])
            candidates = token_re.findall(lookahead)
        for token in candidates:
            try:
                value = _parse_numeric_token(token)
            except ValueError:
                continue
            if pd.notna(value):
                return float(value)
    return None


def extract_standard_filing_facts(text: str) -> dict[str, float]:
    """Extract standardized facts without guessing units or missing values.

    The parser only accepts filing text that explicitly declares yuan units. It
    does not rescale 万元/百万元 tables. Missing rows stay missing and are audited
    by the materializer instead of being imputed from a current-state provider.
    """

    compact = re.sub(r"\s+", "", str(text))
    if "单位:万元" in compact or "单位:百万元" in compact:
        raise ValueError("filing table uses a non-yuan unit; parser refuses inferred scaling")
    if not any(marker in compact for marker in ("单位:元", "单位:人民币元")):
        raise ValueError("filing text does not prove CNY-yuan table units")

    lines = _normalize_text_lines(text)
    facts: dict[str, float] = {}
    for fact_type, labels in _FACT_LABELS.items():
        value = _first_value_after_label(lines, labels)
        if value is not None:
            facts[fact_type] = value
    if "OPERATING_REVENUE" in facts and "NET_PROFIT_PARENT" in facts:
        revenue = facts["OPERATING_REVENUE"]
        if revenue != 0:
            facts["NET_PROFIT_MARGIN"] = facts["NET_PROFIT_PARENT"] / revenue
    return facts


def build_filing_fact_rows(
    *,
    entity_id: str,
    title: str,
    evidence_available_date: object,
    source_identity: str,
    provider: str,
    document_id: str,
    revision_id: str,
    document_url: str,
    document_sha256: str,
    text: str,
) -> pd.DataFrame:
    period_end = filing_period_end_from_title(title)
    facts = extract_standard_filing_facts(text)
    rows: list[dict[str, object]] = []
    for fact_type, value in sorted(facts.items()):
        unit = "RATIO" if fact_type == "NET_PROFIT_MARGIN" else (
            "CNY_PER_SHARE" if fact_type == "BASIC_EPS" else "CNY"
        )
        rows.append(
            {
                "entity_id": str(entity_id),
                "period_end": period_end,
                "fact_type": fact_type,
                "value": float(value),
                "unit": unit,
                "evidence_available_date": pd.Timestamp(evidence_available_date).normalize(),
                "source_identity": str(source_identity),
                "provider": str(provider),
                "document_id": str(document_id),
                "revision_id": str(revision_id),
                "document_url": str(document_url),
                "document_sha256": str(document_sha256),
                "parser_version": FILING_PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows)


def _latest_fact_as_of(
    facts: pd.DataFrame,
    *,
    entity_id: str,
    fact_type: str,
    period_end: pd.Timestamp,
    as_of: pd.Timestamp,
) -> Mapping[str, object] | None:
    rows = facts[
        facts["entity_id"].astype(str).eq(str(entity_id))
        & facts["fact_type"].astype(str).eq(fact_type)
        & pd.to_datetime(facts["period_end"]).dt.normalize().eq(period_end)
        & pd.to_datetime(facts["evidence_available_date"]).dt.normalize().le(as_of)
    ].copy()
    if rows.empty:
        return None
    rows["evidence_available_date"] = pd.to_datetime(rows["evidence_available_date"]).dt.normalize()
    rows = rows.sort_values(["evidence_available_date", "document_id", "revision_id"])
    return rows.iloc[-1].to_dict()


def derive_fundamental_trend_evidence(facts: pd.DataFrame) -> pd.DataFrame:
    """Derive only threshold-free, as-of trends from versioned filing facts.

    No FUNDAMENTAL_PASS/WATCH/FAIL mapping is created here. The project does not
    currently contain a frozen mapping from these numerical facts to that state,
    so inventing one would cross the evidence-definition boundary.
    """

    required = {
        "entity_id",
        "period_end",
        "fact_type",
        "value",
        "unit",
        "evidence_available_date",
        "source_identity",
        "provider",
        "document_id",
        "revision_id",
        "document_url",
        "document_sha256",
        "parser_version",
    }
    missing = required - set(facts.columns)
    if missing:
        raise ValueError(f"filing facts missing columns: {sorted(missing)}")
    if facts.empty:
        return pd.DataFrame()

    x = facts.copy()
    x["period_end"] = pd.to_datetime(x["period_end"], errors="raise").dt.normalize()
    x["evidence_available_date"] = pd.to_datetime(
        x["evidence_available_date"], errors="raise"
    ).dt.normalize()
    if x.duplicated(["entity_id", "document_id", "revision_id", "fact_type"]).any():
        raise ValueError("filing facts contain duplicate document/fact identities")

    evidence_rows: list[dict[str, object]] = []
    evidence_types = {
        "OPERATING_REVENUE": "REVENUE_TREND",
        "NET_PROFIT_PARENT": "PROFIT_TREND",
        "OPERATING_CASH_FLOW_NET": "CASH_FLOW_TREND",
        "NET_PROFIT_MARGIN": "MARGIN_TREND",
    }
    for _, current in x[x["fact_type"].isin(evidence_types)].iterrows():
        period_end = pd.Timestamp(current["period_end"]).normalize()
        prior_end = period_end - pd.DateOffset(years=1)
        as_of = pd.Timestamp(current["evidence_available_date"]).normalize()
        prior = _latest_fact_as_of(
            x,
            entity_id=str(current["entity_id"]),
            fact_type=str(current["fact_type"]),
            period_end=prior_end,
            as_of=as_of,
        )
        if prior is None:
            continue
        current_value = float(current["value"])
        prior_value = float(prior["value"])
        if str(current["fact_type"]) == "NET_PROFIT_MARGIN":
            change = current_value - prior_value
            change_name = "yoy_delta"
        else:
            if prior_value == 0:
                continue
            change = current_value / prior_value - 1.0
            change_name = "yoy_change"

        payload = {
            "metric": str(current["fact_type"]),
            "current_value": current_value,
            "current_unit": str(current["unit"]),
            "prior_comparable_value": prior_value,
            "prior_period_end": str(pd.Timestamp(prior["period_end"]).date()),
            change_name: float(change),
            "formula_version": FILING_PARSER_VERSION,
            "current_document_id": str(current["document_id"]),
            "current_document_sha256": str(current["document_sha256"]),
            "prior_document_id": str(prior["document_id"]),
            "prior_document_sha256": str(prior["document_sha256"]),
        }
        provenance = {
            "derived_source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
            "source_filing_identity": str(current["source_identity"]),
            "source_provider": str(current["provider"]),
            "document_url": str(current["document_url"]),
            "document_id": str(current["document_id"]),
            "revision_id": str(current["revision_id"]),
            "document_sha256": str(current["document_sha256"]),
            "prior_document_id": str(prior["document_id"]),
            "prior_revision_id": str(prior["revision_id"]),
            "prior_document_sha256": str(prior["document_sha256"]),
            "as_of_selection": "LATEST_COMPARABLE_VERSION_AVAILABLE_BY_CURRENT_EVIDENCE_DATE",
            "later_restatements_do_not_rewrite_prior_evidence": True,
            "parser_version": FILING_PARSER_VERSION,
        }
        evidence_type = evidence_types[str(current["fact_type"])]
        observation_id = (
            f"{current['entity_id']}:{evidence_type}:{period_end.date()}:"
            f"{current['document_id']}"
        )
        evidence_rows.append(
            {
                "evidence_id": f"derived-fundamental:{_stable_hash(observation_id)}",
                "entity_id": str(current["entity_id"]),
                "evidence_type": evidence_type,
                "event_date": period_end,
                "evidence_available_date": as_of,
                "source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
                "provider": DERIVED_FUNDAMENTAL_PROVIDER,
                "document_id": str(current["document_id"]),
                "revision_id": str(current["revision_id"]),
                "provenance": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                "ingestion_identity": _stable_hash(
                    {
                        "observation": observation_id,
                        "payload": payload,
                        "available": str(as_of.date()),
                    }
                ),
                "availability_state": "HISTORICAL_RECONSTRUCTABLE",
                "evidence_payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                "source_url_identity": str(current["document_url"]),
            }
        )
    if not evidence_rows:
        return pd.DataFrame()
    return validate_materialized_pit_records(pd.DataFrame(evidence_rows))


__all__ = [
    "DERIVED_FUNDAMENTAL_SOURCE_ID",
    "DERIVED_FUNDAMENTAL_PROVIDER",
    "FILING_PARSER_VERSION",
    "DownloadedOfficialDocument",
    "download_official_document",
    "extract_pdf_text",
    "filing_period_end_from_title",
    "extract_standard_filing_facts",
    "build_filing_fact_rows",
    "derive_fundamental_trend_evidence",
]
