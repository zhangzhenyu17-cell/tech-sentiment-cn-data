import json
import sys
from types import SimpleNamespace
from urllib.error import HTTPError

import pandas as pd
import pytest

import tech_sentiment.official_filing_facts as filing_module
from tech_sentiment.official_filing_facts import (
    FILING_PRESENTATION_BODY,
    FILING_PRESENTATION_FULL,
    FILING_PRESENTATION_SUMMARY,
    classify_official_filing_presentation,
    latest_filing_fact_as_of,
    download_official_document,
    build_filing_fact_rows,
    derive_fundamental_trend_evidence,
    extract_pdf_text,
    extract_standard_filing_facts,
    filing_period_end_from_title,
)


def test_filing_period_end_is_derived_from_the_versioned_report_title():
    assert filing_period_end_from_title("某公司2025年年度报告") == pd.Timestamp("2025-12-31")
    assert filing_period_end_from_title("某公司2026年第一季度报告") == pd.Timestamp("2026-03-31")
    assert filing_period_end_from_title("某公司2026年半年度报告（修订版）") == pd.Timestamp("2026-06-30")
    assert filing_period_end_from_title("某公司2026年第三季度报告") == pd.Timestamp("2026-09-30")




def test_pdf_text_falls_back_to_plain_only_when_layout_loses_explicit_unit(monkeypatch):
    calls: list[str] = []

    class FakePage:
        def extract_text(self, extraction_mode=None):
            if extraction_mode == "layout":
                calls.append("layout")
                return (
                    "主要会计数据\n"
                    "营业收入 124,099,843,771.99 106,190,154,843.76\n"
                    "归属于上市公司股东的净利润 62,716,443,738.27 52,460,144,378.16\n"
                )
            calls.append("plain")
            return (
                "主要会计数据\n"
                "单位：元 币种：人民币\n"
                "营业收入 124,099,843,771.99 106,190,154,843.76\n"
                "归属于上市公司股东的净利润 62,716,443,738.27 52,460,144,378.16\n"
            )

    class FakeReader:
        def __init__(self, stream, strict=False):
            self.pages = [FakePage()]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakeReader))

    text = extract_pdf_text(b"%PDF-fixture")
    facts = extract_standard_filing_facts(text)

    assert calls == ["layout", "plain"]
    assert "单位：元" in text
    assert facts["OPERATING_REVENUE"] == 124_099_843_771.99
    assert facts["NET_PROFIT_PARENT"] == 62_716_443_738.27


def test_pdf_text_keeps_layout_when_layout_already_has_explicit_unit(monkeypatch):
    calls: list[str] = []

    class FakePage:
        def extract_text(self, extraction_mode=None):
            if extraction_mode == "layout":
                calls.append("layout")
                return "单位：元 币种：人民币\n营业收入 120.00 100.00"
            calls.append("plain")
            return "plain should not be selected"

    class FakeReader:
        def __init__(self, stream, strict=False):
            self.pages = [FakePage()]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakeReader))

    text = extract_pdf_text(b"%PDF-fixture")

    assert calls == ["layout"]
    assert text.startswith("单位：元")



def test_pdf_text_uses_pdfminer_text_layer_when_both_pypdf_modes_lose_unit(monkeypatch):
    pypdf_calls: list[str] = []

    class FakePage:
        def extract_text(self, extraction_mode=None):
            pypdf_calls.append("layout" if extraction_mode == "layout" else "plain")
            return (
                "主要会计数据\n"
                "营业收入 124,099,843,771.99 106,190,154,843.76\n"
                "归属于上市公司股东的净利润 62,716,443,738.27 52,460,144,378.16\n"
            )

    class FakeReader:
        def __init__(self, stream, strict=False):
            self.pages = [FakePage()]

    class MinerPage:
        def extract_text(self, **kwargs):
            return (
                "3.1 近3年的主要会计数据和财务指标\n"
                "单位：元 币种：人民币\n"
                "营业收入 124,099,843,771.99 106,190,154,843.76\n"
                "归属于上市公司股东的净利润 62,716,443,738.27 52,460,144,378.16\n"
            )

    class FakePdf:
        pages = [MinerPage()]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakeReader))
    monkeypatch.setitem(
        sys.modules,
        "pdfplumber",
        SimpleNamespace(open=lambda stream: FakePdf()),
    )

    text = extract_pdf_text(b"%PDF-fixture")
    facts = extract_standard_filing_facts(text)

    assert pypdf_calls == ["layout", "plain"]
    assert "单位：元" in text
    assert facts["OPERATING_REVENUE"] == 124_099_843_771.99
    assert facts["NET_PROFIT_PARENT"] == 62_716_443_738.27


