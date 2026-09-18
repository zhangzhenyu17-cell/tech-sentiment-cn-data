from __future__ import annotations

import json

from tech_sentiment.official_policy_archive import (
    CSRC_CHANNELS,
    _channel_metadata_url,
    _fetch_json,
    _fetch_text,
    _search_list_url,
    parse_csrc_search_page,
    resolve_csrc_channel,
)


def main() -> None:
    channels: list[dict[str, object]] = []
    article_probe: dict[str, object] | None = None

    for segment, (channel_code, default_type) in CSRC_CHANNELS.items():
        metadata = _fetch_json(_channel_metadata_url(channel_code))
        channel = resolve_csrc_channel(
            segment=segment,
            channel_code=channel_code,
            default_evidence_type=default_type,
            payload=metadata,
        )
        page_url = _search_list_url(channel.channel_id, page=1, page_size=2)
        payload = _fetch_json(page_url)
        entries, meta = parse_csrc_search_page(
            payload,
            channel=channel,
            requested_page=1,
        )
        if not entries:
            raise SystemExit(
                f"CSRC protocol probe failed: {channel_code} first page is empty"
            )
        channels.append(
            {
                "segment": segment,
                "channel_code": channel.channel_code,
                "channel_id": channel.channel_id,
                "channel_name": channel.channel_name,
                "page": meta["page"],
                "rows": meta["rows"],
                "total": meta["total"],
                "protocol_ok": True,
            }
        )
        if article_probe is None:
            entry = entries[0]
            body = _fetch_text(entry.url)
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
