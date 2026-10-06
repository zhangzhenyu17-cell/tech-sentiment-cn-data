from __future__ import annotations

import pandas as pd
import pytest

from tech_sentiment.official_filing_accounting_balance_sheet_layout_proof_v1 import (
    ACCOUNTING_BALANCE_SHEET_LAYOUT_PROOF_PARSER_VERSION,
    extract_accounting_balance_sheet_layout_proof,
    extract_accounting_balance_sheet_layout_proofs,
)

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


def test_layout_proof_recognizes_note_blank_two_dash_amount_cells_without_zero() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    单位：人民币元
    项目 附注 期末余额 期初余额
    应付债券 - -
    负债合计 100 90
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="BONDS_PAYABLE")
    assert proof["parser_version"] == ACCOUNTING_BALANCE_SHEET_LAYOUT_PROOF_PARSER_VERSION
    assert proof["statement_amount_columns_declared"] is True
    assert proof["statement_has_note_column"] is True
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_NOTE_BLANK"
    assert proof["current_cell_kind"] == "DASH"
    assert proof["prior_cell_kind"] == "DASH"
    assert proof["current_value_cny"] is None
    assert proof["zero_interpretation_applied"] is False
    assert proof["private_classification_applied"] is False


def test_layout_proof_recognizes_exact_blank_row_but_does_not_impute_zero() -> None:
    text = """
    2025年半年度报告
    合并资产负债表
    单位：人民币万元
    项目 期末余额 期初余额
    交易性金融资产
    应收账款 20 10
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="TRADING_FINANCIAL_ASSETS")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_POSITION_PROVEN_BLANK_BOTH"
    assert proof["current_cell_kind"] == "BLANK"
    assert proof["prior_cell_kind"] == "BLANK"
    assert proof["current_value_cny"] is None
    assert proof["zero_interpretation_applied"] is False


def test_layout_proof_keeps_note_plus_single_amount_unresolved() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    单位：人民币元
    项目 附注 期末余额 期初余额
    应付债券 12 500
    负债合计 1000 900
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="BONDS_PAYABLE")
    assert proof["layout_proof_state"] == "ROW_PRESENT_NOTE_PLUS_SINGLE_AMOUNT_UNRESOLVED"
    assert proof["current_cell_kind"] is None
    assert proof["current_value_cny"] is None
    assert proof["zero_interpretation_applied"] is False


def test_layout_proof_accepts_two_explicit_balance_sheet_date_columns() -> None:
    text = """
    2024年年度报告
    合并资产负债表
    2024 年 12 月 31 日
    单位：人民币元
    项目 附注 2024 年 12 月 31 日 2023 年 12 月 31 日
    应付债券 - -
    负债合计 100 90
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="BONDS_PAYABLE")
    assert proof["statement_amount_columns_declared"] is True
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_NOTE_BLANK"
    assert proof["current_cell_kind"] == "DASH"
    assert proof["prior_cell_kind"] == "DASH"
    assert proof["zero_interpretation_applied"] is False


def test_layout_proof_uses_header_positions_to_assign_single_prior_amount() -> None:
    text = """
    2024年年度报告
    合并资产负债表
    2024 年 12 月 31 日
    单位：人民币元
              项目                      附注            2024 年 12 月 31 日         2023 年 12 月 31 日
    使用权资产                                                                             8,759,664.03
    资产总计                                             100,000,000.00              90,000,000.00
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="RIGHT_OF_USE_ASSETS")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_POSITION_PROVEN"
    assert proof["current_cell_kind"] == "BLANK"
    assert proof["prior_cell_kind"] == "NUMERIC"
    assert proof["prior_cell_token"] == "8,759,664.03"
    assert proof["current_value_cny"] is None
    assert proof["zero_interpretation_applied"] is False


def test_layout_proof_position_route_keeps_blank_current_as_blank_not_zero() -> None:
    text = """
    2024年年度报告
    合并资产负债表
    2024 年 12 月 31 日
    单位：人民币元
              项目                      附注            2024 年 12 月 31 日         2023 年 12 月 31 日
    长期借款                                                                           638,279,169.17
    负债合计                                             100,000,000.00              90,000,000.00
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="LONG_TERM_BORROWINGS")
    assert proof["current_cell_kind"] == "BLANK"
    assert proof["prior_cell_kind"] == "NUMERIC"
    assert proof["current_value_cny"] is None
    assert proof["zero_interpretation_applied"] is False


