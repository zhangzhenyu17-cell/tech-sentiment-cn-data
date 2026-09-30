from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import pandas as pd

SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_TARGET_COMPANY = "江苏恒瑞医药股份有限公司"
CDE_TITLE = "国家药品监督管理局药品审评中心"

SOURCE_SPECS: dict[str, dict[str, Any]] = {
    "priority": {
        "source_url": "https://www.cde.org.cn/main/xxgk/listpage/2f78f372d351c6851af7431c7710a731",
        "endpoint": "/priority/getPriorityApprovalList",
        "category": "纳入优先审评品种名单",
        "company_field": "company",
        "record_code_field": "pridCODE",
        "detail_endpoint": "/priority/getPriorityApprovalInfo",
        "detail_param": "pridCODE",
        "base_params": {"noticeType": 2, "acceptid": "", "drugname": ""},
    },
    "breakthrough": {
        "source_url": "https://www.cde.org.cn/main/xxgk/listpage/da6efd086c099b7fc949121166f0130c",
        "endpoint": "/breakthrough/getBreakthroughCureList",
        "category": "纳入突破性治疗品种名单",
        "company_field": "company",
        "record_code_field": "bcnidCODE",
        "detail_endpoint": "/breakthrough/getBreakthroughInfo",
        "detail_param": "bcnidCODE",
        "base_params": {"noticeType": 1, "acceptid": "", "drugname": ""},
    },
    "clinical": {
        "source_url": "https://www.cde.org.cn/main/xxgk/listpage/4b5255eb0a84820cef4ca3e8b6bbe20c",
        "endpoint": "/xxgk/getCliniCalList",
        "category": "临床试验默示许可",
        "company_field": "companys",
        "record_code_field": "nidCODE",
        "base_params": {},
    },
    "conditional": {
        "source_url": "https://www.cde.org.cn/main/xxgk/listpage/c8d79e513a6df98adf05893281ace198",
        "endpoint": "/xxgk/getFtjpzqdList",
        "category": "附条件批准品种",
        "company_field": "ssxkcyr",
        "record_code_field": None,
        "base_params": {"drugnamecn": "", "instruction": ""},
    },
}

RAW_COLUMNS = [
    "category",
    "source_url",
    "source_record_id",
    "applicant",
    "drug_name",
    "acceptance_no",
    "indication",
    "registration_class",
    "publication_date",
    "approval_date",
    "status",
]


@dataclass(frozen=True)
class BrowserCaptureResult:
    raw_rows: pd.DataFrame
    raw_responses: dict[str, list[dict[str, Any]]]
    detail_responses: dict[str, dict[str, dict[str, Any]]]
    report: dict[str, Any]


class _CdpRuntime:
    def __init__(self, cdp_url: str) -> None:
        try:
            import websocket  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "browser capture requires websocket-client; install the data extra"
            ) from exc
        targets = json.load(urlopen(cdp_url.rstrip("/") + "/json/list", timeout=5))
        pages = [
            item
            for item in targets
            if item.get("type") == "page"
            and urlparse(str(item.get("url") or "")).hostname in {"cde.org.cn", "www.cde.org.cn"}
        ]
        if len(pages) != 1:
            raise ValueError(f"expected exactly one CDE browser page, found {len(pages)}")
        self._ws = websocket.create_connection(
            pages[0]["webSocketDebuggerUrl"], timeout=30, origin="http://localhost"
        )
        self._next_id = 1

    def close(self) -> None:
        self._ws.close()

    def eval(self, expression: str) -> Any:
        message_id = self._next_id
        self._next_id += 1
        self._ws.send(
            json.dumps(
                {
                    "id": message_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                },
                ensure_ascii=False,
            )
        )
        while True:
            response = json.loads(self._ws.recv())
            if response.get("id") != message_id:
                continue
            if response.get("result", {}).get("exceptionDetails"):
                raise RuntimeError("CDE browser runtime evaluation failed")
            return response["result"]["result"].get("value")

    def post(self, endpoint: str, params: Mapping[str, Any]) -> dict[str, Any]:
        endpoint_json = json.dumps(endpoint, ensure_ascii=False)
        params_json = json.dumps(dict(params), ensure_ascii=False, separators=(",", ":"))
        expression = (
            "new Promise((resolve)=>{"
            f"myAjax({endpoint_json},{params_json},'post')"
            ".done(resolve)"
            ".fail((x,s,e)=>resolve({fail:true,status:x.status,text:x.responseText,error:String(e)}))"
            "})"
        )
        value = self.eval(expression)
        if not isinstance(value, dict) or value.get("code") != 200:
            raise RuntimeError(f"CDE official endpoint failed: {endpoint}: {value}")
        data = value.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"CDE official endpoint returned invalid data: {endpoint}")
        return data


