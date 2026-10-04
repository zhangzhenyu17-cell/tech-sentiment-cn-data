from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

import pandas as pd

from .official_filing_facts import (
    FILING_FACT_COLUMNS,
    _AMOUNT_UNIT_SCALE,
    _NUMERIC_TOKEN_RE,
    _explicit_unit_from_text,
    _normalize_text_lines,
    _parse_numeric_token,
    filing_period_end_from_title,
)

NONRECURRING_DISCLOSURE_PARSER_VERSION = (
    "official-filing-nonrecurring-disclosure-v2-sse-szse-regulatory-table"
)

# These are public raw disclosure-row identities from the issuer's explicit
# 非经常性损益项目和金额 table. They are not a private core/unusual taxonomy and do
# not decide whether a row belongs inside CN-GAAP 营业利润.
NONRECURRING_DISCLOSURE_FACT_LABELS: Mapping[str, tuple[str, ...]] = {
    "NR_NON_CURRENT_ASSET_DISPOSAL": ("非流动性资产处置损益", "非流动资产处置损益"),
    "NR_GOVERNMENT_GRANT": ("计入当期损益的政府补助",),
    "NR_FINANCIAL_ASSET_FAIR_VALUE_AND_DISPOSAL": ("除同公司正常经营业务相关的有效套期保值",),
    "NR_NONFINANCIAL_FUND_OCCUPATION_FEE": ("计入当期损益的对非金融企业收取的资金占用费",),
    "NR_ENTRUSTED_INVESTMENT_OR_ASSET_MANAGEMENT": ("委托他人投资或管理资产的损益",),
    "NR_EXTERNAL_ENTRUSTED_LOAN": ("对外委托贷款取得的损益",),
    "NR_FORCE_MAJEURE_ASSET_LOSS": ("因不可抗力因素",),
    "NR_SEPARATELY_TESTED_RECEIVABLE_IMPAIRMENT_REVERSAL": ("单独进行减值测试的应收款项减值准备转回",),
    "NR_BARGAIN_PURCHASE_GAIN": ("企业取得子公司、联营企业及合营企业的投资成本",),
    "NR_COMMON_CONTROL_COMBINATION_PRECOMBINATION_PROFIT": ("同一控制下企业合并产生的子公司",),
    "NR_NONMONETARY_ASSET_EXCHANGE": ("非货币性资产交换损益",),
    "NR_DEBT_RESTRUCTURING": ("债务重组损益",),
    "NR_DISCONTINUATION_ONE_OFF_COST": ("企业因相关经营活动不再持续而发生的一次性费用",),
    "NR_LAW_ACCOUNTING_CHANGE_ONE_OFF_EFFECT": ("因税收、会计等法律、法规的调整",),
    "NR_CANCEL_MODIFY_EQUITY_INCENTIVE_ONE_OFF_SHARE_PAYMENT": ("因取消、修改股权激励计划",),
    "NR_CASH_SETTLED_SHARE_PAYMENT_FAIR_VALUE_CHANGE": ("对于现金结算的股份支付",),
    "NR_INVESTMENT_PROPERTY_FAIR_VALUE_CHANGE": ("采用公允价值模式进行后续计量的投资性房地产",),
    "NR_MANIFESTLY_UNFAIR_TRANSACTION_GAIN": ("交易价格显失公允的交易产生的收益",),
    "NR_NONCORE_CONTINGENCY_GAIN_LOSS": ("与公司正常经营业务无关的或有事项产生的损益",),
    "NR_ENTRUSTED_OPERATION_MANAGEMENT_FEE": ("受托经营取得的托管费收入",),
    "NR_OTHER_NON_OPERATING_INCOME_EXPENSE": ("除上述各项之外的其他营业外收入和支出",),
    "NR_OTHER_DEFINED_ITEM": ("其他符合非经常性损益定义的损益项目",),
    "NR_INCOME_TAX_EFFECT": ("减:所得税影响额", "所得税影响额"),
    "NR_MINORITY_INTEREST_EFFECT_AFTER_TAX": ("少数股东权益影响额(税后)",),
    "NR_REPORTED_NET_TOTAL": ("合计",),
}

