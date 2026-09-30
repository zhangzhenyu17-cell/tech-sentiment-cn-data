from __future__ import annotations

import argparse
import json
from pathlib import Path

from tech_sentiment.innovation_drug_cde_nmpa_browser_capture_v1 import (
    DEFAULT_TARGET_COMPANY,
    capture_cde_nmpa_via_browser,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture CDE/NMPA official rows through an already-loaded local real Chrome CDP session."
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9223")
    parser.add_argument("--target-company", default=DEFAULT_TARGET_COMPANY)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    result = capture_cde_nmpa_via_browser(
        cdp_url=args.cdp_url,
        target_company=args.target_company,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result.raw_rows.to_csv(
        args.output_dir / "cde_nmpa_official_raw_snapshot.csv",
        index=False,
        encoding="utf-8-sig",
    )
    (args.output_dir / "cde_nmpa_official_raw_responses.json").write_text(
        json.dumps(result.raw_responses, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "cde_nmpa_official_detail_responses.json").write_text(
        json.dumps(result.detail_responses, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "capture_report.json").write_text(
        json.dumps(result.report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result.report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
