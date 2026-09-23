from __future__ import annotations

from hashlib import sha256
import re
from urllib.error import HTTPError

import requests
from requests.exceptions import ChunkedEncodingError

from .bounded_retry import is_transient_network_error
from .official_filing_facts import (
    DownloadedOfficialDocument,
    _cninfo_https_download_fallback,
    _decode_official_transport_body,
    _download_cninfo_with_browser_transport,
    _download_cninfo_with_https_session,
    _looks_like_html,
    _looks_like_pdf,
    _validate_same_provider_retrieval,
    download_official_document,
)


_CONTENT_RANGE_RE = re.compile(
    r"^bytes (?P<start>\d+)-(?P<end>\d+)/(?P<total>\d+)$",
    flags=re.IGNORECASE,
)
_RANGE_CHUNK_SIZE = 256 * 1024
_MAX_RANGE_REQUESTS = 8
_MAX_DOCUMENT_BYTES = 128 * 1024 * 1024
_BROWSER_EXHAUSTED_MESSAGE = (
    "CNINFO browser HTTPS transport exhausted without official PDF bytes"
)


def _session_failure_allows_browser_fallback(exc: BaseException) -> bool:
    """Return True only for safe CNINFO session-transport recovery cases.

    requests wraps an incomplete HTTP entity body as ChunkedEncodingError even
    when the underlying cause is http.client.IncompleteRead. That is the same
    transport-truncation class that already permits recovery after urllib
    exhaustion, so the existing same-provider browser transport may be tried.
    Semantic/content-validation errors remain fail-closed.
    """

    return (
        isinstance(exc, ChunkedEncodingError)
        or (isinstance(exc, HTTPError) and exc.code == 403)
        or is_transient_network_error(exc)
    )


def _browser_failure_allows_range_resume(exc: BaseException) -> bool:
    """Allow byte-range recovery only after the known browser transport exhausts."""

    return isinstance(exc, RuntimeError) and str(exc) == _BROWSER_EXHAUSTED_MESSAGE


def _parse_positive_content_length(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError as exc:
        raise ValueError("CNINFO response has invalid Content-Length") from exc
    if parsed < 0:
        raise ValueError("CNINFO response has negative Content-Length")
    return parsed


def _download_cninfo_candidate_with_range_resume(
    canonical_url: str,
    candidate_url: str,
    *,
    timeout: float,
) -> tuple[bytes, str]:
    """Reconstruct one exact CNINFO entity with strict HTTP byte ranges.

    Recovery never mixes candidates. A candidate starts from byte zero and, if
    the HTTP entity stream is truncated, subsequent requests must return 206
    with a Content-Range starting at the exact number of bytes already kept.
    Same-provider retrieval, stable validators/redirect target, bounded size,
    and a stable total length are enforced before assembled bytes are accepted.
    """

    base_headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.cninfo.com.cn/",
    }
    assembled = bytearray()
    expected_total: int | None = None
    stable_retrieval_url: str | None = None
    validator: str | None = None
    last_transient: BaseException | None = None
    session = requests.Session()
    try:
        for _attempt in range(_MAX_RANGE_REQUESTS):
            offset = len(assembled)
            headers = dict(base_headers)
            if offset:
                headers["Range"] = f"bytes={offset}-"
                if validator:
                    headers["If-Range"] = validator

            response = None
            try:
                response = session.get(
                    candidate_url,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=True,
                    stream=True,
                )
                status = int(getattr(response, "status_code", 0) or 0)
                if status == 403:
                    raise HTTPError(candidate_url, 403, "Forbidden", hdrs=None, fp=None)
                response.raise_for_status()

                retrieval_url = str(
                    getattr(response, "url", candidate_url) or candidate_url
                )
                _validate_same_provider_retrieval(canonical_url, retrieval_url)
                if stable_retrieval_url is None:
                    stable_retrieval_url = retrieval_url
                elif retrieval_url != stable_retrieval_url:
                    raise ValueError(
                        "CNINFO range resume retrieval URL changed between byte ranges"
                    )

                response_headers = getattr(response, "headers", {}) or {}
                response_validator = str(
                    response_headers.get("ETag")
                    or response_headers.get("Last-Modified")
                    or ""
                ).strip()
                if validator and response_validator and response_validator != validator:
                    raise ValueError(
                        "CNINFO range resume entity validator changed between byte ranges"
                    )
                if validator is None and response_validator:
                    validator = response_validator

                if offset:
                    if status != 206:
                        raise ValueError(
                            "CNINFO range resume server ignored Range after partial transfer"
                        )
                    content_range = str(
                        response_headers.get("Content-Range") or ""
                    ).strip()
                    match = _CONTENT_RANGE_RE.fullmatch(content_range)
                    if match is None:
                        raise ValueError(
                            "CNINFO range resume response lacks a valid Content-Range"
                        )
                    start = int(match.group("start"))
                    end = int(match.group("end"))
                    total = int(match.group("total"))
                    if start != offset or end < start or total <= end:
                        raise ValueError(
                            "CNINFO range resume Content-Range does not match requested offset"
                        )
                    if expected_total is None:
                        expected_total = total
                    elif total != expected_total:
                        raise ValueError(
                            "CNINFO range resume total length changed between requests"
                        )
                    remaining_length = _parse_positive_content_length(
                        response_headers.get("Content-Length")
                    )
                    if (
                        remaining_length is not None
                        and remaining_length != end - start + 1
                    ):
                        raise ValueError(
                            "CNINFO range resume Content-Length disagrees with Content-Range"
                        )
                else:
                    if status == 206:
                        content_range = str(
                            response_headers.get("Content-Range") or ""
                        ).strip()
                        match = _CONTENT_RANGE_RE.fullmatch(content_range)
                        if match is None or int(match.group("start")) != 0:
                            raise ValueError(
                                "CNINFO initial partial response has invalid Content-Range"
                            )
                        expected_total = int(match.group("total"))
                    elif status == 200:
                        expected_total = _parse_positive_content_length(
                            response_headers.get("Content-Length")
                        )
                    else:  # raise_for_status should have rejected ordinary errors.
                        raise ValueError(
                            f"CNINFO range resume received unexpected HTTP status {status}"
                        )

                if expected_total is not None and expected_total > _MAX_DOCUMENT_BYTES:
                    raise ValueError("CNINFO document exceeds bounded range-recovery size")

                bytes_before = len(assembled)
                try:
                    for chunk in response.iter_content(chunk_size=_RANGE_CHUNK_SIZE):
                        if not chunk:
                            continue
                        assembled.extend(chunk)
                        if len(assembled) > _MAX_DOCUMENT_BYTES:
                            raise ValueError(
                                "CNINFO document exceeds bounded range-recovery size"
                            )
                        if expected_total is not None and len(assembled) > expected_total:
                            raise ValueError(
                                "CNINFO range resume received bytes beyond declared total"
                            )
                except Exception as stream_exc:
                    if not (
                        isinstance(stream_exc, ChunkedEncodingError)
                        or is_transient_network_error(stream_exc)
                    ):
                        raise
                    last_transient = stream_exc
                    # A zero-progress retry is allowed, but all retries remain bounded.
                    continue

                if not assembled:
                    raise ValueError("official filing attachment is empty")
                if expected_total is not None and len(assembled) < expected_total:
                    last_transient = ChunkedEncodingError(
                        "CNINFO stream ended before the declared entity length"
                    )
                    if len(assembled) == bytes_before and offset:
                        continue
                    continue
                if expected_total is not None and len(assembled) != expected_total:
                    raise ValueError(
                        "CNINFO range resume assembled length disagrees with declared total"
                    )
                assert stable_retrieval_url is not None
                return bytes(assembled), stable_retrieval_url
            finally:
                if response is not None:
                    response.close()
    finally:
        session.close()

    raise RuntimeError("CNINFO exact byte-range recovery exhausted") from last_transient