ROLE_BY_FACT: Mapping[str, str] = {
    **{key: "DISCLOSED_ITEM" for key in list(NONRECURRING_DISCLOSURE_FACT_LABELS)[:22]},
    "NR_INCOME_TAX_EFFECT": "INCOME_TAX_EFFECT",
    "NR_MINORITY_INTEREST_EFFECT_AFTER_TAX": "MINORITY_INTEREST_EFFECT_AFTER_TAX",
    "NR_REPORTED_NET_TOTAL": "REPORTED_NET_TOTAL",
}

EXTRA_COLUMNS = ("disclosure_role", "source_row_label")
_SECTION_TOKENS = ("非经常性损益项目和金额", "非经常性损益项目及金额")
_SECTION_END_TOKENS = (
    "其他符合非经常性损益定义的损益项目的具体情况",
    "对公司将《公开发行证券的公司信息披露解释性公告",
    "将《公开发行证券的公司信息披露解释性公告",
)
_PAGE_NUMBER_RE = re.compile(r"^\d+\s*/\s*\d+$")


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value)).replace("：", ":")


def _is_page_noise(line: str) -> bool:
    compact = _compact(line)
    if _PAGE_NUMBER_RE.fullmatch(compact):
        return True
    if "股份有限公司" in compact and "报告" in compact and re.search(r"20\d{2}", compact):
        return True
    return False


def _blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    for start, line in enumerate(lines):
        compact_line = _compact(line)
        if not any(token in compact_line for token in _SECTION_TOKENS):
            continue
        end = min(len(lines), start + 140)
        for pos in range(start + 1, end):
            compact_candidate = _compact(lines[pos])
            if any(token in compact_candidate for token in _SECTION_END_TOKENS):
                end = pos
                break
        block = lines[start:end]
        if any(_explicit_unit_from_text(item) for item in block[:15]):
            blocks.append(block)
    return blocks


def _unit(block: list[str]) -> str | None:
    for line in block[:15]:
        unit = _explicit_unit_from_text(line)
        if unit is not None:
            return unit
    return None


def _starter_fact(line: str) -> str | None:
    compact = _compact(line)
    # Table headers contain 非经常性损益项目 but never begin with any specific
    # issuer-disclosed item prefix below. Longest prefix wins deterministically.
    matches: list[tuple[int, str]] = []
    for fact_type, prefixes in NONRECURRING_DISCLOSURE_FACT_LABELS.items():
        for prefix in prefixes:
            norm = _compact(prefix)
            if compact.startswith(norm):
                matches.append((len(norm), fact_type))
    if not matches:
        return None
    return max(matches)[1]


def _row_segments(block: list[str]) -> list[tuple[str, list[str]]]:
    starts: list[tuple[int, str]] = []
    for idx, line in enumerate(block):
        fact_type = _starter_fact(line)
        if fact_type is not None:
            starts.append((idx, fact_type))
    rows: list[tuple[str, list[str]]] = []
    for offset, (idx, fact_type) in enumerate(starts):
        end = starts[offset + 1][0] if offset + 1 < len(starts) else len(block)
        segment = [line for line in block[idx:end] if not _is_page_noise(line)]
        rows.append((fact_type, segment))
    return rows


def _amount_from_segment(segment: list[str], *, scale: float) -> float | None:
    tokens: list[str] = []
    for line in segment:
        # Skip the table section/header and applicability markers defensively.
        compact = _compact(line)
        if "单位:" in compact or "币种:" in compact or compact in {"√适用□不适用", "□适用√不适用"}:
            continue
        tokens.extend(match.group(0) for match in _NUMERIC_TOKEN_RE.finditer(line))
    if not tokens:
        return None
    # A disclosure row has exactly one amount column. Multiple numeric tokens
    # mean the PDF layout is ambiguous; fail closed instead of choosing one.
    if len(tokens) != 1:
        return None
    return float(_parse_numeric_token(tokens[0])) * scale


