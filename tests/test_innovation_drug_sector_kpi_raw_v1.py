import json

import pandas as pd
import pytest

from tech_sentiment.innovation_drug_sector_kpi_raw_v1 import (
    BD_LICENSING,
    CLINICAL_REGULATORY,
    build_sector_kpi_raw_result,
    classify_innovation_drug_title,
    normalize_cde_snapshot,
    normalize_cninfo_sector_events,
)


def _cninfo_row(title: str, document_id: str = "1") -> dict[str, object]:
    return {
        "entity_id": "600276.SH",
        "event_date": "2026-05-12",
        "evidence_available_date": "2026-05-12",
        "source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE",
        "provider": "CNINFO",
        "document_id": document_id,
        "revision_id": f"DOCUMENT:{document_id}",
        "provenance": json.dumps({"source_identity": "CNINFO_ANNOUNCEMENT_ARCHIVE"}),
        "ingestion_identity": f"source-{document_id}",
        "availability_state": "HISTORICAL_RECONSTRUCTABLE",
        "title": title,
        "source_url_identity": (
            "https://www.cninfo.com.cn/new/disclosure/detail?"
            f"announcementId={document_id}&orgId=test"
        ),
    }


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("恒瑞医药关于获得药物临床试验批准通知书的公告", "CLINICAL_TRIAL_AUTHORIZATION"),
        ("恒瑞医药关于药物纳入突破性治疗品种名单的公告", "BREAKTHROUGH_THERAPY_DESIGNATION"),
        ("恒瑞医药关于药品上市许可申请获受理的提示性公告", "MARKETING_APPLICATION_ACCEPTED"),
        (
            "恒瑞医药关于药品上市许可申请获受理并纳入优先审评程序的提示性公告",
            "MARKETING_APPLICATION_PRIORITY_REVIEW",
        ),
        ("恒瑞医药关于获得药品注册批准的公告", "MARKETING_APPROVAL"),
        ("恒瑞医药关于撤回药品注册申请的公告", "REGISTRATION_APPLICATION_WITHDRAWAL"),
        ("恒瑞医药关于与某公司签署战略合作及许可协议的公告", "BD_LICENSE_OR_COLLABORATION"),
        ("关于终止临床试验的公告", "CLINICAL_TRIAL_TERMINATION"),
        ("2025年度利润分配方案及提请股东会授权董事会制定中期分红方案的公告", None),
        ("四川百利天恒药业股份有限公司关于与中国银行股份有限公司四川省分行签订战略合作协议的公告", None),
        ("山东步长制药股份有限公司关于控股子公司拟签署知识产权合作协议的公告", None),
        ("关于与礼来公司签署研发合作协议的公告", "BD_COLLABORATION"),
        ("关于独家商业化合作协议的公告", "BD_COLLABORATION"),
        ("关于全球战略合作协议触发里程碑付款条件的公告", "BD_COLLABORATION"),
    ],
)
def test_title_taxonomy_is_domain_specific_and_non_directional(title: str, expected: str | None) -> None:
    assert classify_innovation_drug_title(title) == expected


def test_cninfo_normalization_uses_exact_pit_identity_without_direction() -> None:
    frame = pd.DataFrame(
        [
            _cninfo_row("恒瑞医药关于获得药物临床试验批准通知书的公告", "11"),
            _cninfo_row("恒瑞医药关于与某公司签署战略合作及许可协议的公告", "12"),
            _cninfo_row("年度股东大会决议公告", "13"),
        ]
    )
    out = normalize_cninfo_sector_events(frame)
    assert len(out) == 2
    assert set(out["event_family"]) == {CLINICAL_REGULATORY, BD_LICENSING}
    assert set(out["event_type"]) == {
        "CLINICAL_TRIAL_AUTHORIZATION",
        "BD_LICENSE_OR_COLLABORATION",
    }
    assert out["direction_classified"].tolist() == [False, False]
    assert out["predictive_weight_assigned"].tolist() == [False, False]
    assert out["outcome_read"].tolist() == [False, False]
    assert set(out["source_identity"]) == {"CNINFO_ANNOUNCEMENT_ARCHIVE"}


def test_cde_snapshot_requires_exact_mapping_and_delays_date_only_publication() -> None:
    calendar = pd.to_datetime(["2026-04-16", "2026-04-17", "2026-04-20"])
    snapshot = pd.DataFrame(
        [
            {
                "entity_id": "600276.SH",
                "entity_mapping_basis": "EXACT_ISSUER_DISCLOSURE_CROSS_REFERENCE",
                "category": "纳入突破性治疗品种名单",
                "publication_date": "2026-04-17",
                "record_id": "cde-1",
                "applicant": "江苏恒瑞医药股份有限公司",
                "drug_name": "TEST-001",
                "indication": "示例适应症",
                "source_url": (
                    "https://www.cde.org.cn/main/xxgk/listpage/"
                    "da6efd086c099b7fc949121166f0130c"
                ),
            }
        ]
    )
    out = normalize_cde_snapshot(snapshot, trading_dates=calendar)
    assert out.loc[0, "event_type"] == "CDE_BREAKTHROUGH_INCLUDED"
    assert str(out.loc[0, "event_date"].date()) == "2026-04-17"
    assert str(out.loc[0, "evidence_available_date"].date()) == "2026-04-20"
    assert out.loc[0, "direction_classified"] is False or not bool(
        out.loc[0, "direction_classified"]
    )


def test_cde_snapshot_rejects_fuzzy_or_unofficial_mapping() -> None:
    calendar = pd.to_datetime(["2026-04-17", "2026-04-20"])
    snapshot = pd.DataFrame(
        [
            {
                "entity_id": "600276.SH",
                "entity_mapping_basis": "FUZZY_NAME_MATCH",
                "category": "拟突破性治疗品种",
                "publication_date": "2026-04-17",
                "record_id": "cde-2",
                "applicant": "恒瑞",
                "drug_name": "TEST-002",
                "indication": "示例",
                "source_url": "https://example.com/not-official",
            }
        ]
    )
    with pytest.raises(ValueError, match="exact and auditable"):
        normalize_cde_snapshot(snapshot, trading_dates=calendar)


def test_raw_result_never_creates_sector_score_or_qualification() -> None:
    cninfo = normalize_cninfo_sector_events(
        pd.DataFrame([_cninfo_row("恒瑞医药关于获得药品注册批准的公告", "21")])
    )
    result = build_sector_kpi_raw_result(cninfo_events=cninfo)
    assert result.summary["state"] == "RAW_EVENT_CONTEXT_MATERIALIZED_NOT_FORMALLY_QUALIFIED"
    assert result.summary["event_rows"] == 1
    assert result.summary["sector_931152_point_in_time_aggregation_computed"] is False
    assert result.summary["sector_kpi_state_classified"] is False
    assert result.summary["predictive_score_computed"] is False
    assert result.summary["historical_outcomes_read"] is False
    assert result.summary["prospective_outcomes_read"] is False
    assert result.summary["evidence_qualification_changed"] is False
    assert result.summary["production_changed"] is False
    assert result.summary["trading_authority_changed"] is False