def test_600519_2022_summary_table_layout_remains_fail_closed_and_complete():
    text = """
    3 公司主要会计数据和财务指标
    3.1 近 3 年的主要会计数据和财务指标
    单位：元 币种：人民币
    总资产 254,364,804,995.25 255,168,195,159.90 -0.31 213,395,810,527.46
    营业收入 124,099,843,771.99 106,190,154,843.76 16.87 94,915,380,916.72
    归属于上市公司股东的净
    利润
    62,716,443,738.27 52,460,144,378.16 19.55 46,697,285,429.81
    经营活动产生的现金流量
    净额
    36,698,595,830.03 64,028,676,147.37 -42.68 51,669,068,693.03
    基本每股收益（元／股） 49.93 41.76 19.55 37.17
    """
    facts = extract_standard_filing_facts(text)

    assert facts["OPERATING_REVENUE"] == 124_099_843_771.99
    assert facts["NET_PROFIT_PARENT"] == 62_716_443_738.27
    assert facts["OPERATING_CASH_FLOW_NET"] == 36_698_595_830.03
    assert facts["BASIC_EPS"] == 49.93
    assert facts["NET_PROFIT_MARGIN"] == pytest.approx(
        62_716_443_738.27 / 124_099_843_771.99
    )



def test_explicit_unit_survives_unicode_controls_and_three_line_split():
    text = """
    主要会计数据
    单\u200b 位 ：
    人 民 币
    元 币\u0000种 ： 人 民 币
    营业收入 124,099,843,771.99 106,190,154,843.76
    归属于上市公司股东的净利润 62,716,443,738.27 52,460,144,378.16
    """
    facts = extract_standard_filing_facts(text)
    assert facts["OPERATING_REVENUE"] == 124_099_843_771.99
    assert facts["NET_PROFIT_PARENT"] == 62_716_443_738.27
    assert facts["NET_PROFIT_MARGIN"] == pytest.approx(
        62_716_443_738.27 / 124_099_843_771.99
    )


def test_pdf_text_uses_pymupdf_when_other_text_engines_lose_explicit_unit(monkeypatch):
    class FakePypdfPage:
        def extract_text(self, extraction_mode=None):
            return (
                "主要会计数据\n"
                "营业收入 124,099,843,771.99 106,190,154,843.76\n"
                "归属于上市公司股东的净利润 62,716,443,738.27 52,460,144,378.16\n"
            )

    class FakeReader:
        def __init__(self, stream, strict=False):
            self.pages = [FakePypdfPage()]

    class MinerPage:
        def extract_text(self, **kwargs):
            return "主要会计数据\n营业收入 1 1"

    class FakeMinerPdf:
        pages = [MinerPage()]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class MuPage:
        def get_text(self, mode, sort=False):
            assert mode == "text"
            assert sort is True
            return (
                "主要会计数据\n"
                "单 位 ： 人 民 币\n"
                "元 币 种 ： 人 民 币\n"
                "营业收入 124,099,843,771.99 106,190,154,843.76\n"
                "归属于上市公司股东的净利润 62,716,443,738.27 52,460,144,378.16\n"
            )

    class MuDoc:
        def __iter__(self):
            return iter([MuPage()])

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=FakeReader))
    monkeypatch.setitem(
        sys.modules,
        "pdfplumber",
        SimpleNamespace(open=lambda stream: FakeMinerPdf()),
    )
    monkeypatch.setitem(
        sys.modules,
        "pymupdf",
        SimpleNamespace(open=lambda **kwargs: MuDoc()),
    )

    text = extract_pdf_text(b"%PDF-fixture")
    facts = extract_standard_filing_facts(text)

    assert "单 位" in text
    assert facts["OPERATING_REVENUE"] == 124_099_843_771.99
    assert facts["NET_PROFIT_PARENT"] == 62_716_443_738.27


