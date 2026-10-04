from __future__ import annotations

import pandas as pd
import pytest

from tech_sentiment.official_filing_nonrecurring_disclosure_v1 import (
    build_nonrecurring_disclosure_fact_rows,
    extract_nonrecurring_disclosure_facts,
)


def _facts(text: str) -> dict[str, float]:
    return {item.fact_type: item.value for item in extract_nonrecurring_disclosure_facts(text)}


def test_half_year_explicit_table_parses_wrapped_rows_and_reconciliation_fields() -> None:
    text = """
九、 非经常性损益项目和金额
√适用 □不适用
单位：元 币种：人民币
非经常性损益项目 金额 附注（如适用）
非流动性资产处置损益，包括已计提资产减 -1,864,170.69
值准备的冲销部分
计入当期损益的政府补助，但与公司正常经 20,012,654.88
营业务密切相关、符合国家政策规定、按照
5/110
贵州茅台酒股份有限公司2026年半年度报告
确定的标准享有、对公司损益产生持续影响
的政府补助除外
除同公司正常经营业务相关的有效套期保值
业务外，非金融企业持有金融资产和金融负 22,537,102.09
债产生的公允价值变动损益以及处置金融资
产和金融负债产生的损益
除上述各项之外的其他营业外收入和支出 29,131,392.76
其他符合非经常性损益定义的损益项目 418,450.00
减：所得税影响额 17,558,857.26
少数股东权益影响额（税后） 3,795.93
合计 52,672,775.85
对公司将《公开发行证券的公司信息披露解释性公告第1号——非经常性损益》未列举的项目认定为非经常性损益项目且金额重大的
"""
    facts = _facts(text)
    assert facts["NR_NON_CURRENT_ASSET_DISPOSAL"] == -1_864_170.69
    assert facts["NR_GOVERNMENT_GRANT"] == 20_012_654.88
    assert facts["NR_FINANCIAL_ASSET_FAIR_VALUE_AND_DISPOSAL"] == 22_537_102.09
    assert facts["NR_OTHER_NON_OPERATING_INCOME_EXPENSE"] == 29_131_392.76
    assert facts["NR_OTHER_DEFINED_ITEM"] == 418_450.0
    assert facts["NR_INCOME_TAX_EFFECT"] == 17_558_857.26
    assert facts["NR_MINORITY_INTEREST_EFFECT_AFTER_TAX"] == 3_795.93
    assert facts["NR_REPORTED_NET_TOTAL"] == 52_672_775.85
    item_sum = sum(value for key, value in facts.items() if key.startswith("NR_") and key not in {
        "NR_INCOME_TAX_EFFECT", "NR_MINORITY_INTEREST_EFFECT_AFTER_TAX", "NR_REPORTED_NET_TOTAL"
    })
    assert item_sum - facts["NR_INCOME_TAX_EFFECT"] - facts["NR_MINORITY_INTEREST_EFFECT_AFTER_TAX"] == pytest.approx(
        facts["NR_REPORTED_NET_TOTAL"], abs=0.01
    )


def test_quarter_table_does_not_zero_impute_blank_disclosed_item() -> None:
    text = """
(二)非经常性损益项目和金额
√适用 □不适用
单位:元 币种:人民币
非经常性损益项目 本期金额 说明
非流动性资产处置损益，包括已计提资产减值准备的冲销部分 -346,004.45
计入当期损益的政府补助，但与公司正常经营业务密切相关、符合国家政策规定、按照确定的标准享有、对公司损益产生持续影响的政府补助除外
除同公司正常经营业务相关的有效套期保值业务外，非金融企业持有金融资产和金融负债产生的公允价值变动损益以及处置金融资产和金融负债产生的损益 -3,077,007.20
除上述各项之外的其他营业外收入和支出 6,717,371.54
其他符合非经常性损益定义的损益项目 223,700.00
减：所得税影响额 879,514.97
少数股东权益影响额（税后） 110,852.88
合计 2,527,692.04
对公司将《公开发行证券的公司信息披露解释性公告第1号——非经常性损益》未列举的项目认定为非经常性损益项目且金额重大的
"""
    facts = _facts(text)
    assert "NR_GOVERNMENT_GRANT" not in facts
    assert facts["NR_REPORTED_NET_TOTAL"] == 2_527_692.04


