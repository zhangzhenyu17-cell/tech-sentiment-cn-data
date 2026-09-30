from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import time
from typing import Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

REGISTRY_ID = "INNOVATION_DRUG_931152_ISSUER_IDENTITY_V1"
MAPPING_REGISTRY_ID = "INNOVATION_DRUG_CDE_NMPA_931152_ENTITY_MAPPING_V1"
INDEX_CODE = "931152"
EXPECTED_MEMBER_COUNT = 86
SSE_ENDPOINT = "https://query.sse.com.cn/sseQuery/commonQuery.do"
SZSE_ENDPOINT = "https://www.szse.cn/api/report/index/companyGeneralization"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140 Safari/537.36"


@dataclass(frozen=True)
class IssuerIdentityResult:
    rows: list[dict[str, object]]
    raw_responses: dict[str, object]
    registry: dict[str, object]
    mapping_registry: dict[str, object]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def membership_identity(membership_csv_bytes: bytes) -> str:
    return _sha256_bytes(membership_csv_bytes)


def membership_symbols(frame: pd.DataFrame) -> list[str]:
    if "symbol" not in frame.columns:
        raise ValueError("931152 membership scope requires symbol")
    symbols = sorted(set(frame["symbol"].astype(str).str.zfill(6)))
    if len(symbols) != EXPECTED_MEMBER_COUNT:
        raise ValueError(f"931152 membership symbol count drift: {len(symbols)}")
    if any(not re.fullmatch(r"\d{6}", symbol) for symbol in symbols):
        raise ValueError("931152 membership contains invalid symbol")
    return symbols


def _default_fetch(url: str, referer: str) -> str:
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": UA, "Referer": referer})
            with urlopen(request, timeout=20) as response:
                return response.read().decode("utf-8", "replace")
        except Exception as exc:
            last_error = exc
            if attempt >= 3:
                break
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"official issuer identity fetch failed after bounded retries: {url}") from last_error


def _sse_query(symbol: str) -> tuple[str, str]:
    params = {
        "jsonCallBack": "cb931152",
        "STOCK_TYPE": "8" if symbol.startswith("688") else "1",
        "REG_PROVINCE": "",
        "CSRC_CODE": "",
        "STOCK_CODE": symbol,
        "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
        "COMPANY_STATUS": "2,4,5,7,8",
        "type": "inParams",
        "isPagination": "true",
        "pageHelp.cacheSize": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.pageSize": "10",
        "pageHelp.pageNo": "1",
    }
    url = SSE_ENDPOINT + "?" + urlencode(params)
    referer = "https://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE=" + symbol
    return url, referer


def _szse_query(symbol: str) -> tuple[str, str]:
    url = SZSE_ENDPOINT + "?" + urlencode({"secCode": symbol, "random": "0.931152"})
    referer = "https://www.szse.cn/certificate/individual/index.html?code=" + symbol
    return url, referer


def _parse_sse(symbol: str, text: str) -> tuple[str, str, object]:
    match = re.fullmatch(r"cb931152\((.*)\)\s*", text, re.S)
    if not match:
        raise ValueError(f"SSE issuer response is not expected JSONP: {symbol}")
    payload = json.loads(match.group(1))
    rows = payload.get("result") or []
    if len(rows) != 1:
        raise ValueError(f"SSE issuer identity is not exact-one: {symbol}: {len(rows)}")
    row = rows[0]
    if str(row.get("A_STOCK_CODE") or "") != symbol:
        raise ValueError(f"SSE issuer code mismatch: {symbol}")
    legal = str(row.get("FULL_NAME") or "").strip()
    short = str(row.get("SEC_NAME_CN") or row.get("COMPANY_ABBR") or "").strip()
    if not legal:
        raise ValueError(f"SSE issuer legal name missing: {symbol}")
    return legal, short, payload


def _parse_szse(symbol: str, text: str) -> tuple[str, str, object]:
    payload = json.loads(text)
    if str(payload.get("code")) != "0":
        raise ValueError(f"SZSE issuer response failed: {symbol}: {payload.get('code')}")
    row = payload.get("data") or {}
    if str(row.get("agdm") or "") != symbol:
        raise ValueError(f"SZSE issuer code mismatch: {symbol}")
    legal = str(row.get("gsqc") or "").strip()
    short = str(row.get("agdjc") or row.get("agjc") or "").strip()
    if not legal:
        raise ValueError(f"SZSE issuer legal name missing: {symbol}")
    return legal, short, payload


