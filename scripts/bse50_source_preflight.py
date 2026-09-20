from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import time

from bs4 import BeautifulSoup
import requests

from tech_sentiment.bse50_official import (
    discover_attachment_links,
    discover_notice_links,
    extract_effective_date,
)


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _html_text(response: requests.Response) -> str:
    # BSE pages are served without a reliable charset header on some hosted
    # runners. requests then defaults to ISO-8859-1 and corrupts Chinese text.
    # Decode from raw bytes using the response's apparent encoding, while
    # preserving the original bytes for provenance hashing.
    encoding = response.apparent_encoding or response.encoding or "utf-8"
    decoded = response.content.decode(encoding, errors="replace")
    return BeautifulSoup(decoded, "html.parser").get_text(" ", strip=True)


def _get(session: requests.Session, url: str, *, timeout: float = 20.0) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.get(
                url,
                timeout=timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; public BSE50 qualification preflight)",
                    "Accept": "text/html,application/xhtml+xml,*/*",
                },
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.75 * (attempt + 1))
    raise RuntimeError(f"BSE fetch failed for {url}: {last_error}") from last_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    if contract.get("contract_id") != "bse50_universe_qualification_public_v1":
        raise ValueError("unexpected BSE50 public contract")
    if contract.get("status") != "FROZEN_PUBLIC_DATA_SCOPE":
        raise ValueError("BSE50 public contract is not frozen")
    boundary = contract["public_boundary"]
    for key in (
        "private_model_semantics_allowed",
        "portfolio_or_holdings_data_allowed",
        "forward_result_computation_allowed",
        "private_evidence_allowed",
        "automatic_trigger_allowed",
        "production_or_trading_authority_changed",
    ):
        if boundary.get(key) is not False:
            raise ValueError(f"public boundary drift: {key}")

    sources = contract["official_sources"]
    seed_urls = [
        sources["notice_index_url"],
        sources["initial_sample_notice_url"],
        sources["historical_index_notice_url"],
        sources["current_index_page_url"],
        contract["code_mapping"]["official_mapping_url"],
    ]
    session = requests.Session()
    seed_records: list[dict[str, object]] = []
    notice_urls: set[str] = {
        sources["initial_sample_notice_url"],
        "https://www.bse.cn/bse_indices_news/200013968.html",
    }

    for url in seed_urls:
        response = _get(session, url)
        body = response.content
        html = response.content.decode(response.apparent_encoding or response.encoding or "utf-8", errors="replace")
        text = _html_text(response)
        links = discover_notice_links(html, base_url=url)
        attachments = discover_attachment_links(html, base_url=url)
        notice_urls.update(links)
        seed_records.append(
            {
                "url": url,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "bytes": len(body),
                "sha256": _digest(body),
                "notice_link_count": len(links),
                "attachment_link_count": len(attachments),
            }
        )

    qualified: list[dict[str, object]] = []
    visited: set[str] = set()
    queue = list(sorted(notice_urls))
    max_chain_pages = 128
    history_end = contract["history_end"]
    while queue and len(visited) < max_chain_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        response = _get(session, url)
        html = response.content.decode(response.apparent_encoding or response.encoding or "utf-8", errors="replace")
        text = _html_text(response)

        # Only expand the chain through pages that are themselves about BSE50.
        # Otherwise a generic neighbouring index notice can fan out into the
        # entire BSE indices-news archive and exhaust the traversal budget.
        is_bse50_page = "北证50" in text or "899050" in text
        linked_notices = discover_notice_links(html, base_url=url)
        if is_bse50_page:
            for linked in linked_notices:
                if linked not in visited and linked not in queue:
                    queue.append(linked)

        if not is_bse50_page:
            continue
        try:
            effective_date = extract_effective_date(text).date().isoformat()
        except ValueError:
            if url == sources["initial_sample_notice_url"]:
                effective_date = "2022-11-21"
            else:
                continue

        if effective_date > history_end:
            continue

        attachments = discover_attachment_links(html, base_url=url)
        if url != sources["initial_sample_notice_url"] and not attachments:
            continue
        qualified.append(
            {
                "url": url,
                "effective_date": effective_date,
                "attachment_count": len(attachments),
                "attachments": attachments,
            }
        )

    dates = sorted({row["effective_date"] for row in qualified})
    required_snapshots = int(contract["discovery"]["minimum_distinct_effective_snapshots"])
    status = "PASS" if len(dates) >= required_snapshots else "FAIL_CLOSED"
    result = {
        "schema_version": "bse50-source-preflight-v1",
        "status": status,
        "seed_records": seed_records,
        "qualified_notices": qualified,
        "distinct_effective_dates": dates,
        "distinct_effective_date_count": len(dates),
        "visited_notice_pages": len(visited),
        "minimum_final_snapshot_count": required_snapshots,
        "qualification_granted": False,
        "forward_outcomes_read": False,
        "note": "Transport/source-discovery preflight only. Full PIT qualification remains fail closed.",
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if status != "PASS":
        raise SystemExit("BSE50 official-source preflight failed closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
