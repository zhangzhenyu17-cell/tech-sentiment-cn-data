from __future__ import annotations

import pandas as pd
import pytest

from tech_sentiment.official_filing_accounting_balance_sheet_v1 import (
    ACCOUNTING_BALANCE_SHEET_FACT_LABELS,
    ACCOUNTING_BALANCE_SHEET_PARSER_VERSION,
    ACCOUNTING_BALANCE_SHEET_ROW_EVIDENCE_PARSER_VERSION,
    build_accounting_balance_sheet_fact_rows,
    build_accounting_balance_sheet_row_evidence_rows,
    extract_accounting_balance_sheet_facts,
    extract_accounting_balance_sheet_row_evidence,
)


def _text() -> str:
    return """
    2025年半年度报告
    母公司资产负债表
    单位：人民币元
    项目 期末余额 期初余额
    货币资金 999 888
    资产总计 9,999 8,888

    合并资产负债表
    单位：人民币万元
    项目 期末余额 期初余额
    货币资金 12,345 11,111
    交易性金融资产 101 90
    应收票据 200 180
    应收账款 3,000 2,800
    应收款项融资 300 250
    预付款项 120 110
    存货 1,500 1,400
    合同资产 80 70
    其他应收款 60 50
    流动资产合计 20,000 18,000
    长期股权投资 900 800
    投资性房地产 400 390
    固定资产 5,000 4,900
    在建工程 700 600
    使用权资产 300 280
    无形资产 1,000 950
    开发支出 100 90
    商誉 2,000 2,000
    长期待摊费用 50 45
    递延所得税资产 70 60
    其他非流动资产 130 120
    非流动资产合计 11,000 10,500
    资产总计 31,000 28,500
    短期借款 2,000 1,500
    交易性金融负债 30 20
    应付票据 500 450
    应付账款 2,500 2,300
    预收款项 40 35
    合同负债 600 550
    应付职工薪酬 300 280
    应交税费 350 330
    其他应付款 500 470
    一年内到期的非流动负债 300 200
    其他流动负债 100 90
    流动负债合计 7,220 6,225
    长期借款 4,000 3,500
    应付债券 500 450
    租赁负债 120 100
    递延所得税负债 80 70
    其他非流动负债 90 80
    非流动负债合计 4,790 4,200
    负债合计 12,010 10,425
    归属于母公司所有者权益合计 18,500 17,600
    少数股东权益 490 475
    所有者权益合计 18,990 18,075

    合并利润表
    单位：人民币万元
    营业利润 1,234 1,100
    """


def test_extracts_direct_consolidated_balance_sheet_facts_not_parent_values() -> None:
    facts = extract_accounting_balance_sheet_facts(_text())
    assert len(ACCOUNTING_BALANCE_SHEET_FACT_LABELS) == 51
    assert facts["MONETARY_FUNDS"] == pytest.approx(123_450_000.0)
    assert facts["ACCOUNTS_RECEIVABLE"] == pytest.approx(30_000_000.0)
    assert facts["ACCOUNTS_PAYABLE"] == pytest.approx(25_000_000.0)
    assert facts["FIXED_ASSETS"] == pytest.approx(50_000_000.0)
    assert facts["SHORT_TERM_BORROWINGS"] == pytest.approx(20_000_000.0)
    assert facts["MINORITY_INTEREST"] == pytest.approx(4_900_000.0)
    assert facts["TOTAL_EQUITY"] == pytest.approx(189_900_000.0)
    assert facts["TOTAL_LIABILITIES"] == pytest.approx(120_100_000.0)


def test_parser_emits_raw_facts_only_no_private_reformulation_semantics() -> None:
    facts = extract_accounting_balance_sheet_facts(_text())
    forbidden = {
        "EXCESS_CASH",
        "DEBT",
        "OPERATING_ASSETS",
        "OPERATING_LIABILITIES",
        "FINANCING_ASSETS",
        "FINANCING_LIABILITIES",
        "NOA",
        "INVESTED_CAPITAL",
        "NOPAT",
        "ROIC",
        "RNOA",
    }
    assert forbidden.isdisjoint(facts)


