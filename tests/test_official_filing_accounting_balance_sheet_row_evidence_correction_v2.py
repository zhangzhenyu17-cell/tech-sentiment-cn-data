from __future__ import annotations
import hashlib
import pytest
from tech_sentiment.official_filing_accounting_balance_sheet_row_evidence_correction_v2 import extract_row_evidence_correction


def test_correction_skips_change_analysis_and_rekeys_exact_statement_row() -> None:
    text="""
    2026年半年度报告
    1、合并资产负债表项目
    单位：元
    项目 2026年6月30日 2025年12月31日 变动金额 变动比例 变动说明
    应收票据 - 58 -58 -100% 原因说明

    1、合并资产负债表
    2026年6月30日
    单位：元
    项目 期末余额 期初余额
    应收票据 30 40
    """
    old=hashlib.sha256("应收票据 - 58 -58 -100% 原因说明".encode()).hexdigest()
    row=extract_row_evidence_correction(text,fact_type="NOTES_RECEIVABLE",supersedes_source_row_sha256=old)
    assert row["current_cell_kind"]=="NUMERIC"
    assert row["current_value_cny"]==pytest.approx(30.0)
    assert row["source_row_sha256"]!=old
    assert row["source_row_mismatch_confirmed"] is True
    assert row["zero_interpretation_applied"] is False