def test_table_unit_is_required_and_never_inferred() -> None:
    text = """
非经常性损益项目和金额
非经常性损益项目 金额
非流动性资产处置损益 100.00
减：所得税影响额 25.00
合计 75.00
对公司将《公开发行证券的公司信息披露解释性公告第1号——非经常性损益》未列举的项目
"""
    with pytest.raises(ValueError, match="no explicit non-recurring"):
        extract_nonrecurring_disclosure_facts(text)


def test_builder_preserves_direct_disclosure_identity_and_provenance() -> None:
    text = """
非经常性损益项目和金额
单位：万元 币种：人民币
非经常性损益项目 金额
非流动性资产处置损益 1.50
减：所得税影响额 0.30
少数股东权益影响额（税后） 0.10
合计 1.10
对公司将《公开发行证券的公司信息披露解释性公告第1号——非经常性损益》未列举的项目
"""
    rows = build_nonrecurring_disclosure_fact_rows(
        entity_id="600519.SH",
        title="贵州茅台2026年第一季度报告",
        evidence_available_date="2026-04-30",
        publication_timestamp="2026-04-29T18:00:00+08:00",
        source_identity="CNINFO_OFFICIAL_DISCLOSURE",
        provider="CNINFO",
        document_id="doc-1",
        revision_id="rev-1",
        document_url="https://static.cninfo.com.cn/doc.pdf",
        document_sha256="a" * 64,
        text=text,
    )
    assert set(rows["fact_type"]) == {
        "NR_NON_CURRENT_ASSET_DISPOSAL",
        "NR_INCOME_TAX_EFFECT",
        "NR_MINORITY_INTEREST_EFFECT_AFTER_TAX",
        "NR_REPORTED_NET_TOTAL",
    }
    assert rows.loc[rows["fact_type"].eq("NR_NON_CURRENT_ASSET_DISPOSAL"), "value"].iloc[0] == 15_000.0
    assert rows["period_end"].eq(pd.Timestamp("2026-03-31")).all()
    assert rows["unit"].eq("CNY").all()
    assert rows["disclosure_role"].isin({"DISCLOSED_ITEM", "INCOME_TAX_EFFECT", "MINORITY_INTEREST_EFFECT_AFTER_TAX", "REPORTED_NET_TOTAL"}).all()


def test_szse_header_ji_amount_and_explanatory_section_end_are_supported() -> None:
    text = """
六、非经常性损益项目及金额
适用 □不适用
单位：千元
项目 金额 说明
非流动性资产处置损益（包括已计提资产减值准备的冲销部分） 228,681
除同公司正常经营业务相关的有效套期保值业务外，非金融企业
持有金融资产和金融负债产生的公允价值变动损益以及处置金融 1,536,019
资产和金融负债产生的损益
单独进行减值测试的应收款项减值准备转回 1,590
除上述各项之外的其他营业外收入和支出 102,100
其他符合非经常性损益定义的损益项目 4,540,958
减：所得税影响额 1,476,303
少数股东权益影响额（税后） 662,343
合计 4,270,703
其他符合非经常性损益定义的损益项目的具体情况：
适用 □不适用
主要是其他收益。
合计 999,999,999
"""
    facts = _facts(text)
    assert facts["NR_NON_CURRENT_ASSET_DISPOSAL"] == 228_681_000.0
    assert facts["NR_FINANCIAL_ASSET_FAIR_VALUE_AND_DISPOSAL"] == 1_536_019_000.0
    assert facts["NR_OTHER_DEFINED_ITEM"] == 4_540_958_000.0
    assert facts["NR_REPORTED_NET_TOTAL"] == 4_270_703_000.0
