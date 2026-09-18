from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import io
import json
import re
from typing import Callable, Iterable, Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import pandas as pd
import requests

from .bounded_retry import call_with_bounded_network_retry
from .pit_public_materialization import (
    REQUIRED_PIT_COLUMNS,
    _stable_hash,
    validate_materialized_pit_records,
)


DERIVED_FUNDAMENTAL_SOURCE_ID = "DERIVED_PIT_FUNDAMENTAL_TRENDS"
DERIVED_FUNDAMENTAL_PROVIDER = "DERIVED_VERSIONED_OFFICIAL_FILINGS"
FILING_PARSER_VERSION = "official-filing-facts-v3-layout-safe-units-revision-time"

FILING_FACT_COLUMNS = (
    "entity_id",
    "period_end",
    "fact_type",
    "value",
    "unit",
    "evidence_available_date",
    "publication_timestamp",
    "source_identity",
    "provider",
    "document_id",
    "revision_id",
    "document_url",
    "document_sha256",
    "parser_version",
)
DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS = tuple(REQUIRED_PIT_COLUMNS) + (
    "evidence_payload",
    "source_url_identity",
)

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
_UNIT_RE = re.compile(r"单位\s*:\s*(人民币)?(百万元|万元|元)(?:\s|$|币种|[,，;；])")
_NUMERIC_TOKEN_RE = re.compile(
    r"(?<![\d.])(?:-?\d[\d,]*(?:\.\d+)?|\(\d[\d,]*(?:\.\d+)?\))(?![\d.])"
)


@dataclass(frozen=True)
class DownloadedOfficialDocument:
    # Canonical immutable source identity requested by the materializer.
    url: str
    sha256: str
    content: bytes
    # Actual same-provider HTTPS transport endpoint used to retrieve bytes.
    # Optional keeps existing injected test doubles/backward-compatible callers valid.
    retrieval_url: str | None = None


def _canonical_host(url: str) -> str:
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("official filing URL must be absolute HTTPS")
    host = parsed.hostname.lower()
    if host not in _OFFICIAL_ATTACHMENT_HOSTS:
        raise ValueError(f"filing attachment host is not allowlisted: {host}")
    return host


_CNINFO_STATIC_ATTACHMENT_RE = re.compile(
    r"^/finalpage/(?P<date>20\d{2}-\d{2}-\d{2})/(?P<document_id>\d+)\.PDF$",
    re.IGNORECASE,
)


def _cninfo_https_download_fallback(url: str) -> str | None:
    """Derive CNINFO's official HTTPS download endpoint from an exact attachment URL."""

    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "static.cninfo.com.cn":
        return None
    match = _CNINFO_STATIC_ATTACHMENT_RE.fullmatch(parsed.path)
    if match is None:
        return None
    query = urlencode(
        {
            "bulletinId": match.group("document_id"),
            "announceTime": match.group("date"),
        }
    )
    return f"https://www.cninfo.com.cn/new/announcement/download?{query}"


def _download_once(
    url: str,
    *,
    timeout: float,
    opener: Callable[..., object],
) -> bytes:
    host = _canonical_host(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
    }
    if host in {"static.cninfo.com.cn", "www.cninfo.com.cn"}:
        headers["Referer"] = "https://www.cninfo.com.cn/"
    request = Request(url, headers=headers, method="GET")
    with opener(request, timeout=timeout) as response:  # nosec B310 - host allowlist above
        return response.read()



_CNINFO_SESSION_BOOTSTRAP_URL = "https://www.cninfo.com.cn/"


