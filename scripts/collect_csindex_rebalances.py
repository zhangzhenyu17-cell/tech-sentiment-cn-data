from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

import pandas as pd

from tech_sentiment.sector_rebalance_evidence import (
    CSINDEX_HOME,
    RebalanceNotice,
    announcement_payload,
    extract_attachments,
    extract_index_changes_from_sheets,
    has_index_rows,
    parse_notice_rows,
)


DEFAULT_TERMS = (
    "定期调整结果",
    "调整指数样本",
    "指数样本调整名单",
    "指数定期调整",
)


def _headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (compatible; tech-sentiment-cn-data/sector-research)",
        "Referer": "https://www.csindex.com.cn/zh-CN/about/newsCenter",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
    }


def _request_json(url: str, *, body: dict[str, object] | None = None) -> dict[str, object]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ascii_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/%:@")
    query = urllib.parse.quote(parts.query, safe="=&%:@/?")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def _request_bytes(url: str) -> bytes:
    req = urllib.request.Request(_ascii_url(url), headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", value).strip("._")
    return cleaned or fallback


def _read_workbook(content: bytes) -> dict[str, pd.DataFrame]:
    return pd.read_excel(BytesIO(content), sheet_name=None, dtype=str)


def _direct_notice_rows(notice_ids: tuple[int, ...]) -> dict[int, RebalanceNotice]:
    return {
        notice_id: RebalanceNotice(
            notice_id=notice_id,
            title="",
            publish_date="",
            detail_url=f"{CSINDEX_HOME}/announcement/queryAnnouncementById?id={notice_id}",
        )
        for notice_id in notice_ids
    }


def collect(
    *,
    index_code: str,
    since: str,
    until: str,
    output_dir: Path,
    search_terms: tuple[str, ...] = DEFAULT_TERMS,
    notice_ids: tuple[int, ...] = (),
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    attachment_dir = output_dir / "attachments"
    attachment_dir.mkdir(exist_ok=True)

    notices_by_id: dict[int, RebalanceNotice] = {}
    diagnostics: list[dict[str, object]] = []
    if notice_ids:
        notices_by_id.update(_direct_notice_rows(notice_ids))
        diagnostics.append(
            {
                "search_term": "DIRECT_NOTICE_IDS",
                "status": "ok",
                "notice_rows": len(notice_ids),
                "notice_ids": "|".join(str(value) for value in notice_ids),
            }
        )
    else:
        for term in search_terms:
            try:
                payload = _request_json(
                    f"{CSINDEX_HOME}/announcement/queryAnnouncementByVo",
                    body=announcement_payload(term, page=1, rows=200),
                )
                rows = parse_notice_rows(payload, since=since, until=until)
                diagnostics.append({"search_term": term, "status": "ok", "notice_rows": len(rows)})
                for row in rows:
                    notices_by_id[row.notice_id] = row
            except Exception as exc:  # network/upstream diagnostic; collection remains fail-closed
                diagnostics.append({"search_term": term, "status": "error", "reason": repr(exc)})

    evidence_rows: list[dict[str, object]] = []
    change_frames: list[pd.DataFrame] = []
    for notice in sorted(notices_by_id.values(), key=lambda row: row.notice_id):
        detail_url = f"{CSINDEX_HOME}/announcement/queryAnnouncementById?{urllib.parse.urlencode({'id': notice.notice_id})}"
        try:
            payload = _request_json(detail_url)
            detail = payload.get("data")
            if str(payload.get("code")) != "200" or not isinstance(detail, dict):
                raise ValueError(f"detail API unavailable: code={payload.get('code')!r}")
            title = str(detail.get("title") or notice.title)
            publish_date = str(detail.get("publishDate") or notice.publish_date)
            attachments = extract_attachments(detail)
        except Exception as exc:
            evidence_rows.append(
                {
                    "notice_id": notice.notice_id,
                    "publish_date": notice.publish_date,
                    "title": notice.title,
                    "detail_url": detail_url,
                    "status": "detail_error",
                    "reason": repr(exc),
                }
            )
            continue

        if not attachments:
            evidence_rows.append(
                {
                    "notice_id": notice.notice_id,
                    "publish_date": publish_date,
                    "title": title,
                    "detail_url": detail_url,
                    "status": "no_attachment",
                }
            )
            continue

        for n, attachment in enumerate(attachments, start=1):
            parsed_path = urllib.parse.urlsplit(attachment.file_url).path
            suffix = Path(parsed_path).suffix.lower() or ".bin"
            file_name = _safe_name(attachment.file_name, f"notice_{notice.notice_id}_{n}{suffix}")
            if not Path(file_name).suffix:
                file_name += suffix
            local_path = attachment_dir / f"{notice.notice_id}_{n}_{file_name}"
            row = {
                "notice_id": notice.notice_id,
                "publish_date": publish_date,
                "effective_date": attachment.effective_date,
                "effect_timing": attachment.effect_timing,
                "title": title,
                "detail_url": detail_url,
                "attachment_name": attachment.file_name,
                "attachment_url": attachment.file_url,
                "local_path": local_path.as_posix(),
                "status": "pending_parse",
                "contains_index_code": False,
                "change_rows": 0,
                "reason": "",
            }
            if suffix not in {".xls", ".xlsx", ".xlsm"}:
                row["status"] = "non_excel_attachment"
                evidence_rows.append(row)
                continue
            try:
                content = _request_bytes(attachment.file_url)
                local_path.write_bytes(content)
                sheets = _read_workbook(content)
                contains = has_index_rows(sheets, index_code=index_code)
                row["contains_index_code"] = contains
                if not contains:
                    row["status"] = "parsed_no_index_rows"
                else:
                    changes = extract_index_changes_from_sheets(
                        sheets,
                        index_code=index_code,
                        effective_date=attachment.effective_date,
                    )
                    changes["effect_timing"] = attachment.effect_timing
                    changes["notice_id"] = notice.notice_id
                    changes["publish_date"] = publish_date
                    changes["attachment_url"] = attachment.file_url
                    changes["attachment_name"] = attachment.file_name
                    change_frames.append(changes)
                    row["change_rows"] = len(changes)
                    row["status"] = "official_index_rows_found"
            except Exception as exc:
                row["status"] = "attachment_parse_error"
                row["reason"] = repr(exc)
            evidence_rows.append(row)

    pd.DataFrame(diagnostics).to_csv(output_dir / "search_diagnostics.csv", index=False)
    evidence = pd.DataFrame(evidence_rows)
    evidence.to_csv(output_dir / "attachment_audit.csv", index=False)
    if change_frames:
        changes = pd.concat(change_frames, ignore_index=True)
        changes.to_csv(output_dir / f"{index_code}_official_changes.csv", index=False)
    else:
        changes = pd.DataFrame()
        pd.DataFrame(
            columns=[
                "index_code",
                "effective_date",
                "effect_timing",
                "change_type",
                "security_code",
                "security_name",
                "evidence_status",
                "notice_id",
                "publish_date",
                "attachment_url",
                "attachment_name",
            ]
        ).to_csv(output_dir / f"{index_code}_official_changes.csv", index=False)

    summary = {
        "index_code": index_code,
        "since": since,
        "until": until,
        "notice_count": len(notices_by_id),
        "attachment_count": len(evidence_rows),
        "attachments_with_index_rows": int(evidence.get("contains_index_code", pd.Series(dtype=bool)).fillna(False).sum()) if not evidence.empty else 0,
        "official_change_rows": len(changes),
        "status": "CANDIDATE_OFFICIAL_EVIDENCE_COLLECTED" if len(changes) else "NO_OFFICIAL_INDEX_ROWS_COLLECTED",
        "qualification": "research-only; collection alone does not mark membership periods complete",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect outcome-free CSIndex rebalance evidence")
    parser.add_argument("--index-code", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--notice-id", action="append", type=int, default=[])
    args = parser.parse_args()
    collect(
        index_code=args.index_code,
        since=args.since,
        until=args.until,
        output_dir=args.output_dir,
        notice_ids=tuple(args.notice_id),
    )


if __name__ == "__main__":
    main()
