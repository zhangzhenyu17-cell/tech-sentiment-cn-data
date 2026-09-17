from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
import time

from tech_sentiment import sina_index_membership_evidence as sina


LEGACY_SCRIPT = Path(__file__).with_name("build_931152_oos_inputs.py")
CHECKPOINT_SCHEMA = 1
NETWORK_SPACING_SECONDS = 2.0
RATE_LIMIT_COOLDOWN_SECONDS = 20.0
RATE_LIMIT_OUTER_RETRIES = 2


def _load_legacy_module():
    spec = importlib.util.spec_from_file_location("build_931152_oos_inputs", LEGACY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen 931152 OOS input builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_output_dir(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output-dir", type=Path, required=True)
    args, _ = parser.parse_known_args(argv)
    return args.output_dir


def _checkpoint_paths(root: Path, symbol: str) -> tuple[Path, Path]:
    return root / f"sina_{symbol}.html", root / f"sina_{symbol}.meta.json"


def _text_sha256(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _validate_retained_page(html: str, *, symbol: str) -> None:
    compact = " ".join(html.replace("<", " <").replace(">", "> ").split())
    if symbol not in compact or "所属指数" not in compact or "指数代码" not in compact:
        raise ValueError(f"retained Sina page failed semantic validation for {symbol}")


def _read_checkpoint(root: Path, symbol: str, *, index_code: str):
    html_path, meta_path = _checkpoint_paths(root, symbol)
    if not html_path.is_file() or not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("schema_version") != CHECKPOINT_SCHEMA:
        return None
    if meta.get("symbol") != symbol or meta.get("index_code") != index_code:
        return None
    source_url = sina.related_url(symbol)
    if meta.get("source_url") != source_url:
        return None
    html = html_path.read_text(encoding="utf-8")
    if meta.get("checkpoint_text_sha256") != _text_sha256(html):
        return None
    response_sha256 = str(meta.get("response_sha256") or "")
    if len(response_sha256) != 64:
        return None
    _validate_retained_page(html, symbol=symbol)
    intervals = sina.parse_membership_intervals(
        html,
        symbol=symbol,
        source_url=source_url,
        response_sha256=response_sha256,
        index_code=index_code,
    )
    return intervals, html


def _write_checkpoint(
    root: Path,
    symbol: str,
    *,
    index_code: str,
    html: str,
    source_url: str,
    response_sha256: str,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    html_path, meta_path = _checkpoint_paths(root, symbol)
    html_path.write_text(html, encoding="utf-8")
    meta = {
        "schema_version": CHECKPOINT_SCHEMA,
        "symbol": symbol,
        "index_code": index_code,
        "source_url": source_url,
        "response_sha256": response_sha256,
        "checkpoint_text_sha256": _text_sha256(html),
        "evidence_role": "sina_related_index_page_transport_checkpoint_only",
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _checkpointed_fetcher(root: Path):
    def fetch(symbol: str, *, timeout: int = 20, index_code: str = sina.CSI_931152):
        retained = _read_checkpoint(root, symbol, index_code=index_code)
        if retained is not None:
            return retained

        # Hosted runners are currently receiving Sina HTTP 456 after bursts.
        # Serialize the network path and deliberately pace new requests. Retained
        # pages are returned immediately and therefore do not consume rate budget.
        time.sleep(NETWORK_SPACING_SECONDS)
        last_error: Exception | None = None
        for outer_attempt in range(RATE_LIMIT_OUTER_RETRIES + 1):
            try:
                html, source_url, response_sha256 = sina.fetch_related_page(
                    symbol,
                    timeout=timeout,
                    retries=2,
                    retry_backoff_seconds=2.0,
                )
                intervals = sina.parse_membership_intervals(
                    html,
                    symbol=symbol,
                    source_url=source_url,
                    response_sha256=response_sha256,
                    index_code=index_code,
                )
                _write_checkpoint(
                    root,
                    symbol,
                    index_code=index_code,
                    html=html,
                    source_url=source_url,
                    response_sha256=response_sha256,
                )
                return intervals, html
            except Exception as exc:
                last_error = exc
                if "HTTP Error 456" not in str(exc) or outer_attempt >= RATE_LIMIT_OUTER_RETRIES:
                    raise
                time.sleep(RATE_LIMIT_COOLDOWN_SECONDS * (outer_attempt + 1))
        assert last_error is not None
        raise last_error

    return fetch


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    output_dir = _parse_output_dir(effective_argv)
    output_dir.mkdir(parents=True, exist_ok=True)

    legacy = _load_legacy_module()
    legacy.fetch_membership_intervals = _checkpointed_fetcher(output_dir)
    # The legacy builder keeps its own second pass. Give provider-side rate limits
    # a real cooldown instead of immediately repeating an HTTP 456 burst.
    legacy.SINA_SECOND_PASS_SLEEP_SECONDS = RATE_LIMIT_COOLDOWN_SECONDS

    original_argv = sys.argv
    try:
        sys.argv = [str(LEGACY_SCRIPT), *effective_argv]
        return int(legacy.main())
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