def _download_cninfo_with_https_session(
    canonical_url: str,
    fallback_url: str,
    *,
    timeout: float,
) -> tuple[bytes, str]:
    """Retry the same CNINFO bulletin over browser-like HTTPS session transport.

    This transport fallback is used only after both urllib HTTPS paths returned
    403.  It never changes the canonical source identity, never downgrades to
    HTTP, and rejects redirects outside the existing official HTTPS allowlist.
    """

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.cninfo.com.cn/",
    }
    bootstrap_headers = {
        "User-Agent": headers["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": headers["Accept-Language"],
    }
    session = requests.Session()
    try:
        try:
            bootstrap = call_with_bounded_network_retry(
                lambda: session.get(
                    _CNINFO_SESSION_BOOTSTRAP_URL,
                    headers=bootstrap_headers,
                    timeout=min(timeout, 10.0),
                    allow_redirects=True,
                ),
                attempts=2,
                backoff_seconds=0.5,
            )
            if int(getattr(bootstrap, "status_code", 0) or 0) >= 500:
                bootstrap.raise_for_status()
        except (requests.RequestException, ValueError):
            # Cookie warm-up is best-effort only; the exact bulletin request
            # below remains authoritative and fail-closed.
            pass

        for candidate in (canonical_url, fallback_url):
            response = call_with_bounded_network_retry(
                lambda candidate=candidate: session.get(
                    candidate,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=True,
                ),
                attempts=3,
                backoff_seconds=0.5,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 403:
                continue
            response.raise_for_status()
            retrieval_url = str(getattr(response, "url", candidate) or candidate)
            _canonical_host(retrieval_url)
            content = bytes(response.content)
            if not content:
                raise ValueError("official filing attachment is empty")
            return content, retrieval_url
    finally:
        session.close()

    raise HTTPError(
        fallback_url,
        403,
        "Forbidden after CNINFO HTTPS session transport",
        hdrs=None,
        fp=None,
    )


def _download_cninfo_with_browser_transport(
    canonical_url: str,
    fallback_url: str,
    *,
    timeout: float,
) -> tuple[bytes, str]:
    """Use browser-fingerprint HTTPS transport against the same CNINFO bulletin.

    This is a final transport-only fallback for hosted-runner WAF decisions.
    Canonical evidence identity remains the immutable CNINFO attachment URL.
    Both candidate URLs stay on the existing official HTTPS allowlist, and any
    redirect outside that allowlist is rejected before bytes are accepted.
    """

    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:  # pragma: no cover - installed by the data extra via akshare
        raise RuntimeError(
            "curl_cffi is required for CNINFO browser transport fallback"
        ) from exc

    headers = {
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.cninfo.com.cn/",
    }
    last_error: Exception | None = None
    for candidate in (canonical_url, fallback_url):
        try:
            response = curl_requests.get(
                candidate,
                headers=headers,
                impersonate="chrome",
                timeout=timeout,
                allow_redirects=True,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 403:
                last_error = HTTPError(
                    candidate, 403, "Forbidden", hdrs=None, fp=None
                )
                continue
            response.raise_for_status()
            retrieval_url = str(getattr(response, "url", candidate) or candidate)
            _canonical_host(retrieval_url)
            content = bytes(response.content)
            if not content:
                raise ValueError("official filing attachment is empty")
            return content, retrieval_url
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise RuntimeError(
            "CNINFO browser HTTPS transport exhausted without official PDF bytes"
        ) from last_error
    raise RuntimeError("CNINFO browser HTTPS transport produced no attempt")


def download_official_document(
    url: str,
    *,
    timeout: float = 30.0,
    opener: Callable[..., object] = urlopen,
) -> DownloadedOfficialDocument:
    """Download one exact official filing version and bind it to a content hash.

    CNINFO's immutable static HTTPS attachment can return HTTP 403 to non-browser
    infrastructure even when the same exact bulletin remains available through
    CNINFO's official HTTPS download endpoint.  Only that deterministic,
    same-provider endpoint is allowed as a fallback, and only for a 403 from
    static.cninfo.com.cn.  The bulletin id and announcement date are derived
    from the immutable attachment URL; no search, substitution, or HTTP
    transport downgrade is permitted.
    """

    _canonical_host(url)
    retrieval_url = url
    try:
        content = call_with_bounded_network_retry(
            lambda: _download_once(url, timeout=timeout, opener=opener),
            attempts=3,
            backoff_seconds=0.5,
        )
    except HTTPError as exc:
        fallback = _cninfo_https_download_fallback(url)
        if exc.code != 403 or fallback is None:
            raise
        retrieval_url = fallback
        try:
            content = call_with_bounded_network_retry(
                lambda: _download_once(fallback, timeout=timeout, opener=opener),
                attempts=3,
                backoff_seconds=0.5,
            )
        except HTTPError as fallback_exc:
            if fallback_exc.code != 403:
                raise
            try:
                content, retrieval_url = _download_cninfo_with_https_session(
                    url,
                    fallback,
                    timeout=timeout,
                )
            except HTTPError as session_exc:
                if session_exc.code != 403:
                    raise
                content, retrieval_url = _download_cninfo_with_browser_transport(
                    url,
                    fallback,
                    timeout=timeout,
                )

    if not content:
        raise ValueError("official filing attachment is empty")
    return DownloadedOfficialDocument(
        url=url,
        retrieval_url=retrieval_url,
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
        .replace("／", "/")
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


def _nearest_explicit_unit(lines: list[str], index: int, *, lookback: int = 12) -> str | None:
    """Return the nearest explicit table unit at or before a fact row.

    The bounded lookup avoids using a unit declaration from an unrelated distant
    table. No rescaling is performed: only exact yuan tables are eligible.
    """

    left = max(0, index - lookback)
    for position in range(index, left - 1, -1):
        match = _UNIT_RE.search(lines[position])
        if match:
            return str(match.group(2))
    return None


def _logical_row_window(
    lines: list[str],
    index: int,
    *,
    max_lines: int = 4,
) -> str:
    """Rejoin one visually wrapped PDF table row without crossing into later values.

    pypdf layout extraction can split a single Chinese table label across
    physical lines, for example 归属于上市公司 / 股东的净利润. We only
    join forward until the first numeric token appears, capped at four physical
    lines, so labels may be reconstructed without flattening adjacent table rows.
    """

    parts: list[str] = []
    for position in range(index, min(len(lines), index + max_lines)):
        parts.append(lines[position])
        joined = " ".join(parts)
        if _NUMERIC_TOKEN_RE.search(joined):
            break
    return " ".join(parts)


def _compact_row_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value))


def _first_yuan_value_after_label(lines: list[str], labels: Iterable[str]) -> float | None:
    for index in range(len(lines)):
        logical_row = _logical_row_window(lines, index)
        compact = _compact_row_text(logical_row)
        label = next((candidate for candidate in labels if candidate in compact), None)
        if label is None:
            continue
        unit = _nearest_explicit_unit(lines, index)
        if unit != "元":
            # Missing/local non-yuan unit is not evidence for a canonical CNY
            # amount. Keep searching for another explicit yuan table occurrence.
            continue
        suffix = compact.split(label, 1)[1]
        for token in _NUMERIC_TOKEN_RE.findall(suffix):
            try:
                value = _parse_numeric_token(token)
            except ValueError:
                continue
            if pd.notna(value):
                return float(value)
    return None


def _first_basic_eps_value(lines: list[str], labels: Iterable[str]) -> float | None:
    """Extract EPS from an explicitly proven per-share row, including wrapped units."""

    for index in range(len(lines)):
        logical_row = _logical_row_window(lines, index)
        compact = _compact_row_text(logical_row)
        label = next((candidate for candidate in labels if candidate in compact), None)
        if label is None:
            continue
        suffix = compact.split(label, 1)[1]
        first_number = _NUMERIC_TOKEN_RE.search(suffix)
        if first_number is None:
            continue
        unit_region = suffix[: first_number.start()]
        if "元/股" not in unit_region:
            continue
        try:
            value = _parse_numeric_token(first_number.group(0))
        except ValueError:
            continue
        if pd.notna(value):
            return float(value)

    # Preserve the existing explicit-table-unit contract for legacy layouts in
    # which EPS shares the table's declared 单位：元 context.
    return _first_yuan_value_after_label(lines, labels)


def extract_standard_filing_facts(text: str) -> dict[str, float]:
    """Extract facts only from locally proven CNY-yuan table contexts.

    A document may legitimately contain unrelated tables in 万元/百万元. Those
    tables no longer poison the whole document, but a target fact is accepted
    only when its nearest bounded unit declaration is exactly 元/人民币元. The
    parser never rescales a non-yuan table and never fills a missing fact.
    """

    lines = _normalize_text_lines(text)
    if not any(_UNIT_RE.search(line) for line in lines):
        raise ValueError("filing text does not contain an explicit table unit declaration")

    facts: dict[str, float] = {}
    for fact_type, labels in _FACT_LABELS.items():
        value = (
            _first_basic_eps_value(lines, labels)
            if fact_type == "BASIC_EPS"
            else _first_yuan_value_after_label(lines, labels)
        )
        if value is not None:
            facts[fact_type] = value
    if not facts:
        for index in range(len(lines)):
            compact = _compact_row_text(_logical_row_window(lines, index))
            contains_target = any(
                label in compact
                for labels in _FACT_LABELS.values()
                for label in labels
            )
            if not contains_target:
                continue
            unit = _nearest_explicit_unit(lines, index)
            if unit in {"万元", "百万元"}:
                raise ValueError(
                    "filing target fact uses non-yuan unit; parser refuses inferred scaling"
                )
        raise ValueError("filing has no target facts with locally proven CNY-yuan units")
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
    publication_timestamp: object,
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
    publication = pd.Timestamp(pd.to_datetime(publication_timestamp, errors="raise"))
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
                "publication_timestamp": publication.isoformat(),
                "source_identity": str(source_identity),
                "provider": str(provider),
                "document_id": str(document_id),
                "revision_id": str(revision_id),
                "document_url": str(document_url),
                "document_sha256": str(document_sha256),
                "parser_version": FILING_PARSER_VERSION,
            }
        )
    return pd.DataFrame(rows, columns=list(FILING_FACT_COLUMNS))