def test_layout_proof_does_not_positionally_treat_note_id_as_amount_when_note_anchor_is_on_other_header_line() -> None:
    text = """
    2024年年度报告
    合并资产负债表
    单位：人民币元
              项目                      附注
                                      2024 年 12 月 31 日         2023 年 12 月 31 日
    应付债券                            12
    负债合计                                             100,000,000.00              90,000,000.00
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="BONDS_PAYABLE")
    assert proof["statement_has_note_column"] is True
    assert proof["statement_amount_columns_declared"] is True
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_NOTE_ONLY_AMOUNTS_BLANK"
    assert proof["note_reference"] == "12"
    assert proof["current_cell_kind"] == "BLANK"
    assert proof["current_value_cny"] is None
    assert proof["zero_interpretation_applied"] is False


def test_layout_proof_accepts_chinese_parenthesized_note_reference_without_using_it_as_amount() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    单位：人民币元
    项目 附注 期末余额 期初余额
    货币资金 七（1） 31,597,464,469.62 32,830,782,585.55
    负债合计 100 90
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="MONETARY_FUNDS")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_WITH_NOTE"
    assert proof["note_reference"] == "七(1)"
    assert proof["current_cell_token"] == "31,597,464,469.62"
    assert proof["prior_cell_token"] == "32,830,782,585.55"
    assert proof["zero_interpretation_applied"] is False


def test_layout_proof_accepts_chinese_dot_note_reference_with_two_amount_cells() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    单位：人民币元
    项目 附注 期末余额 期初余额
    应付债券 七.46 0 0
    负债合计 100 90
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="BONDS_PAYABLE")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_WITH_NOTE"
    assert proof["note_reference"] == "七.46"
    assert proof["current_cell_kind"] == "NUMERIC"
    assert proof["current_value_cny"] == 0
    assert proof["prior_cell_kind"] == "NUMERIC"
    assert proof["zero_interpretation_applied"] is False


def test_layout_proof_keeps_textual_note_plus_one_amount_unresolved() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    单位：人民币元
    项目 附注 期末余额
    期初余额
    应付债券 七.46 500
    负债合计 1000 900
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="BONDS_PAYABLE")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMN_OWNERSHIP_UNRESOLVED"
    assert proof["current_value_cny"] is None
    assert proof["zero_interpretation_applied"] is False


def test_layout_proof_accepts_compound_parenthesized_note_reference() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    单位：人民币元
    项目 附注 期末余额 期初余额
    应收账款 七（5）（71） 481,587,422 422,932,937
    资产总计 1000 900
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="ACCOUNTS_RECEIVABLE")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_WITH_NOTE"
    assert proof["note_reference"] == "七(5)(71)"
    assert proof["current_cell_token"] == "481,587,422"
    assert proof["prior_cell_token"] == "422,932,937"
    assert proof["zero_interpretation_applied"] is False


def test_layout_proof_accepts_year_start_count_header() -> None:
    text = """
    2026年第一季度报告
    合并资产负债表
    金额单位：人民币元
    项目 期末数 年初数
    货币资金 8,885,636,676.51 9,305,381,624.06
    资产总计 1000 900
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="MONETARY_FUNDS")
    assert proof["statement_amount_columns_declared"] is True
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN"
    assert proof["current_cell_token"] == "8,885,636,676.51"
    assert proof["prior_cell_token"] == "9,305,381,624.06"


def test_bulk_layout_proof_matches_single_fact_semantics() -> None:
    fact_types = ["MONETARY_FUNDS", "ACCOUNTS_RECEIVABLE", "BONDS_PAYABLE"]
    bulk = extract_accounting_balance_sheet_layout_proofs(_text(), fact_types=fact_types)
    assert set(bulk) == set(fact_types)
    for fact_type in fact_types:
        assert bulk[fact_type] == extract_accounting_balance_sheet_layout_proof(
            _text(), fact_type=fact_type
        )


