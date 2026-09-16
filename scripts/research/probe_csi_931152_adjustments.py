from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from tech_sentiment.csi_adjustment_evidence import (
    CSI_931152,
    DEFAULT_SEARCH_TERMS,
    attachment_refs_from_detail,
    build_adjustment_rows,
    download_attachment,
    fetch_announcement_detail,
    query_design_announcements,
)


STATUS = "CANDIDATE_MEMBERSHIP_EVIDENCE_ONLY"


def _safe_name(value: str) -> str:
    name = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return name or "attachment"


def _attachment_filename(notice_id: int, file_name: str, file_url: str) -> tuple[str, str]:
    suffix = Path(file_name).suffix.lower()
    if not suffix:
        suffix = Path(urlparse(file_url).path).suffix.lower()
    stem = Path(file_name).stem if file_name else "attachment"
    safe_stem = _safe_name(stem)
    return f"{notice_id}_{safe_stem}{suffix}", suffix


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Outcome-free one-shot probe for official CSI 931152 rebalance evidence. "
            "It never edits the membership evidence manifest."
        )
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--search-term",
        action="append",
        dest="search_terms",
        help=(
            "Repeatable CSI announcement search term. If omitted, the checked-in "
            "default terms are used."
        ),
    )
    args = parser.parse_args()

    out_dir = args.output_dir
    attachment_dir = out_dir / "attachments"
    detail_dir = out_dir / "details"
    out_dir.mkdir(parents=True, exist_ok=True)
    attachment_dir.mkdir(parents=True, exist_ok=True)
    detail_dir.mkdir(parents=True, exist_ok=True)

    search_terms = tuple(args.search_terms) if args.search_terms else DEFAULT_SEARCH_TERMS
    notices = query_design_announcements(
        search_terms=search_terms,
        timeout=args.timeout,
    )
    notice_rows: list[dict[str, object]] = []
    attachment_rows: list[dict[str, object]] = []
    change_rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []

    for notice in notices:
        try:
            detail = fetch_announcement_detail(notice.notice_id, timeout=args.timeout)
            (detail_dir / f"{notice.notice_id}.json").write_text(
                json.dumps(detail, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:  # network evidence probe: retain failure explicitly
            failures.append(
                {
                    "notice_id": notice.notice_id,
                    "stage": "detail",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        refs = attachment_refs_from_detail(detail)
        notice_rows.append(
            {
                **asdict(notice),
                "detail_title": str(detail.get("title", "")),
                "detail_publish_date": str(detail.get("publishDate", "")),
                "has_enclosure_list": isinstance(detail.get("enclosureList"), list),
                "attachment_refs": len(refs),
            }
        )

        for attachment in refs:
            row = asdict(attachment)
            row["status"] = "located_not_yet_qualified"
            try:
                raw, digest = download_attachment(attachment.file_url, timeout=args.timeout)
                filename, suffix = _attachment_filename(
                    attachment.notice_id,
                    attachment.file_name,
                    attachment.file_url,
                )
                local_path = attachment_dir / filename
                local_path.write_bytes(raw)
                row["sha256"] = digest
                row["local_path"] = str(local_path)

                if suffix in {".xls", ".xlsx"}:
                    parsed = build_adjustment_rows(
                        attachment,
                        workbook_bytes=raw,
                        index_code=CSI_931152,
                    )
                    if parsed:
                        row["status"] = "contains_931152_changes_candidate"
                        change_rows.extend(asdict(item) for item in parsed)
                    else:
                        row["status"] = "parsed_no_931152_change_rows"
                else:
                    row["status"] = "downloaded_non_excel_unparsed"
            except Exception as exc:  # preserve provenance; never silently skip
                row["status"] = "failed_closed"
                row["error"] = f"{type(exc).__name__}: {exc}"
                failures.append(
                    {
                        "notice_id": attachment.notice_id,
                        "stage": "attachment",
                        "url": attachment.file_url,
                        "error": row["error"],
                    }
                )
            attachment_rows.append(row)

    def dump_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    dump_jsonl(out_dir / "notices.jsonl", notice_rows)
    dump_jsonl(out_dir / "attachments.jsonl", attachment_rows)
    dump_jsonl(out_dir / "failures.jsonl", failures)

    change_path = out_dir / "931152_changes.csv"
    fields = [
        "notice_id",
        "publish_date",
        "effective_date",
        "action",
        "symbol",
        "attachment_url",
        "attachment_sha256",
    ]
    with change_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(change_rows)

    report = {
        "status": STATUS,
        "index_code": CSI_931152,
        "design_window": ["2019-04-22", "2023-12-31"],
        "search_terms": list(search_terms),
        "notices_located": len(notice_rows),
        "details_saved": len(list(detail_dir.glob("*.json"))),
        "attachments_located": len(attachment_rows),
        "change_rows_extracted": len(change_rows),
        "failures": len(failures),
        "manifest_updated": False,
        "qualification_note": (
            "Output is candidate source material only. An attachment containing 931152 rows "
            "does not by itself qualify a rebalance period; effective-date confirmation, "
            "anchor/reconstruction consistency, and manifest review remain required."
        ),
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