def _download_cninfo_with_range_resume(
    canonical_url: str,
    fallback_url: str,
    *,
    timeout: float,
) -> tuple[bytes, str]:
    """Try strict range reconstruction independently on existing CNINFO URLs."""

    last_error: BaseException | None = None
    for candidate_url in (canonical_url, fallback_url):
        try:
            return _download_cninfo_candidate_with_range_resume(
                canonical_url,
                candidate_url,
                timeout=timeout,
            )
        except Exception as exc:
            last_error = exc
    raise RuntimeError(
        "CNINFO same-provider byte-range recovery exhausted without official PDF bytes"
    ) from last_error


def download_cninfo_document_resilient(
    url: str,
    *,
    timeout: float = 30.0,
) -> DownloadedOfficialDocument:
    """Recover one exact CNINFO PDF after bounded transport exhaustion.

    The canonical immutable attachment remains authoritative. This adapter is
    entered only when the existing official downloader exhausts a transport
    failure already classified as transient. Recovery then uses only existing
    same-provider HTTPS session/browser paths and, if those exhaust, strict byte
    ranges against those exact same URLs. No source search, document
    substitution, HTTP downgrade, or evidence-rule change is allowed.
    """

    try:
        return download_official_document(url, timeout=timeout)
    except Exception as exc:
        fallback_url = _cninfo_https_download_fallback(url)
        if fallback_url is None or not is_transient_network_error(exc):
            raise

    transport_method = "cninfo_same_provider_session"
    try:
        content, retrieval_url = _download_cninfo_with_https_session(
            url,
            fallback_url,
            timeout=timeout,
        )
    except Exception as session_exc:
        if not _session_failure_allows_browser_fallback(session_exc):
            raise
        try:
            content, retrieval_url = _download_cninfo_with_browser_transport(
                url,
                fallback_url,
                timeout=timeout,
            )
            transport_method = "cninfo_same_provider_browser"
        except Exception as browser_exc:
            if not _browser_failure_allows_range_resume(browser_exc):
                raise
            content, retrieval_url = _download_cninfo_with_range_resume(
                url,
                fallback_url,
                timeout=timeout,
            )
            transport_method = "cninfo_same_provider_range_resume"

    _validate_same_provider_retrieval(url, retrieval_url)
    if not content:
        raise ValueError("official filing attachment is empty")

    transport_sha256 = sha256(content).hexdigest()
    document_content, transport_encoding = _decode_official_transport_body(content)
    if _looks_like_html(document_content):
        raise ValueError(
            "official filing attachment returned HTML instead of PDF after allowed transport fallbacks"
        )
    if not _looks_like_pdf(document_content):
        raise ValueError(
            "official filing attachment returned non-PDF content after allowed transport fallbacks"
        )

    return DownloadedOfficialDocument(
        url=url,
        retrieval_url=retrieval_url,
        sha256=sha256(document_content).hexdigest(),
        content=document_content,
        transport_sha256=transport_sha256,
        transport_encoding=transport_encoding,
        transport_method=transport_method,
    )


__all__ = ["download_cninfo_document_resilient"]