def latest_filing_fact_as_of(
    facts: pd.DataFrame,
    *,
    entity_id: str,
    fact_type: str,
    period_end: pd.Timestamp,
    as_of: pd.Timestamp,
) -> Mapping[str, object] | None:
    """Select the latest genuinely knowable filing version, never by ID order."""

    required = {"publication_timestamp", "evidence_available_date", "document_id", "revision_id"}
    missing = required - set(facts.columns)
    if missing:
        raise ValueError(f"filing fact revision ordering missing columns: {sorted(missing)}")
    rows = facts[
        facts["entity_id"].astype(str).eq(str(entity_id))
        & facts["fact_type"].astype(str).eq(fact_type)
        & pd.to_datetime(facts["period_end"]).dt.normalize().eq(period_end)
        & pd.to_datetime(facts["evidence_available_date"]).dt.normalize().le(as_of)
    ].copy()
    if rows.empty:
        return None
    rows["evidence_available_date"] = pd.to_datetime(
        rows["evidence_available_date"], errors="raise"
    ).dt.normalize()
    rows["publication_timestamp_order"] = pd.to_datetime(
        rows["publication_timestamp"], errors="raise", utc=True
    )
    latest_available = rows["evidence_available_date"].max()
    candidates = rows[rows["evidence_available_date"].eq(latest_available)].copy()
    latest_publication = candidates["publication_timestamp_order"].max()
    candidates = candidates[candidates["publication_timestamp_order"].eq(latest_publication)]
    if len(candidates) != 1:
        identities = sorted(
            f"{row.document_id}/{row.revision_id}" for row in candidates.itertuples()
        )
        raise ValueError(
            "ambiguous same-availability filing revisions without deterministic publication order: "
            + ",".join(identities)
        )
    return candidates.iloc[0].drop(labels=["publication_timestamp_order"]).to_dict()