def test_blank_or_dash_does_not_become_zero() -> None:
    text = """
    2025年半年度报告
    合并资产负债表
    单位：人民币万元
    项目 期末余额 期初余额
    货币资金 - 100
    短期借款 20 10
    """
    facts = extract_accounting_balance_sheet_facts(text)
    assert "MONETARY_FUNDS" not in facts
    assert facts["SHORT_TERM_BORROWINGS"] == pytest.approx(200_000.0)


def test_no_explicit_balance_sheet_unit_fails_closed() -> None:
    with pytest.raises(ValueError, match="no direct consolidated balance-sheet"):
        extract_accounting_balance_sheet_facts(
            "2025年半年度报告\n合并资产负债表\n货币资金 123 100"
        )


def test_rows_preserve_pit_and_parser_identity() -> None:
    rows = build_accounting_balance_sheet_fact_rows(
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
    assert not rows.empty
    assert rows["parser_version"].eq(ACCOUNTING_BALANCE_SHEET_PARSER_VERSION).all()
    assert rows["unit"].eq("CNY").all()
    assert rows["period_end"].eq(pd.Timestamp("2025-06-30")).all()
    assert rows["evidence_available_date"].eq(pd.Timestamp("2025-08-22")).all()


def test_row_evidence_preserves_numeric_source_cell_without_private_semantics() -> None:
    evidence = extract_accounting_balance_sheet_row_evidence(_text())
    cash = evidence["MONETARY_FUNDS"]
    assert cash["source_row_label"] == "货币资金"
    assert cash["current_cell_kind"] == "NUMERIC"
    assert cash["current_value_cny"] == pytest.approx(123_450_000.0)
    assert cash["zero_interpretation_applied"] is False
    assert cash["private_classification_applied"] is False
    assert len(cash["source_row_sha256"]) == 64


def test_row_evidence_records_position_proven_dash_but_never_turns_it_into_zero() -> None:
    text = """
    2025年半年度报告
    合并资产负债表
    单位：人民币万元
    项目 期末余额 期初余额
    货币资金 - 100
    短期借款 20 10
    """
    evidence = extract_accounting_balance_sheet_row_evidence(text)
    cash = evidence["MONETARY_FUNDS"]
    assert cash["row_layout_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN"
    assert cash["current_cell_kind"] == "DASH"
    assert cash["current_cell_token"] == "-"
    assert cash["current_value_cny"] is None
    assert cash["prior_cell_kind"] == "NUMERIC"
    assert cash["zero_interpretation_applied"] is False
    facts = extract_accounting_balance_sheet_facts(text)
    assert "MONETARY_FUNDS" not in facts


def test_row_evidence_with_note_column_requires_provable_note_or_amount_layout() -> None:
    text = """
    2025年半年度报告
    合并资产负债表
    单位：人民币万元
    项目 附注 期末余额 期初余额
    货币资金 1 - 100
    应收账款 - 50
    """
    evidence = extract_accounting_balance_sheet_row_evidence(text)
    cash = evidence["MONETARY_FUNDS"]
    assert cash["row_layout_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_WITH_NOTE"
    assert cash["note_reference"] == "1"
    assert cash["current_cell_kind"] == "DASH"
    receivables = evidence["ACCOUNTS_RECEIVABLE"]
    assert receivables["row_layout_state"] == "ROW_PRESENT_LAYOUT_AMBIGUOUS"
    assert receivables["current_cell_kind"] is None
    assert receivables["current_value_cny"] is None


def test_row_evidence_rows_preserve_document_provenance_and_parser_identity() -> None:
    rows = build_accounting_balance_sheet_row_evidence_rows(
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
    assert not rows.empty
    assert rows["parser_version"].eq(ACCOUNTING_BALANCE_SHEET_ROW_EVIDENCE_PARSER_VERSION).all()
    assert rows["document_sha256"].eq("a" * 64).all()
    assert rows["period_end"].eq(pd.Timestamp("2025-06-30")).all()
    assert rows["zero_interpretation_applied"].eq(False).all()
    assert rows["private_classification_applied"].eq(False).all()