def test_three_line_spaced_explicit_wanyuan_unit_is_normalized_to_cny():
    text = """
    单 位 ：
    人 民 币
    万 元
    营业收入 10 9
    """
    facts = extract_standard_filing_facts(text)
    assert facts["OPERATING_REVENUE"] == 100_000.0

def test_standard_filing_facts_require_proven_yuan_units_and_do_not_fill():
    text = """
    主要会计数据和财务指标  单位：元 币种：人民币
    营业收入 1,200.00 1,000.00
    归属于上市公司股东的净利润 120.00 100.00
    经营活动产生的现金流量净额 90.00 80.00
    总资产 5,000.00 4,800.00
    归属于上市公司股东的所有者权益 3,000.00 2,900.00
    基本每股收益 1.20 1.00
    """
    facts = extract_standard_filing_facts(text)
    assert facts["OPERATING_REVENUE"] == 1200.0
    assert facts["NET_PROFIT_PARENT"] == 120.0
    assert facts["NET_PROFIT_MARGIN"] == pytest.approx(0.1)
    assert facts["BASIC_EPS"] == 1.2
    assert "NONEXISTENT" not in facts

    scaled = extract_standard_filing_facts("单位：万元\n营业收入 10 9")
    assert scaled["OPERATING_REVENUE"] == 100_000.0




def test_explicit_yuan_unit_tolerates_pdf_layout_whitespace_and_line_wrap():
    text = """
    主要会计数据
    单 位 ： 人 民 币
    元 币 种 ： 人 民 币
    营业收入 1,200.00 1,000.00
    归属于上市公司股东的净利润 120.00 100.00
    """
    facts = extract_standard_filing_facts(text)
    assert facts["OPERATING_REVENUE"] == 1200.0
    assert facts["NET_PROFIT_PARENT"] == 120.0
    assert facts["NET_PROFIT_MARGIN"] == pytest.approx(0.1)


def test_spaced_explicit_wanyuan_unit_is_normalized_to_cny():
    text = """
    单 位 ： 万 元
    营业收入 10 9
    """
    facts = extract_standard_filing_facts(text)
    assert facts["OPERATING_REVENUE"] == 100_000.0

def test_standard_filing_facts_handle_real_sse_wrapped_600519_layout():
    # Representative pypdf layout from the official 600519 2023 annual report:
    # long Chinese labels and the EPS unit are visually wrapped across lines.
    text = """
    近三年主要会计数据和财务指标
    (一) 主要会计数据
    单位：元 币种：人民币
    主要会计数据 2023年 2022年 本期比上年同期增减(%) 2021年
    营业收入 147,693,604,994.14 124,099,843,771.99 19.01 106,190,154,843.76
    归属于上市公
    司股东的净利 74,734,071,550.75 62,717,467,870.12 19.16 52,435,506,622.16
    润
    经营活动产生
    的现金流量净 66,593,247,721.09 36,698,595,830.03 81.46 64,028,676,147.37
    额
    (二) 主要财务指标
    基本每股收益（元
    ／股） 59.49 49.93 19.16 41.74
    """

    facts = extract_standard_filing_facts(text)

    assert facts["OPERATING_REVENUE"] == 147_693_604_994.14
    assert facts["NET_PROFIT_PARENT"] == 74_734_071_550.75
    assert facts["OPERATING_CASH_FLOW_NET"] == 66_593_247_721.09
    assert facts["BASIC_EPS"] == 59.49
    assert facts["NET_PROFIT_MARGIN"] == pytest.approx(
        74_734_071_550.75 / 147_693_604_994.14
    )


def test_wrapped_explicit_wanyuan_fact_is_normalized_to_cny():
    text = """
    单位：万元 币种：人民币
    归属于上市公司
    股东的净利润 10 9
    """
    facts = extract_standard_filing_facts(text)
    assert facts["NET_PROFIT_PARENT"] == 100_000.0


