from __future__ import annotations

import pandas as pd
import pytest

from tech_sentiment.official_filing_extended_pit import (
    EXTENDED_FILING_PARSER_VERSION,
    build_extended_filing_fact_rows,
    extract_extended_filing_facts,
)


def _sample_text() -> str:
    return """
    2025年半年度报告
    合并资产负债表 单位：人民币万元
    货币资金 12,345 11,111
    短期借款 2,000 1,500
    一年内到期的非流动负债 300 200
    长期借款 4,000 3,500
    应付债券 500 450
    租赁负债 120 100

    合并利润表 单位：人民币万元
    研发费用 888 777

    合并现金流量表 单位：人民币万元
    购建固定资产、无形资产和其他长期资产支付的现金 1,234 1,100
    期末现金及现金等价物余额 9,876 8,765
    """


def test_extract_extended_pit_primitives_requires_explicit_units_and_keeps_debt_raw() -> None:
    facts = extract_extended_filing_facts(_sample_text())

    assert facts["MONETARY_FUNDS"] == pytest.approx(123_450_000.0)
    assert facts["CASH_AND_CASH_EQUIVALENTS_END"] == pytest.approx(98_760_000.0)
    assert facts["CAPEX_CASH_PAID"] == pytest.approx(12_340_000.0)
    assert facts["R_AND_D_EXPENSE"] == pytest.approx(8_880_000.0)
    assert facts["SHORT_TERM_BORROWINGS"] == pytest.approx(20_000_000.0)
    assert facts["CURRENT_PORTION_NON_CURRENT_LIABILITIES"] == pytest.approx(
        3_000_000.0
    )
    assert facts["LONG_TERM_BORROWINGS"] == pytest.approx(40_000_000.0)
    assert facts["BONDS_PAYABLE"] == pytest.approx(5_000_000.0)
    assert facts["LEASE_LIABILITIES"] == pytest.approx(1_200_000.0)

    assert "DEBT" not in facts
    assert "CASH" not in facts


def test_extended_pit_note_column_skips_reference_and_uses_current_amount() -> None:
    text = """
    2025年半年度报告
    合并资产负债表 单位：人民币万元
    项目 附注 期末余额 期初余额
    货币资金 七、1 12,345 11,111
    短期借款 七、20 2,000 1,500
    """

    facts = extract_extended_filing_facts(text)

    assert facts["MONETARY_FUNDS"] == pytest.approx(123_450_000.0)
    assert facts["SHORT_TERM_BORROWINGS"] == pytest.approx(20_000_000.0)
    assert facts["MONETARY_FUNDS"] != pytest.approx(10_000.0)
    assert facts["SHORT_TERM_BORROWINGS"] != pytest.approx(200_000.0)


def test_extended_pit_blank_note_column_fails_closed_instead_of_guessing() -> None:
    text = """
    2025年半年度报告
    合并资产负债表 单位：人民币万元
    项目 附注 期末余额 期初余额
    货币资金 12,345 11,111

    合并利润表 单位：人民币万元
    研发费用 888 777
    """

    facts = extract_extended_filing_facts(text)

    assert "MONETARY_FUNDS" not in facts
    assert facts["R_AND_D_EXPENSE"] == pytest.approx(8_880_000.0)


def test_extended_pit_header_cannot_own_following_physical_row_label() -> None:
    from tech_sentiment.official_filing_extended_pit import _physical_line_starts_label

    labels = ("货币资金",)
    assert _physical_line_starts_label("项目 附注 期末余额 期初余额", labels) is False
    assert _physical_line_starts_label("货币资金 12,345 11,111", labels) is True
    assert _physical_line_starts_label("六、货币资金 12,345 11,111", labels) is True
    assert _physical_line_starts_label("项目 六、货币资金 12,345 11,111", labels) is False


def test_extended_pit_single_collapsed_numeric_token_fails_closed() -> None:
    text = """
    2025年半年度报告
    合并资产负债表 单位：人民币元
    货币资金 55581004502569534000000

    合并利润表 单位：人民币元
    研发费用 888 777
    """

    facts = extract_extended_filing_facts(text)

    assert "MONETARY_FUNDS" not in facts
    assert facts["R_AND_D_EXPENSE"] == pytest.approx(888.0)


def test_extended_pit_more_than_two_unproven_numeric_cells_fails_closed() -> None:
    text = """
    2025年半年度报告
    合并资产负债表 单位：人民币万元
    货币资金 1 12,345 11,111

    合并利润表 单位：人民币万元
    研发费用 888 777
    """

    facts = extract_extended_filing_facts(text)

    assert "MONETARY_FUNDS" not in facts
    assert facts["R_AND_D_EXPENSE"] == pytest.approx(8_880_000.0)


def test_extended_pit_fragmented_label_interrupted_by_numeric_cell_fails_closed() -> None:
    text = """
    2025年半年度报告
    合并资产负债表 单位：人民币万元
    货币资 1
    金 12,345 11,111

    合并利润表 单位：人民币万元
    研发费用 888 777
    """

    facts = extract_extended_filing_facts(text)

    assert "MONETARY_FUNDS" not in facts
    assert facts["R_AND_D_EXPENSE"] == pytest.approx(8_880_000.0)


def test_extended_pit_primitives_fail_closed_without_explicit_table_unit() -> None:
    with pytest.raises(ValueError, match="explicit table unit"):
        extract_extended_filing_facts(
            "2025年半年度报告\n货币资金 12345 11111\n短期借款 2000 1500"
        )


