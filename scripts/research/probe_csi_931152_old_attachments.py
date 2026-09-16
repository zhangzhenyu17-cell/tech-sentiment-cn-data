from __future__ import annotations

import argparse
from dataclasses import asdict
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit

from tech_sentiment.csi_adjustment_evidence import (
    AttachmentRef,
    CSI_931152,
    CSINDEX_SITE,
    attachment_refs_from_detail,
    build_adjustment_rows,
    download_attachment,
    extract_effective_date,
    fetch_announcement_detail,
)


# Located by the paginated outcome-free search probe:
# 2019-06, 2019-12, 2020-06 CSI consolidated rebalance announcements.
NOTICE_IDS = (11379, 11529, 11429)


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _fallback_refs(detail: dict[str, object]) -> list[AttachmentRef]:
    """Recover legacy Excel links embedded in announcement HTML.

    Older CSI notices often expose no ``enclosureList`` but keep the rebalance
    workbook as an ``href`` inside ``content``. This mirrors the fallback used
    by Microsoft's Qlib CSI collector and remains restricted to the official
    CSI announcement body.
    """

    refs = attachment_refs_from_detail(detail)
    if refs:
        return refs
    notice_id = int(str(detail.get("id", "0")))
    title = _clean(detail.get("title"))
    publish_date = _clean(detail.get("publishDate"))
    effective_date = extract_effective_date(detail.get("content"))
    content = unescape(str(detail.get("content") or ""))
    links = re.findall(r"href\s*=\s*[\"']([^\"']+?\.(?:xlsx?|XLSX?)(?:\?[^\"']*)?)[\"']", content, flags=re.I)
    out: list[AttachmentRef] = []
    seen: set[str] = set()
    for raw_href in links:
        href = raw_href.strip()
        if not href:
            continue
        url = urljoin(CSINDEX_SITE, href)
        if url.startswith("http://www.csindex.com.cn/"):
            url = "https://www.csindex.com.cn/" + url.split("http://www.csindex.com.cn/", 1)[1]
        if url in seen:
            continue
        seen.add(url)
        path_name = Path(unquote(urlsplit(url).path)).name
        out.append(
            AttachmentRef(
                notice_id=notice_id,
                title=title,
                publish_date=publish_date,
                effective_date=effective_date,
                file_name=path_name,
                file_url=url,
                detail_url=f"{CSINDEX_SITE}/zh-CN/about/newsDetail?id={notice_id}",
            )
        )
    return out


def _dump(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Direct legacy CSI 931152 attachment fallback probe")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    out = args.output_dir
    files = out / "attachments"
    out.mkdir(parents=True, exist_ok=True)
    files.mkdir(parents=True, exist_ok=True)

    notice_rows: list[dict[str, object]] = []
    attachment_rows: list[dict[str, object]] = []
    changes: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    for notice_id in NOTICE_IDS:
        try:
            detail = fetch_announcement_detail(notice_id, timeout=args.timeout)
        except Exception as exc:
            failures.append({"notice_id": notice_id, "stage": "detail", "error": f"{type(exc).__name__}: {exc}"})
            continue
        refs = _fallback_refs(detail)
        notice_rows.append(
            {
                "notice_id": notice_id,
                "title": _clean(detail.get("title")),
                "publish_date": _clean(detail.get("publishDate")),
                "embedded_excel_links": len(refs),
            }
        )
        for ref in refs:
            row = asdict(ref)
            try:
                raw, digest = download_attachment(ref.file_url, timeout=args.timeout)
                suffix = Path(urlsplit(ref.file_url).path).suffix.lower()
                local = files / f"{notice_id}_{digest[:12]}{suffix}"
                local.write_bytes(raw)
                parsed = build_adjustment_rows(ref, workbook_bytes=raw, index_code=CSI_931152)
                changes.extend(asdict(item) for item in parsed)
                row.update(
                    {
                        "sha256": digest,
                        "local_path": str(local),
                        "status": "contains_931152_changes" if parsed else "parsed_no_931152_rows",
                        "change_rows": len(parsed),
                    }
                )
            except Exception as exc:
                row["status"] = "failed_closed"
                row["error"] = f"{type(exc).__name__}: {exc}"
                failures.append({"notice_id": notice_id, "stage": "attachment", "url": ref.file_url, "error": row["error"]})
            attachment_rows.append(row)

    _dump(out / "notices.jsonl", notice_rows)
    _dump(out / "attachments.jsonl", attachment_rows)
    _dump(out / "changes.jsonl", changes)
    _dump(out / "failures.jsonl", failures)
    report = {
        "status": "LEGACY_CSI_ATTACHMENT_CANDIDATE_ONLY",
        "notice_ids": list(NOTICE_IDS),
        "notices_fetched": len(notice_rows),
        "attachments": len(attachment_rows),
        "change_rows": len(changes),
        "change_publish_dates": sorted({str(row.get("publish_date", "")) for row in changes}),
        "failures": len(failures),
        "model_results_read": False,
        "holdout_opened": False,
        "manifest_updated": False,
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