def test_layout_proof_strips_hyphenated_chinese_note_reference_before_amount_cells() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    2025 年 12 月 31 日
    单位：元 币种：人民币
    项目 附注 2025年12月31日 2024年12月31日
    货币资金 七-1 6,367,991,239.76 8,667,063,566.99
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="MONETARY_FUNDS")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_WITH_NOTE"
    assert proof["note_reference"] == "七-1"
    assert proof["current_value_cny"] == pytest.approx(6_367_991_239.76)


def test_layout_proof_strips_parenthesized_chinese_note_reference_before_amount_cells() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    2025 年 12 月 31 日
    单位：元 币种：人民币
    项目 附注 2025年12月31日 2024年12月31日
    货币资金 七 (1) 17,140,010,938.17 13,255,671,486.27
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="MONETARY_FUNDS")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_WITH_NOTE"
    assert proof["note_reference"] == "七(1)"
    assert proof["current_value_cny"] == pytest.approx(17_140_010_938.17)


def test_layout_proof_skips_balance_sheet_change_analysis_and_uses_exact_statement() -> None:
    text = """
    1、合并资产负债表项目
    单位：元
    项目 2026年6月30日 2025年12月31日 变动金额 变动比例 变动说明
    应收票据 - 58,052,380.37 -58,052,380.37 -100.00%

    1、合并资产负债表
    2026年6月30日
    单位：元
    项目 期末余额 期初余额
    应收票据 10 20
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="NOTES_RECEIVABLE")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_POSITION_PROVEN"
    assert proof["current_value_cny"] == pytest.approx(10.0)


def test_layout_proof_strips_delimited_parenthesized_note_reference() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    2025年12月31日
    单位：元 币种：人民币
    项目 附注 2025年12月31日 2024年12月31日
    货币资金 七、（1） 14,557,574,646.66 10,978,262,688.04
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="MONETARY_FUNDS")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_PROVEN_WITH_NOTE"
    assert proof["note_reference"] == "七、(1)"
    assert proof["current_value_cny"] == pytest.approx(14_557_574_646.66)


def test_layout_proof_uses_common_shift_to_prove_blank_note_two_amount_cells() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    2025年12月31日
    单位：元 币种：人民币
           项目                 附注          期末余额                  期初余额
      开发支出                                               0                         0
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="DEVELOPMENT_EXPENDITURE")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_LARGE_GAP_PROVEN_NOTE_BLANK"
    assert proof["current_cell_token"] == "0"
    assert proof["prior_cell_token"] == "0"


def test_layout_proof_recovers_two_spaced_digit_amount_chunks_only_with_large_gaps() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    2025年12月31日
    单位：元
    项目                                      期末余额                         期初余额
    货币资金        1  0,  07  4,  02  8,  50  4.  63        9,249,724,836.53
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="MONETARY_FUNDS")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_LARGE_GAP_PROVEN"
    assert proof["current_value_cny"] == pytest.approx(10_074_028_504.63)
    assert proof["prior_cell_token"] == "9,249,724,836.53"


def test_layout_proof_uses_partial_second_date_anchor_when_day_suffix_wraps() -> None:
    text = """
    2025年年度报告
    合并资产负债表
    2025年12月31日
    单位：元 币种：人民币
                      项目                       附注    2025 年 12月  31日    2024年 12 月 31
                                                                         日
      开发支出                                                                       5,728,094.32
    """
    proof = extract_accounting_balance_sheet_layout_proof(text, fact_type="DEVELOPMENT_EXPENDITURE")
    assert proof["layout_proof_state"] == "ROW_PRESENT_AMOUNT_COLUMNS_POSITION_PROVEN"
    assert proof["current_cell_kind"] == "BLANK"
    assert proof["prior_cell_kind"] == "NUMERIC"
    assert proof["prior_cell_token"] == "5,728,094.32"
    assert proof["current_value_cny"] is None
