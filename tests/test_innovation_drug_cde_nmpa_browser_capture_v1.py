from __future__ import annotations

from tech_sentiment.innovation_drug_cde_nmpa_browser_capture_v1 import (
    RAW_COLUMNS,
    SOURCE_SPECS,
    normalize_cde_browser_capture_rows,
)


def test_browser_capture_normalizer_preserves_official_row_codes_and_dates() -> None:
    raw = {
        "priority": [
            {
                "pridCODE": "priority-1",
                "acceptid": "CXHS1",
                "drgnamecn": "药A",
                "company": "江苏恒瑞医药股份有限公司",
                "noticeDate": "2026-09-20",
            }
        ],
        "breakthrough": [
            {
                "bcnidCODE": "break-1",
                "acceptid": "CXHL1",
                "drgnamecn": "药B",
                "company": "江苏恒瑞医药股份有限公司",
                "noticeDate": "2026-09-21",
            }
        ],
        "clinical": [
            {
                "nidCODE": "clinical-1",
                "acceptid": "CXHL2",
                "drgnamecn": "药C",
                "companys": "江苏恒瑞医药股份有限公司",
                "lcmsxkIndication": "适应症C",
                "lcmsxkRegisterkind": "1",
            }
        ],
        "conditional": [
            {
                "ypmc": "药D",
                "ssxkcyr": "江苏恒瑞医药股份有限公司",
                "list": [
                    {
                        "instruction": "适应症D",
                        "bcftjpzDate": "2026-09-22",
                        "state": "所附条件研究进行中",
                    }
                ],
            }
        ],
    }
    details = {
        "priority": {"priority-1": {"instruction": "适应症A"}},
        "breakthrough": {
            "break-1": {"instruction": "适应症B", "registerkind": "2.2"}
        },
    }
    frame = normalize_cde_browser_capture_rows(raw, details)
    assert list(frame.columns) == RAW_COLUMNS
    assert len(frame) == 4
    assert set(frame["source_record_id"]) == {
        "priority-1",
        "break-1",
        "clinical-1",
        "",
    }
    priority = frame.loc[frame["category"].eq("纳入优先审评品种名单")].iloc[0]
    assert priority["publication_date"] == "2026-09-20"
    assert priority["indication"] == "适应症A"
    clinical = frame.loc[frame["category"].eq("临床试验默示许可")].iloc[0]
    assert clinical["publication_date"] == ""
    assert clinical["approval_date"] == ""
    conditional = frame.loc[frame["category"].eq("附条件批准品种")].iloc[0]
    assert conditional["approval_date"] == "2026-09-22"


def test_browser_capture_specs_bind_official_frontend_row_codes() -> None:
    assert SOURCE_SPECS["priority"]["record_code_field"] == "pridCODE"
    assert SOURCE_SPECS["breakthrough"]["record_code_field"] == "bcnidCODE"
    assert SOURCE_SPECS["clinical"]["record_code_field"] == "nidCODE"
    assert SOURCE_SPECS["conditional"]["record_code_field"] is None
