from __future__ import annotations

import pandas as pd
import pytest

from tech_sentiment.official_filing_decision_drivers_v1 import (
    DECISION_DRIVER_PARSER_VERSION,
    build_decision_driver_fact_rows,
    extract_decision_driver_facts,
)


def _text() -> str:
    return """
    2025年半年度报告
    合并利润表
    单位：人民币万元
    项目 2025年半年度 2024年半年度
    营业利润 12,345 10,111
    利润总额 12,100 9,900
    所得税费用 1,815 1,485
    归属于母公司所有者的净利润 10,000 8,000

    母公司利润表
    单位：人民币元
    项目 2025年半年度 2024年半年度
    营业利润 999 888
    利润总额 777 666
    所得税费用 111 100
    """


def test_extracts_direct_cngaap_driver_lines_from_first_consolidated_profit_statement() -> None:
    facts = extract_decision_driver_facts(_text())
    assert facts == {
        "OPERATING_PROFIT_CN_GAAP": pytest.approx(123_450_000.0),
        "TOTAL_PROFIT_CN_GAAP": pytest.approx(121_000_000.0),
        "INCOME_TAX_EXPENSE_CN_GAAP": pytest.approx(18_150_000.0),
    }


def test_decision_driver_parser_does_not_infer_ebit_nopat_or_cash_tax() -> None:
    facts = extract_decision_driver_facts(_text())
    assert "EBIT" not in facts
    assert "NOPAT" not in facts
    assert "CASH_TAX" not in facts
    assert "EFFECTIVE_TAX_RATE" not in facts


def test_decision_driver_parser_fails_closed_without_explicit_unit() -> None:
    with pytest.raises(ValueError, match="explicit table unit"):
        extract_decision_driver_facts(
            "2025年半年度报告\n合并利润表\n营业利润 123 100\n利润总额 120 90"
        )


def test_decision_driver_parser_does_not_fall_back_to_md_and_a_when_profit_statement_exists() -> None:
    text = """
    2025年半年度报告
    管理层讨论与分析
    营业利润 999 888
    合并利润表
    单位：人民币万元
    项目 2025年半年度 2024年半年度
    营业利润 - -
    利润总额 100 90
    所得税费用 15 14
    """
    facts = extract_decision_driver_facts(text)
    assert "OPERATING_PROFIT_CN_GAAP" not in facts
    assert facts["TOTAL_PROFIT_CN_GAAP"] == pytest.approx(1_000_000.0)
    assert facts["INCOME_TAX_EXPENSE_CN_GAAP"] == pytest.approx(150_000.0)


def test_decision_driver_rows_preserve_pit_and_parser_identity() -> None:
    rows = build_decision_driver_fact_rows(
        entity_id="600276.SH",
        title="恒瑞医药2025年半年度报告",
        evidence_available_date="2025-08-22",
        publication_timestamp="2025-08-21T18:00:00+08:00",
        source_identity="CNINFO_OFFICIAL_DISCLOSURE",
        provider="cninfo",
        document_id="doc-1",
        revision_id="rev-1",
        document_url="https://static.cninfo.com.cn/doc.pdf",
        document_sha256="a" * 64,
        text=_text(),
    )
    assert set(rows["fact_type"]) == {
        "OPERATING_PROFIT_CN_GAAP",
        "TOTAL_PROFIT_CN_GAAP",
        "INCOME_TAX_EXPENSE_CN_GAAP",
    }
    assert rows["parser_version"].eq(DECISION_DRIVER_PARSER_VERSION).all()
    assert rows["unit"].eq("CNY").all()
    assert rows["period_end"].eq(pd.Timestamp("2025-06-30")).all()
    assert rows["evidence_available_date"].eq(pd.Timestamp("2025-08-22")).all()