def _label_from_segment(segment: list[str]) -> str:
    parts: list[str] = []
    for line in segment:
        if _is_page_noise(line):
            continue
        text = _NUMERIC_TOKEN_RE.sub("", line)
        text = " ".join(text.split()).strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


@dataclass(frozen=True)
class NonRecurringDisclosureFact:
    fact_type: str
    value: float
    role: str
    source_row_label: str


def extract_nonrecurring_disclosure_facts(text: str) -> list[NonRecurringDisclosureFact]:
    lines = _normalize_text_lines(text)
    candidates = _blocks(lines)
    if not candidates:
        raise ValueError(
            "official filing has no explicit non-recurring disclosure primitives with locally proven CNY units"
        )

    parsed_candidates: list[list[NonRecurringDisclosureFact]] = []
    for block in candidates:
        unit = _unit(block)
        if unit is None:
            continue
        scale = _AMOUNT_UNIT_SCALE.get(unit)
        if scale is None:
            continue
        facts: list[NonRecurringDisclosureFact] = []
        seen: set[str] = set()
        for fact_type, segment in _row_segments(block):
            if fact_type in seen:
                # Duplicate exact row identity inside one table is ambiguous.
                facts = []
                break
            value = _amount_from_segment(segment, scale=scale)
            if value is None:
                continue
            seen.add(fact_type)
            facts.append(
                NonRecurringDisclosureFact(
                    fact_type=fact_type,
                    value=value,
                    role=ROLE_BY_FACT[fact_type],
                    source_row_label=_label_from_segment(segment),
                )
            )
        if facts and any(item.fact_type == "NR_REPORTED_NET_TOTAL" for item in facts):
            parsed_candidates.append(facts)

    if not parsed_candidates:
        raise ValueError(
            "official filing has no explicit non-recurring disclosure primitives with locally proven CNY units"
        )
    # Prefer the most complete direct disclosure block. Equal-size blocks must
    # agree exactly or the source presentation is ambiguous.
    parsed_candidates.sort(key=lambda facts: len(facts), reverse=True)
    best = parsed_candidates[0]
    if len(parsed_candidates) > 1 and len(parsed_candidates[1]) == len(best):
        sig = lambda facts: [(x.fact_type, x.value) for x in facts]
        if sig(parsed_candidates[1]) != sig(best):
            raise ValueError("official filing contains conflicting explicit non-recurring disclosure tables")
    return best


def build_nonrecurring_disclosure_fact_rows(
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
    facts = extract_nonrecurring_disclosure_facts(text)
    publication = pd.Timestamp(pd.to_datetime(publication_timestamp, errors="raise"))
    rows: list[dict[str, object]] = []
    for fact in sorted(facts, key=lambda item: item.fact_type):
        rows.append(
            {
                "entity_id": str(entity_id),
                "period_end": period_end,
                "fact_type": fact.fact_type,
                "value": float(fact.value),
                "unit": "CNY",
                "evidence_available_date": pd.Timestamp(evidence_available_date).normalize(),
                "publication_timestamp": publication.isoformat(),
                "source_identity": str(source_identity),
                "provider": str(provider),
                "document_id": str(document_id),
                "revision_id": str(revision_id),
                "document_url": str(document_url),
                "document_sha256": str(document_sha256),
                "parser_version": NONRECURRING_DISCLOSURE_PARSER_VERSION,
                "disclosure_role": fact.role,
                "source_row_label": fact.source_row_label,
            }
        )
    return pd.DataFrame(rows, columns=list(FILING_FACT_COLUMNS) + list(EXTRA_COLUMNS))


__all__ = [
    "EXTRA_COLUMNS",
    "NONRECURRING_DISCLOSURE_FACT_LABELS",
    "NONRECURRING_DISCLOSURE_PARSER_VERSION",
    "NonRecurringDisclosureFact",
    "ROLE_BY_FACT",
    "build_nonrecurring_disclosure_fact_rows",
    "extract_nonrecurring_disclosure_facts",
]
