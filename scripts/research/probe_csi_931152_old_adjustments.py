from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

import pandas as pd

from tech_sentiment.csi_adjustment_evidence import (
    CSI_931152,
    CSINDEX_HOME,
    announcement_payload,
    attachment_refs_from_detail,
    build_adjustment_rows,
    download_attachment,
    fetch_announcement_detail,
    parse_announcement_search,
    _json_request,
)


SEARCH_TERMS = (
    "关于调整沪深300和中证香港100等指数样本",
    "关于调整沪深300和中证香港100等指数样本股",
    "沪深300和中证香港100等指数样本",
    "指数样本调整",
    "指数定期调整结果",
)
START = pd.Timestamp("2019-04-22")
END = pd.Timestamp("2020-06-30")


def _date_ok(value: str) -> bool:
    try:
        ts = pd.Timestamp(value).normalize()
    except (TypeError, ValueError):
        return False
    return START <= ts <= END


def _title_ok(title: str) -> bool:
    compact = "".join(str(title).split())
    return "调整" in compact and ("沪深300" in compact or "指数样本" in compact or "定期调整" in compact)


def _dump_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def query_old_notices(*, timeout: int, max_pages: int, rows: int) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    endpoint = f"{CSINDEX_HOME}/announcement/queryAnnouncementByVo"
    notices: dict[int, dict[str, object]] = {}
    audit: list[dict[str, object]] = []
    for term in SEARCH_TERMS:
        seen_page_ids: set[tuple[int, ...]] = set()
        for page in range(1, max_pages + 1):
            payload = _json_request(
                endpoint,
                payload=announcement_payload(term, page=page, rows=rows),
                timeout=timeout,
            )
            parsed = parse_announcement_search(payload, search_term=term)
            raw_data = payload.get("data")
            raw_count = len(raw_data) if isinstance(raw_data, list) else 0
            page_rows = [
                item for item in parsed
                if _date_ok(item.publish_date) and _title_ok(item.title)
            ]
            page_ids = tuple(sorted(item.notice_id for item in parsed))
            audit.append(
                {
                    "search_term": term,
                    "page": page,
                    "raw_rows": raw_count,
                    "design_rows": len(parsed),
                    "target_window_title_rows": len(page_rows),
                    "notice_ids": [item.notice_id for item in page_rows],
                }
            )
            for item in page_rows:
                notices[item.notice_id] = asdict(item)
            if raw_count == 0:
                break
            if page_ids and page_ids in seen_page_ids:
                break
            if page_ids:
                seen_page_ids.add(page_ids)
    ordered = sorted(notices.values(), key=lambda row: (str(row["publish_date"]), int(row["notice_id"])))
    return ordered, audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Targeted outcome-free 2019-2020 CSI 931152 adjustment evidence probe")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--rows", type=int, default=100)
    args = parser.parse_args()

    out = args.output_dir
    attachments_dir = out / "attachments"
    out.mkdir(parents=True, exist_ok=True)
    attachments_dir.mkdir(parents=True, exist_ok=True)

    notices, search_audit = query_old_notices(timeout=args.timeout, max_pages=args.max_pages, rows=args.rows)
    attachment_rows: list[dict[str, object]] = []
    changes: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    for notice in notices:
        notice_id = int(notice["notice_id"])
        try:
            detail = fetch_announcement_detail(notice_id, timeout=args.timeout)
        except Exception as exc:
            failures.append({"notice_id": notice_id, "stage": "detail", "error": f"{type(exc).__name__}: {exc}"})
            continue
        for attachment in attachment_refs_from_detail(detail):
            row = asdict(attachment)
            try:
                raw, digest = download_attachment(attachment.file_url, timeout=args.timeout)
                suffix = Path(attachment.file_name).suffix.lower() or Path(attachment.file_url).suffix.lower()
                local = attachments_dir / f"{notice_id}_{digest[:12]}{suffix}"
                local.write_bytes(raw)
                row.update({"sha256": digest, "local_path": str(local), "status": "downloaded"})
                if suffix in {".xls", ".xlsx"}:
                    parsed = build_adjustment_rows(attachment, workbook_bytes=raw, index_code=CSI_931152)
                    changes.extend(asdict(item) for item in parsed)
                    row["status"] = "contains_931152_changes" if parsed else "parsed_no_931152_rows"
                else:
                    row["status"] = "non_excel_unparsed"
            except Exception as exc:
                row["status"] = "failed_closed"
                row["error"] = f"{type(exc).__name__}: {exc}"
                failures.append({"notice_id": notice_id, "stage": "attachment", "url": attachment.file_url, "error": row["error"]})
            attachment_rows.append(row)

    _dump_jsonl(out / "notices.jsonl", notices)
    _dump_jsonl(out / "search_audit.jsonl", search_audit)
    _dump_jsonl(out / "attachments.jsonl", attachment_rows)
    _dump_jsonl(out / "changes.jsonl", changes)
    _dump_jsonl(out / "failures.jsonl", failures)
    report = {
        "status": "TARGETED_OLD_CSI_EVIDENCE_CANDIDATE_ONLY",
        "target_index": CSI_931152,
        "window": [START.date().isoformat(), END.date().isoformat()],
        "notices": len(notices),
        "attachments": len(attachment_rows),
        "change_rows": len(changes),
        "change_effective_dates": sorted({str(row.get("effective_date", "")) for row in changes}),
        "failures": len(failures),
        "model_results_read": False,
        "holdout_opened": False,
        "manifest_updated": False,
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