def test_explicit_amount_units_normalize_to_cny_without_inference():
    cases = [
        ("千元", 1_000.0),
        ("万元", 10_000.0),
        ("百万元", 1_000_000.0),
        ("亿元", 100_000_000.0),
    ]
    for unit, scale in cases:
        facts = extract_standard_filing_facts(
            f"""
            主要会计数据 单位：人民币{unit} 币种：人民币
            营业收入 12.5 10.0
            归属于母公司所有者的净利润 1.25 1.0
            经营活动产生的现金流量净额 2.5 2.0
            基本每股收益（元/股） 0.50 0.40
            """
        )
        assert facts["OPERATING_REVENUE"] == pytest.approx(12.5 * scale)
        assert facts["NET_PROFIT_PARENT"] == pytest.approx(1.25 * scale)
        assert facts["OPERATING_CASH_FLOW_NET"] == pytest.approx(2.5 * scale)
        assert facts["BASIC_EPS"] == pytest.approx(0.50)
        assert facts["NET_PROFIT_MARGIN"] == pytest.approx(0.1)


def test_long_wrapped_parent_profit_label_is_reconstructed_without_cross_row_inference():
    text = """
    单位：元 币种：人民币
    归属于
    母公司
    所有者的
    净
    利润
    123.00 100.00
    营业收入 1,000.00 900.00
    """
    facts = extract_standard_filing_facts(text)
    assert facts["NET_PROFIT_PARENT"] == pytest.approx(123.0)
    assert facts["OPERATING_REVENUE"] == pytest.approx(1_000.0)


def test_explicit_qianyuan_unit_split_across_five_lines_is_detected():
    text = """
    单
    位：
    人民
    币千
    元
    营业收入 12.5 10.0
    """
    facts = extract_standard_filing_facts(text)
    assert facts["OPERATING_REVENUE"] == pytest.approx(12_500.0)


def _facts(
    title: str,
    available: str,
    doc: str,
    revenue: float,
    profit: float,
    published: str | None = None,
) -> pd.DataFrame:
    text = f"""
    主要会计数据和财务指标 单位：元 币种：人民币
    营业收入 {revenue} {revenue - 1}
    归属于上市公司股东的净利润 {profit} {profit - 1}
    """
    return build_filing_fact_rows(
        entity_id="600000.SH",
        title=title,
        evidence_available_date=available,
        publication_timestamp=published or available,
        source_identity="CNINFO_ANNOUNCEMENT_ARCHIVE",
        provider="CNINFO",
        document_id=doc,
        revision_id=f"DOCUMENT:{doc}",
        document_url=f"https://static.cninfo.com.cn/finalpage/{doc}.PDF",
        document_sha256=(doc * 64)[:64],
        text=text,
    )