def derive_fundamental_trend_evidence(facts: pd.DataFrame) -> pd.DataFrame:
    """Derive only threshold-free, as-of trends from versioned filing facts."""

    required = {
        "entity_id",
        "period_end",
        "fact_type",
        "value",
        "unit",
        "evidence_available_date",
        "publication_timestamp",
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
        return pd.DataFrame(columns=list(DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS))

    x = facts.copy()
    x["period_end"] = pd.to_datetime(x["period_end"], errors="raise").dt.normalize()
    x["evidence_available_date"] = pd.to_datetime(
        x["evidence_available_date"], errors="raise"
    ).dt.normalize()
    pd.to_datetime(x["publication_timestamp"], errors="raise", utc=True)
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
        prior = latest_filing_fact_as_of(
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
            "current_publication_timestamp": str(current["publication_timestamp"]),
            "current_document_sha256": str(current["document_sha256"]),
            "prior_document_id": str(prior["document_id"]),
            "prior_publication_timestamp": str(prior["publication_timestamp"]),
            "prior_document_sha256": str(prior["document_sha256"]),
        }
        provenance = {
            "source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
            "provider": DERIVED_FUNDAMENTAL_PROVIDER,
            "derived_source_identity": DERIVED_FUNDAMENTAL_SOURCE_ID,
            "source_filing_identity": str(current["source_identity"]),
            "source_provider": str(current["provider"]),
            "document_url": str(current["document_url"]),
            "document_id": str(current["document_id"]),
            "revision_id": str(current["revision_id"]),
            "publication_timestamp": str(current["publication_timestamp"]),
            "document_sha256": str(current["document_sha256"]),
            "prior_document_id": str(prior["document_id"]),
            "prior_revision_id": str(prior["revision_id"]),
            "prior_publication_timestamp": str(prior["publication_timestamp"]),
            "prior_document_sha256": str(prior["document_sha256"]),
            "as_of_selection": "LATEST_AVAILABLE_DATE_THEN_LATEST_OFFICIAL_PUBLICATION_TIMESTAMP",
            "later_restatements_do_not_rewrite_prior_evidence": True,
            "parser_version": FILING_PARSER_VERSION,
        }
        evidence_type = evidence_types[str(current["fact_type"])]
        observation_id = (
            f"{current['entity_id']}:{evidence_type}:{period_end.date()}:"
            f"{current['document_id']}:{current['revision_id']}"
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
        return pd.DataFrame(columns=list(DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS))
    return validate_materialized_pit_records(
        pd.DataFrame(evidence_rows, columns=list(DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS))
    )


__all__ = [
    "DERIVED_FUNDAMENTAL_SOURCE_ID",
    "DERIVED_FUNDAMENTAL_PROVIDER",
    "FILING_PARSER_VERSION",
    "FILING_FACT_COLUMNS",
    "DERIVED_FUNDAMENTAL_EVIDENCE_COLUMNS",
    "DownloadedOfficialDocument",
    "download_official_document",
    "extract_pdf_text",
    "filing_period_end_from_title",
    "extract_standard_filing_facts",
    "build_filing_fact_rows",
    "latest_filing_fact_as_of",
    "derive_fundamental_trend_evidence",
]