def test_historical_capex_label_with_suo_is_semantically_equivalent() -> None:
    text = """
    2024年年度报告
    5、合并现金流量表
    单位：元
    项目 2024年度 2023年度
    购建固定资产、无形资产和其他长期资产所支付的现金 862,177,116.77 1,063,204,406.67
    """

    facts = extract_extended_filing_facts(text)

    assert facts["CAPEX_CASH_PAID"] == pytest.approx(862_177_116.77)


def test_statement_scoped_unit_recovers_long_cashflow_table_rows() -> None:
    filler = "\n".join(
        f"现金流量项目{i} {i + 100}.00 {i + 90}.00" for i in range(45)
    )
    text = f"""
    2024年年度报告
    5、合并现金流量表
    单位：元
    项目 2024年度 2023年度
    {filler}
    购建固定资产、无形资产和其他长
    期资产支付的现金 862,177,116.77 1,063,204,406.67
    现金流量项目末尾 1.00 2.00
    六、期末现金及现金等价物余额 2,231,856,393.62 2,208,647,202.51
    6、母公司现金流量表
    单位：元
    项目 2024年度 2023年度
    """

    facts = extract_extended_filing_facts(text)

    assert facts["CAPEX_CASH_PAID"] == pytest.approx(862_177_116.77)
    assert facts["CASH_AND_CASH_EQUIVALENTS_END"] == pytest.approx(2_231_856_393.62)


def test_statement_scoped_unit_never_leaks_from_previous_statement() -> None:
    filler = "\n".join(f"现金流量项目{i} {i}.00" for i in range(25))
    text = f"""
    2024年年度报告
    3、合并利润表
    单位：万元
    研发费用 123 100
    5、合并现金流量表
    项目 2024年度 2023年度
    {filler}
    购建固定资产、无形资产和其他长期资产支付的现金 999 888
    """

    facts = extract_extended_filing_facts(text)

    assert facts["R_AND_D_EXPENSE"] == pytest.approx(1_230_000.0)
    assert "CAPEX_CASH_PAID" not in facts


def test_statement_scoped_unit_uses_first_consolidated_statement_not_parent_unit() -> None:
    filler = "\n".join(f"项目{i} {i}.00" for i in range(30))
    text = f"""
    2024年年度报告
    5、合并现金流量表
    单位：万元
    项目 2024年度 2023年度
    {filler}
    购建固定资产、无形资产和其他长期资产支付的现金 2 1
    6、母公司现金流量表
    单位：元
    项目 2024年度 2023年度
    购建固定资产、无形资产和其他长期资产支付的现金 999 888
    """

    facts = extract_extended_filing_facts(text)

    assert facts["CAPEX_CASH_PAID"] == pytest.approx(20_000.0)


def test_build_extended_rows_preserves_document_provenance() -> None:
    rows = build_extended_filing_fact_rows(
        entity_id="688012.SH",
        title="2025年半年度报告",
        evidence_available_date="2025-08-30",
        publication_timestamp="2025-08-29 18:30:00+08:00",
        source_identity="CNINFO_ANNOUNCEMENT_ARCHIVE",
        provider="CNINFO",
        document_id="1210000000",
        revision_id="DOCUMENT:1210000000:SHA256:" + "a" * 64,
        document_url="https://static.cninfo.com.cn/finalpage/2025-08-29/1210000000.PDF",
        document_sha256="a" * 64,
        text=_sample_text(),
    )

    assert set(rows["fact_type"]) == {
        "MONETARY_FUNDS",
        "CASH_AND_CASH_EQUIVALENTS_END",
        "CAPEX_CASH_PAID",
        "R_AND_D_EXPENSE",
        "SHORT_TERM_BORROWINGS",
        "CURRENT_PORTION_NON_CURRENT_LIABILITIES",
        "LONG_TERM_BORROWINGS",
        "BONDS_PAYABLE",
        "LEASE_LIABILITIES",
    }
    assert set(rows["unit"]) == {"CNY"}
    assert set(rows["parser_version"]) == {EXTENDED_FILING_PARSER_VERSION}
    assert EXTENDED_FILING_PARSER_VERSION == (
        "official-filing-extended-pit-primitives-v5-statement-row-ownership-safe"
    )
    assert set(rows["document_id"]) == {"1210000000"}
    assert set(rows["document_sha256"]) == {"a" * 64}
    assert set(pd.to_datetime(rows["period_end"]).dt.strftime("%Y-%m-%d")) == {
        "2025-06-30"
    }


def test_blank_statement_row_never_borrows_following_row_amounts() -> None:
    text = """
    2024年年度报告
    合并资产负债表
    单位：元
    项目 期末余额 期初余额
    应付债券
    租赁负债 14,369,849.36 26,991,783.44

    合并利润表
    单位：元
    研发费用 1 1
    """

    facts = extract_extended_filing_facts(text)

    assert "BONDS_PAYABLE" not in facts
    assert facts["LEASE_LIABILITIES"] == pytest.approx(14_369_849.36)


def test_statement_presence_blocks_global_note_table_fallback() -> None:
    text = """
    2024年年度报告
    合并资产负债表
    单位：元
    项目 期末余额 期初余额
    应付债券
    租赁负债 14,369,849.36 26,991,783.44

    合并利润表
    单位：元
    研发费用 1 1

    附注风险表
    单位：元
    应付债券 1,796,478,701.63 1,700,000,000.00
    """

    facts = extract_extended_filing_facts(text)

    assert "BONDS_PAYABLE" not in facts
    assert facts["LEASE_LIABILITIES"] == pytest.approx(14_369_849.36)