def test_derived_trends_use_only_prior_version_available_as_of_current_filing():
    old_2024 = _facts("2024年年度报告", "2025-04-20", "a", 100.0, 10.0)
    current_2025 = _facts("2025年年度报告", "2026-04-20", "b", 120.0, 12.0)
    future_restatement_2024 = _facts("2024年年度报告（修订版）", "2026-06-01", "c", 200.0, 20.0)
    evidence = derive_fundamental_trend_evidence(
        pd.concat([old_2024, current_2025, future_restatement_2024], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "b")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "a"
    assert payload["yoy_change"] == pytest.approx(0.2)
    assert pd.Timestamp(revenue["evidence_available_date"]) == pd.Timestamp("2026-04-20")


def test_later_restatement_is_append_only_not_history_rewrite():
    original = _facts("2024年年度报告", "2025-04-20", "a", 100.0, 10.0)
    restated = _facts("2024年年度报告（修订版）", "2025-06-01", "r", 110.0, 11.0)
    current = _facts("2025年年度报告", "2026-04-20", "b", 121.0, 12.1)
    evidence = derive_fundamental_trend_evidence(
        pd.concat([original, restated, current], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "b")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "r"
    assert payload["yoy_change"] == pytest.approx(0.1)


def test_same_close_date_revision_uses_official_publication_order_not_document_id():
    original = _facts(
        "2024年年度报告",
        "2025-04-21",
        "z_original",
        100.0,
        10.0,
        published="2025-04-21 09:00:00",
    )
    revision = _facts(
        "2024年年度报告（修订版）",
        "2025-04-21",
        "a_revision",
        110.0,
        11.0,
        published="2025-04-21 14:00:00",
    )
    current = _facts(
        "2025年年度报告",
        "2026-04-20",
        "current",
        121.0,
        12.1,
        published="2026-04-20 10:00:00",
    )
    evidence = derive_fundamental_trend_evidence(
        pd.concat([original, revision, current], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "current")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "a_revision"
    assert payload["yoy_change"] == pytest.approx(0.1)


def test_same_timestamp_explicit_revision_title_supersedes_original_without_id_order():
    original = _facts(
        "2024年年度报告",
        "2025-04-21",
        "z_original",
        100.0,
        10.0,
        published="2025-04-21 10:00:00",
    )
    revision = _facts(
        "2024年年度报告（修订版）",
        "2025-04-21",
        "a_revision",
        110.0,
        11.0,
        published="2025-04-21 10:00:00",
    )
    current = _facts(
        "2025年年度报告",
        "2026-04-20",
        "current",
        121.0,
        12.1,
        published="2026-04-20 10:00:00",
    )
    original["filing_title"] = "2024年年度报告"
    revision["filing_title"] = "2024年年度报告（修订版）"
    current["filing_title"] = "2025年年度报告"

    evidence = derive_fundamental_trend_evidence(
        pd.concat([original, revision, current], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "current")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "a_revision"
    assert payload["yoy_change"] == pytest.approx(0.1)


def test_same_timestamp_update_after_title_is_explicit_revision():
    original = _facts(
        "2024年年度报告",
        "2025-04-21",
        "z_original",
        100.0,
        10.0,
        published="2025-04-21 10:00:00",
    )
    revision = _facts(
        "2024年年度报告（更新后）",
        "2025-04-21",
        "a_updated",
        110.0,
        11.0,
        published="2025-04-21 10:00:00",
    )
    current = _facts(
        "2025年年度报告",
        "2026-04-20",
        "current",
        121.0,
        12.1,
        published="2026-04-20 10:00:00",
    )
    original["filing_title"] = "2024年年度报告"
    revision["filing_title"] = "2024年年度报告（更新后）"
    current["filing_title"] = "2025年年度报告"

    evidence = derive_fundamental_trend_evidence(
        pd.concat([original, revision, current], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "current")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "a_updated"
    assert payload["yoy_change"] == pytest.approx(0.1)


def test_same_timestamp_revision_after_title_is_explicit_revision():
    original = _facts(
        "2024年年度报告",
        "2025-04-21",
        "z_original",
        100.0,
        10.0,
        published="2025-04-21 10:00:00",
    )
    revision = _facts(
        "2024年年度报告（修订后）",
        "2025-04-21",
        "a_revised",
        110.0,
        11.0,
        published="2025-04-21 10:00:00",
    )
    current = _facts(
        "2025年年度报告",
        "2026-04-20",
        "current",
        121.0,
        12.1,
        published="2026-04-20 10:00:00",
    )
    original["filing_title"] = "2024年年度报告"
    revision["filing_title"] = "2024年年度报告（修订后）"
    current["filing_title"] = "2025年年度报告"

    evidence = derive_fundamental_trend_evidence(
        pd.concat([original, revision, current], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "current")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "a_revised"
    assert payload["yoy_change"] == pytest.approx(0.1)


def test_same_timestamp_semantically_equivalent_facts_use_stable_provenance_only():
    first = _facts(
        "2024年年度报告",
        "2025-04-21",
        "z_equivalent",
        100.0,
        10.0,
        published="2025-04-21 10:00:00",
    )
    second = _facts(
        "2024年年度报告",
        "2025-04-21",
        "a_equivalent",
        100.0,
        10.0,
        published="2025-04-21 10:00:00",
    )
    current = _facts(
        "2025年年度报告",
        "2026-04-20",
        "current",
        120.0,
        12.0,
        published="2026-04-20 10:00:00",
    )
    for frame, title in (
        (first, "2024年年度报告"),
        (second, "2024年年度报告"),
        (current, "2025年年度报告"),
    ):
        frame["filing_title"] = title

    evidence = derive_fundamental_trend_evidence(
        pd.concat([first, second, current], ignore_index=True)
    )
    revenue = evidence[
        (evidence["evidence_type"] == "REVENUE_TREND")
        & (evidence["document_id"] == "current")
    ].iloc[0]
    payload = json.loads(revenue["evidence_payload"])
    assert payload["prior_document_id"] == "a_equivalent"
    assert payload["yoy_change"] == pytest.approx(0.2)


def test_same_timestamp_conflicting_unmarked_facts_remain_fail_closed():
    first = _facts(
        "2024年年度报告",
        "2025-04-21",
        "a_conflict",
        100.0,
        10.0,
        published="2025-04-21 10:00:00",
    )
    second = _facts(
        "2024年年度报告",
        "2025-04-21",
        "b_conflict",
        110.0,
        11.0,
        published="2025-04-21 10:00:00",
    )
    current = _facts(
        "2025年年度报告",
        "2026-04-20",
        "current",
        121.0,
        12.1,
        published="2026-04-20 10:00:00",
    )
    for frame, title in (
        (first, "2024年年度报告"),
        (second, "2024年年度报告"),
        (current, "2025年年度报告"),
    ):
        frame["filing_title"] = title

    with pytest.raises(ValueError, match="conflicting facts"):
        derive_fundamental_trend_evidence(
            pd.concat([first, second, current], ignore_index=True)
        )


def test_official_pdf_title_classifies_summary_body_and_full_carriers():
    assert (
        classify_official_filing_presentation(
            "公司代码：688002 烟台睿创微纳技术股份有限公司 2020 年年度报告摘要"
        )
        == FILING_PRESENTATION_SUMMARY
    )
    assert (
        classify_official_filing_presentation(
            "中微半导体设备（上海）股份有限公司 2020 年第一季度报告正文"
        )
        == FILING_PRESENTATION_BODY
    )
    assert (
        classify_official_filing_presentation(
            "中微半导体设备（上海）股份有限公司 2020 年第一季度报告"
        )
        == FILING_PRESENTATION_FULL
    )


def test_same_timestamp_full_carrier_beats_summary_when_facts_conflict():
    summary = _facts(
        "2020年年度报告",
        "2021-04-28",
        "summary_doc",
        100.0,
        10.0,
        published="2021-04-28 00:00:00",
    )
    full = _facts(
        "2020年年度报告",
        "2021-04-28",
        "full_doc",
        120.0,
        12.0,
        published="2021-04-28 00:00:00",
    )
    summary["filing_title"] = "烟台睿创微纳技术股份有限公司2020年年度报告"
    full["filing_title"] = "2020年年度报告"
    summary["document_presentation_variant"] = FILING_PRESENTATION_SUMMARY
    full["document_presentation_variant"] = FILING_PRESENTATION_FULL

    selected = latest_filing_fact_as_of(
        pd.concat([summary, full], ignore_index=True),
        entity_id="600000.SH",
        fact_type="OPERATING_REVENUE",
        period_end=pd.Timestamp("2020-12-31"),
        as_of=pd.Timestamp("2021-04-28"),
    )
    assert selected is not None
    assert selected["document_id"] == "full_doc"
    assert selected["value"] == pytest.approx(120.0)


class _FakeResponse:
    def __init__(self, content: bytes):
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._content


def test_cninfo_static_403_uses_only_official_https_download_fallback():
    calls: list[str] = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(
                request.full_url,
                403,
                "Forbidden",
                hdrs=None,
                fp=None,
            )
        return _FakeResponse(b"%PDF-1.7 exact bulletin bytes")

    original = "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"
    downloaded = download_official_document(original, opener=opener)

    assert calls[0] == original
    assert calls[1] == (
        "https://www.cninfo.com.cn/new/announcement/download"
        "?bulletinId=1225568832&announceTime=2026-09-16"
    )
    assert downloaded.url == original
    assert downloaded.retrieval_url == calls[1]
    assert downloaded.content.startswith(b"%PDF-")
    assert len(downloaded.sha256) == 64


def test_non_cninfo_or_non_403_download_failure_does_not_substitute_source():
    calls: list[str] = []

    def opener(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)

    with pytest.raises(HTTPError):
        download_official_document(
            "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF",
            opener=opener,
        )
    assert calls == [
        "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"
    ]


def test_cninfo_fallback_retries_transient_transport_failure_only():
    calls: list[str] = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)
        if len(calls) == 2:
            raise ConnectionResetError(104, "reset")
        return _FakeResponse(b"%PDF-1.7 retry success")

    original = "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"
    downloaded = download_official_document(original, opener=opener)

    assert downloaded.url == original
    assert downloaded.retrieval_url == calls[-1]
    assert len(calls) == 3
    assert calls[1] == calls[2]

def test_cninfo_double_403_uses_cookie_session_https_only(monkeypatch):
    opener_calls: list[str] = []

    def opener(request, timeout):
        opener_calls.append(request.full_url)
        raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)

    class FakeResponse:
        def __init__(self, url: str, status_code: int, content: bytes):
            self.url = url
            self.status_code = status_code
            self.content = content

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(f"unexpected status {self.status_code}")

    class FakeSession:
        def __init__(self):
            self.calls: list[str] = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == "https://www.cninfo.com.cn/":
                return FakeResponse(url, 200, b"<html></html>")
            if url.startswith("https://static.cninfo.com.cn/"):
                return FakeResponse(url, 403, b"")
            return FakeResponse(url, 200, b"%PDF-1.7 session transport")

        def close(self):
            pass

    session = FakeSession()
    monkeypatch.setattr(filing_module.requests, "Session", lambda: session)

    original = "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"
    downloaded = download_official_document(original, opener=opener)

    assert opener_calls == [
        original,
        (
            "https://www.cninfo.com.cn/new/announcement/download"
            "?bulletinId=1225568832&announceTime=2026-09-16"
        ),
    ]
    assert session.calls[0] == "https://www.cninfo.com.cn/"
    assert session.calls[1] == original
    assert session.calls[2].startswith("https://www.cninfo.com.cn/new/announcement/download?")
    assert all(url.startswith("https://") for url in session.calls)
    assert downloaded.url == original
    assert downloaded.retrieval_url == session.calls[2]
    assert downloaded.content.startswith(b"%PDF-")

def test_cninfo_triple_403_uses_browser_fingerprint_https_fallback(monkeypatch):
    opener_calls: list[str] = []
    browser_calls: list[tuple[str, str]] = []

    def opener(request, timeout):
        opener_calls.append(request.full_url)
        raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)

    def session_fallback(canonical_url, fallback_url, *, timeout):
        raise HTTPError(
            fallback_url,
            403,
            "Forbidden after CNINFO HTTPS session transport",
            hdrs=None,
            fp=None,
        )

    def browser_fallback(canonical_url, fallback_url, *, timeout):
        browser_calls.append((canonical_url, fallback_url))
        assert timeout == 30.0
        return (
            b"%PDF-1.7 browser transport",
            fallback_url,
        )

    monkeypatch.setattr(
        filing_module,
        "_download_cninfo_with_https_session",
        session_fallback,
    )
    monkeypatch.setattr(
        filing_module,
        "_download_cninfo_with_browser_transport",
        browser_fallback,
    )

    original = "https://static.cninfo.com.cn/finalpage/2026-09-16/1225568832.PDF"
    downloaded = download_official_document(original, opener=opener)

    expected_fallback = (
        "https://www.cninfo.com.cn/new/announcement/download"
        "?bulletinId=1225568832&announceTime=2026-09-16"
    )
    assert opener_calls == [original, expected_fallback]
    assert browser_calls == [(original, expected_fallback)]
    assert downloaded.url == original
    assert downloaded.retrieval_url == expected_fallback
    assert downloaded.content.startswith(b"%PDF-")
    assert len(downloaded.sha256) == 64