def _query_params(name: str, target_company: str) -> dict[str, Any]:
    params = dict(SOURCE_SPECS[name]["base_params"])
    if name in {"priority", "breakthrough"}:
        params["company"] = target_company
    elif name == "clinical":
        params["condition"] = target_company
    elif name == "conditional":
        params["ssxkcyr"] = target_company
    return params


def _paged_records(
    runtime: _CdpRuntime,
    *,
    name: str,
    target_company: str,
    page_size: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    spec = SOURCE_SPECS[name]
    base = _query_params(name, target_company)
    page_num = 1
    records: list[dict[str, Any]] = []
    total: int | None = None
    pages: int | None = None
    while True:
        data = runtime.post(
            spec["endpoint"],
            {**base, "pageSize": int(page_size), "pageNum": int(page_num)},
        )
        chunk = data.get("records") or []
        if not isinstance(chunk, list):
            raise ValueError(f"CDE {name} records is not a list")
        if total is None:
            total_raw = data.get("total")
            pages_raw = data.get("pages")
            total = int(total_raw) if str(total_raw or "").strip() else len(chunk)
            pages = int(pages_raw) if str(pages_raw or "").strip() else (1 if chunk else 0)
        records.extend(dict(item) for item in chunk)
        if page_num >= int(pages or 1):
            break
        page_num += 1
    if len(records) != int(total or 0):
        raise ValueError(f"CDE {name} pagination incomplete: {len(records)} != {total}")
    company_field = str(spec["company_field"])
    unrelated = [row for row in records if target_company not in str(row.get(company_field) or "")]
    if unrelated:
        raise ValueError(f"CDE {name} query returned rows outside target company search")
    return records, {
        "source_url": spec["source_url"],
        "endpoint": spec["endpoint"],
        "query_params": base,
        "total": int(total or 0),
        "pages": int(pages or 0),
        "records_captured": len(records),
        "capture_status": "COMPLETE",
        "capture_status_scope": "QUERY_SCOPED_EXHAUSTIVE_PAGINATION",
    }


def normalize_cde_browser_capture_rows(
    raw_responses: Mapping[str, list[dict[str, Any]]],
    detail_responses: Mapping[str, Mapping[str, dict[str, Any]]],
    *,
    allow_empty: bool = False,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in raw_responses.get("priority", []):
        code = str(item.get("pridCODE") or "")
        detail = dict(detail_responses.get("priority", {}).get(code, {}))
        rows.append(
            {
                "category": SOURCE_SPECS["priority"]["category"],
                "source_url": SOURCE_SPECS["priority"]["source_url"],
                "source_record_id": code,
                "applicant": item.get("company", ""),
                "drug_name": item.get("drgnamecn", ""),
                "acceptance_no": item.get("acceptid", ""),
                "indication": detail.get("instruction", ""),
                "registration_class": detail.get("registerkind", ""),
                "publication_date": item.get("noticeDate", ""),
                "approval_date": "",
                "status": "已纳入",
            }
        )
    for item in raw_responses.get("breakthrough", []):
        code = str(item.get("bcnidCODE") or "")
        detail = dict(detail_responses.get("breakthrough", {}).get(code, {}))
        rows.append(
            {
                "category": SOURCE_SPECS["breakthrough"]["category"],
                "source_url": SOURCE_SPECS["breakthrough"]["source_url"],
                "source_record_id": code,
                "applicant": item.get("company", ""),
                "drug_name": item.get("drgnamecn", ""),
                "acceptance_no": item.get("acceptid", ""),
                "indication": detail.get("instruction", ""),
                "registration_class": detail.get("registerkind", ""),
                "publication_date": item.get("noticeDate", ""),
                "approval_date": "",
                "status": "已纳入",
            }
        )
    for item in raw_responses.get("clinical", []):
        rows.append(
            {
                "category": SOURCE_SPECS["clinical"]["category"],
                "source_url": SOURCE_SPECS["clinical"]["source_url"],
                "source_record_id": item.get("nidCODE", ""),
                "applicant": item.get("companys", ""),
                "drug_name": item.get("drgnamecn", ""),
                "acceptance_no": item.get("acceptid", ""),
                "indication": item.get("lcmsxkIndication", ""),
                "registration_class": item.get("lcmsxkRegisterkind", ""),
                "publication_date": "",
                "approval_date": "",
                "status": "",
            }
        )
    for item in raw_responses.get("conditional", []):
        nested = item.get("list") or [{}]
        for detail in nested:
            rows.append(
                {
                    "category": SOURCE_SPECS["conditional"]["category"],
                    "source_url": SOURCE_SPECS["conditional"]["source_url"],
                    "source_record_id": "",
                    "applicant": item.get("ssxkcyr", ""),
                    "drug_name": item.get("ypmc", ""),
                    "acceptance_no": "",
                    "indication": detail.get("instruction", ""),
                    "registration_class": "",
                    "publication_date": "",
                    "approval_date": detail.get("bcftjpzDate", ""),
                    "status": detail.get("state", ""),
                }
            )
    frame = pd.DataFrame(rows, columns=RAW_COLUMNS)
    if frame.empty and not allow_empty:
        raise ValueError("CDE browser capture produced no raw rows")
    if frame.empty:
        return frame
    return frame.sort_values(
        ["category", "publication_date", "approval_date", "source_record_id", "acceptance_no", "drug_name"]
    ).reset_index(drop=True)


def capture_cde_nmpa_via_browser(
    *,
    cdp_url: str,
    target_company: str = DEFAULT_TARGET_COMPANY,
    page_size: int = 500,
    captured_at: datetime | None = None,
    allow_empty: bool = False,
) -> BrowserCaptureResult:
    runtime = _CdpRuntime(cdp_url)
    try:
        page = runtime.eval(
            "({title:document.title,url:location.href,readyState:document.readyState,"
            "bodyText:document.body.innerText.slice(0,500)})"
        )
        if not isinstance(page, dict):
            raise ValueError("CDE browser page metadata unavailable")
        host = urlparse(str(page.get("url") or "")).hostname
        if page.get("title") != CDE_TITLE or host not in {"cde.org.cn", "www.cde.org.cn"}:
            raise ValueError("CDP target is not the loaded official CDE page")
        if page.get("readyState") != "complete":
            raise ValueError("official CDE browser page is not fully loaded")

        raw: dict[str, list[dict[str, Any]]] = {}
        source_report: dict[str, dict[str, Any]] = {}
        for name in SOURCE_SPECS:
            raw[name], source_report[name] = _paged_records(
                runtime,
                name=name,
                target_company=target_company,
                page_size=page_size,
            )

        details: dict[str, dict[str, dict[str, Any]]] = {
            "priority": {},
            "breakthrough": {},
        }
        for name in ("priority", "breakthrough"):
            spec = SOURCE_SPECS[name]
            for item in raw[name]:
                code = str(item.get(spec["record_code_field"]) or "")
                if not code:
                    raise ValueError(f"CDE {name} row missing official record code")
                details[name][code] = runtime.post(
                    spec["detail_endpoint"], {spec["detail_param"]: code}
                )
    finally:
        runtime.close()

    when = captured_at or datetime.now(SHANGHAI)
    if when.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    when = when.astimezone(SHANGHAI)
    frame = normalize_cde_browser_capture_rows(raw, details, allow_empty=allow_empty)
    applicants = frame["applicant"].astype(str)
    if frame.empty:
        whole_exact_count = 0
        multi_applicant_exact_token_count = 0
        exact_token_count = 0
        no_exact_token_count = 0
    else:
        whole_exact = applicants.eq(target_company)
        exact_token = applicants.map(
            lambda value: target_company
            in {token.strip() for token in re.split(r"[;；\n、]+", value) if token.strip()}
        ).astype(bool)
        whole_exact_count = int(whole_exact.sum())
        multi_applicant_exact_token_count = int((exact_token & ~whole_exact).sum())
        exact_token_count = int(exact_token.sum())
        no_exact_token_count = int((~exact_token).sum())
    report = {
        "captured_at": when.isoformat(timespec="seconds"),
        "capture_method": "BROWSER_RENDERED_OFFICIAL_TABLE_CAPTURE",
        "transport": "LOCAL_REAL_BROWSER_CDP_SAME_ORIGIN_OFFICIAL_ENDPOINTS",
        "browser_page": page,
        "target_company_query": target_company,
        "sources": source_report,
        "raw_rows": int(len(frame)),
        "whole_field_exact_applicant_rows": whole_exact_count,
        "multi_applicant_exact_token_rows": multi_applicant_exact_token_count,
        "rows_with_exact_target_applicant_token": exact_token_count,
        "rows_without_exact_target_applicant_token": no_exact_token_count,
        "substring_matching_used": False,
        "affiliate_inference_used": False,
        "historical_outcomes_read": False,
        "prospective_outcomes_read": False,
        "direction_classified": False,
        "predictive_weight_assigned": False,
        "sector_score_computed": False,
        "evidence_qualification_changed": False,
    }
    return BrowserCaptureResult(frame, raw, details, report)


__all__ = [
    "BrowserCaptureResult",
    "DEFAULT_TARGET_COMPANY",
    "RAW_COLUMNS",
    "SOURCE_SPECS",
    "capture_cde_nmpa_via_browser",
    "normalize_cde_browser_capture_rows",
]