def resolve_issuer_identities(
    symbols: Iterable[str],
    *,
    fetcher: Callable[[str, str], str] = _default_fetch,
    membership_scope_sha256: str,
    captured_at: str,
) -> IssuerIdentityResult:
    symbols = sorted(set(str(x).zfill(6) for x in symbols))
    if len(symbols) != EXPECTED_MEMBER_COUNT:
        raise ValueError("931152 issuer resolver requires exact frozen 86-member union")
    rows: list[dict[str, object]] = []
    raw: dict[str, object] = {}
    for symbol in symbols:
        if symbol.startswith("6"):
            url, referer = _sse_query(symbol)
            text = fetcher(url, referer)
            legal, short, payload = _parse_sse(symbol, text)
            source_kind = "SSE_OFFICIAL_COMPANY_LIST_API"
            exchange = "SSE"
        else:
            url, referer = _szse_query(symbol)
            text = fetcher(url, referer)
            legal, short, payload = _parse_szse(symbol, text)
            source_kind = "SZSE_OFFICIAL_COMPANY_GENERALIZATION_API"
            exchange = "SZSE"
        entity_id = symbol + (".SH" if exchange == "SSE" else ".SZ")
        rows.append({
            "symbol": symbol,
            "entity_id": entity_id,
            "listed_issuer_legal_name": legal,
            "security_short_name": short,
            "exchange": exchange,
            "source_kind": source_kind,
            "source_url": referer,
            "source_api_url": url,
        })
        raw[symbol] = payload
    names = [str(row["listed_issuer_legal_name"]) for row in rows]
    entities = [str(row["entity_id"]) for row in rows]
    if len(set(names)) != EXPECTED_MEMBER_COUNT:
        raise ValueError("931152 issuer legal names must be unique across frozen member union")
    if len(set(entities)) != EXPECTED_MEMBER_COUNT:
        raise ValueError("931152 entity ids must be unique")
    registry = {
        "registry_id": REGISTRY_ID,
        "date": "2026-09-30",
        "status": "OFFICIAL_EXCHANGE_IDENTITY_RESOLVED_EXACT_ONLY",
        "index_code": INDEX_CODE,
        "membership_scope_sha256": membership_scope_sha256,
        "captured_at": captured_at,
        "membership_unique_symbols": EXPECTED_MEMBER_COUNT,
        "resolved_issuer_rows": len(rows),
        "unresolved_issuer_rows": 0,
        "issuers": rows,
        "safety": {
            "fuzzy_matching_used": False,
            "substring_matching_used": False,
            "affiliate_inference_used": False,
            "outcome_read": False,
            "direction_classified": False,
            "predictive_weight_assigned": False,
            "sector_score_defined": False,
            "evidence_qualification_changed": False,
            "production_permission": "NONE",
            "trading_authority": False,
        },
    }
    mappings = []
    for row in rows:
        mappings.append({
            "applicant_name_exact": row["listed_issuer_legal_name"],
            "entity_id": row["entity_id"],
            "mapping_basis": "EXACT_LISTED_ISSUER_LEGAL_NAME",
            "evidence": {
                "source_kind": row["source_kind"],
                "source_url": row["source_url"],
                "source_api_url": row["source_api_url"],
                "security_code": row["symbol"],
                "security_short_name": row["security_short_name"],
                "listed_issuer_legal_name": row["listed_issuer_legal_name"],
                "captured_at": captured_at,
            },
        })
    mapping_registry = {
        "registry_id": MAPPING_REGISTRY_ID,
        "date": "2026-09-30",
        "status": "FROZEN_931152_EXACT_LISTED_ISSUER_MAPPING_ONLY",
        "domain_id": "INNOVATION_DRUG",
        "index_code": INDEX_CODE,
        "membership_scope_sha256": membership_scope_sha256,
        "membership_unique_symbols": EXPECTED_MEMBER_COUNT,
        "fuzzy_matching_allowed": False,
        "substring_matching_allowed": False,
        "affiliate_inference_allowed": False,
        "unmapped_affiliates_remain_unmapped": True,
        "multi_applicant_exact_token_matching_allowed": True,
        "multi_entity_exact_fanout_allowed": True,
        "mappings": mappings,
        "safety": registry["safety"],
    }
    return IssuerIdentityResult(rows, raw, registry, mapping_registry)


__all__ = [
    "EXPECTED_MEMBER_COUNT",
    "INDEX_CODE",
    "IssuerIdentityResult",
    "MAPPING_REGISTRY_ID",
    "REGISTRY_ID",
    "membership_identity",
    "membership_symbols",
    "resolve_issuer_identities",
]
