from __future__ import annotations

import json


from tech_sentiment.bounded_retry import call_with_bounded_network_retry
from tech_sentiment.official_policy_archive import (
    CSRC_CHANNELS,
    _channel_metadata_url,
    _fetch_json,
    _fetch_text,
    _search_list_url,
    parse_csrc_search_page,
    resolve_csrc_channel,
)


PROBE_TIMEOUT_SECONDS = 10.0


def _probe_fetch_json(url: str) -> dict[str, object]:
    return call_with_bounded_network_retry(
        lambda: _fetch_json(url, timeout=PROBE_TIMEOUT_SECONDS),
        attempts=3,
        backoff_seconds=0.5,
    )


def _probe_fetch_text(url: str) -> str:
    return call_with_bounded_network_retry(
        lambda: _fetch_text(url, timeout=PROBE_TIMEOUT_SECONDS),
        attempts=3,
        backoff_seconds=0.5,
    )


def main() -> None:
    channels: list[dict[str, object]] = []
    article_probe: dict[str, object] | None = None

    for segment, (channel_code, default_type) in CSRC_CHANNELS.items():
        metadata = _probe_fetch_json(_channel_metadata_url(channel_code))
        channel = resolve_csrc_channel(
            segment=segment,
            channel_code=channel_code,
            default_evidence_type=default_type,
            payload=metadata,
        )
        page_size = 2
        page_url = _search_list_url(channel.channel_id, page=1, page_size=page_size)
        payload = _probe_fetch_json(page_url)
        entries, meta = parse_csrc_search_page(
            payload,
            channel=channel,
            requested_page=1,
        )
        if not entries:
            raise SystemExit(
                f"CSRC protocol probe failed: {channel_code} first page is empty"
            )

        pagination_verified = True
        first_page_capacity = int(meta["rows"])
        first_page_actual_rows = int(meta["actual_rows"])
        second_page_capacity = 0
        second_page_actual_rows = 0
        if int(meta["total"]) > first_page_actual_rows:
            page2_url = _search_list_url(
                channel.channel_id,
                page=2,
                page_size=page_size,
            )
            page2_payload = _probe_fetch_json(page2_url)
            page2_entries, page2_meta = parse_csrc_search_page(
                page2_payload,
                channel=channel,
                requested_page=2,
            )
            second_page_capacity = int(page2_meta["rows"])
            second_page_actual_rows = int(page2_meta["actual_rows"])
            if int(page2_meta["total"]) != int(meta["total"]):
                raise SystemExit(
                    f"CSRC protocol probe failed: {channel_code} total drift across pages"
                )
            first_ids = {entry.manuscript_id for entry in entries}
            second_ids = {entry.manuscript_id for entry in page2_entries}
            if first_ids.intersection(second_ids):
                raise SystemExit(
                    f"CSRC protocol probe failed: {channel_code} duplicate manuscript across pages"
                )
        channels.append(
            {
                "segment": segment,
                "channel_code": channel.channel_code,
                "channel_id": channel.channel_id,
                "channel_name": channel.channel_name,
                "page": meta["page"],
                "page_capacity": first_page_capacity,
                "actual_rows": first_page_actual_rows,
                "total": meta["total"],
                "second_page_capacity": second_page_capacity,
                "second_page_actual_rows": second_page_actual_rows,
                "pagination_verified": pagination_verified,
                "protocol_ok": True,
            }
        )
        if article_probe is None:
            entry = entries[0]
            body = _probe_fetch_text(entry.url)
            if len(body.strip()) < 100:
                raise SystemExit(
                    "CSRC protocol probe failed: canonical article body is empty/too short"
                )
            article_probe = {
                "segment": segment,
                "manuscript_id": entry.manuscript_id,
                "canonical_url": entry.url,
                "publication_timestamp": entry.publication_timestamp,
                "body_verified": True,
            }

    print(
        json.dumps(
            {
                "status": "CSRC_POLICY_PROTOCOL_CONNECTIVITY_OK",
                "diagnostic_only": True,
                "canonical_evidence_output": False,
                "forward_outcome_read": False,
                "parameter_search": False,
                "channels": channels,
                "article_probe": article_probe,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
