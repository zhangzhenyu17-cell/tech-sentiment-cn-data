from __future__ import annotations

from hashlib import sha256
import re
from urllib.error import HTTPError
from urllib.parse import urlparse

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
_RANGE_STREAM_CHUNK_SIZE = 64 * 1024
_RANGE_SLICE_SIZE = 512 * 1024
_MAX_DOCUMENT_BYTES = 128 * 1024 * 1024
_MAX_RANGE_REQUESTS = (_MAX_DOCUMENT_BYTES // _RANGE_SLICE_SIZE) + 16
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


def _parse_content_range(value: object) -> tuple[int, int, int]:
    text = str(value or "").strip()
    match = _CONTENT_RANGE_RE.fullmatch(text)
    if match is None:
        raise ValueError("CNINFO range response lacks a valid Content-Range")
    return (
        int(match.group("start")),
        int(match.group("end")),
        int(match.group("total")),
    )


def _stable_header_value(headers: object, name: str) -> str | None:
    if not hasattr(headers, "get"):
        return None
    value = str(headers.get(name) or "").strip()
    return value or None


def _download_cninfo_candidate_with_range_resume(
    canonical_url: str,
    candidate_url: str,
    *,
    timeout: float,
) -> tuple[bytes, str]:
    """Reconstruct one exact CNINFO entity using bounded closed byte ranges.

    Every request starts at the exact number of bytes already retained and asks
    for a closed, bounded interval. This deliberately avoids repeating the
    multi-megabyte open-ended entity streams that were truncated on hosted
    runners. Recovery never mixes candidates. Every 206 response must be
    contiguous, stay inside the requested interval, report a stable total
    entity length, and remain on the same CNINFO provider/retrieval target.
    ETag and Last-Modified are checked for stability whenever the server emits
    them. No alternate document/source is searched or substituted.
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
    stable_etag: str | None = None
    stable_last_modified: str | None = None
    last_transient: BaseException | None = None
    session = requests.Session()
    try:
        for _attempt in range(_MAX_RANGE_REQUESTS):
            offset = len(assembled)
            if expected_total is not None and offset == expected_total:
                assert stable_retrieval_url is not None
                return bytes(assembled), stable_retrieval_url
            if expected_total is not None and offset > expected_total:
                raise ValueError(
                    "CNINFO range recovery assembled bytes beyond declared total"
                )

            requested_end = offset + _RANGE_SLICE_SIZE - 1
            if expected_total is not None:
                requested_end = min(requested_end, expected_total - 1)

            headers = dict(base_headers)
            headers["Range"] = f"bytes={offset}-{requested_end}"

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
                        "CNINFO range recovery retrieval URL changed between slices"
                    )

                response_headers = getattr(response, "headers", {}) or {}
                response_etag = _stable_header_value(response_headers, "ETag")
                response_last_modified = _stable_header_value(
                    response_headers, "Last-Modified"
                )
                if stable_etag and response_etag and response_etag != stable_etag:
                    raise ValueError(
                        "CNINFO range recovery ETag changed between slices"
                    )
                if (
                    stable_last_modified
                    and response_last_modified
                    and response_last_modified != stable_last_modified
                ):
                    raise ValueError(
                        "CNINFO range recovery Last-Modified changed between slices"
                    )
                if stable_etag is None and response_etag:
                    stable_etag = response_etag
                if stable_last_modified is None and response_last_modified:
                    stable_last_modified = response_last_modified

                declared_slice_length: int | None = None
                if status == 206:
                    start, end, total = _parse_content_range(
                        response_headers.get("Content-Range")
                    )
                    if start != offset or end < start:
                        raise ValueError(
                            "CNINFO range recovery Content-Range does not match requested offset"
                        )
                    if end > requested_end:
                        raise ValueError(
                            "CNINFO range recovery response exceeded requested closed slice"
                        )
                    if total <= end:
                        raise ValueError(
                            "CNINFO range recovery Content-Range has invalid total"
                        )
                    if expected_total is None:
                        expected_total = total
                    elif total != expected_total:
                        raise ValueError(
                            "CNINFO range recovery total length changed between slices"
                        )
                    if expected_total > _MAX_DOCUMENT_BYTES:
                        raise ValueError(
                            "CNINFO document exceeds bounded range-recovery size"
                        )
                    declared_slice_length = end - start + 1
                    content_length = _parse_positive_content_length(
                        response_headers.get("Content-Length")
                    )
                    if (
                        content_length is not None
                        and content_length != declared_slice_length
                    ):
                        raise ValueError(
                            "CNINFO range recovery Content-Length disagrees with Content-Range"
                        )
                elif status == 200:
                    if offset:
                        raise ValueError(
                            "CNINFO range recovery server ignored Range after partial transfer"
                        )
                    expected_total = _parse_positive_content_length(
                        response_headers.get("Content-Length")
                    )
                    if (
                        expected_total is not None
                        and expected_total > _MAX_DOCUMENT_BYTES
                    ):
                        raise ValueError(
                            "CNINFO document exceeds bounded range-recovery size"
                        )
                else:
                    raise ValueError(
                        f"CNINFO range recovery received unexpected HTTP status {status}"
                    )

                bytes_before = len(assembled)
                stream_failed = False
                try:
                    for chunk in response.iter_content(
                        chunk_size=_RANGE_STREAM_CHUNK_SIZE
                    ):
                        if not chunk:
                            continue
                        assembled.extend(chunk)
                        if len(assembled) > _MAX_DOCUMENT_BYTES:
                            raise ValueError(
                                "CNINFO document exceeds bounded range-recovery size"
                            )
                        if expected_total is not None and len(assembled) > expected_total:
                            raise ValueError(
                                "CNINFO range recovery received bytes beyond declared total"
                            )
                except Exception as stream_exc:
                    if not (
                        isinstance(stream_exc, ChunkedEncodingError)
                        or is_transient_network_error(stream_exc)
                    ):
                        raise
                    last_transient = stream_exc
                    stream_failed = True

                received = len(assembled) - bytes_before
                if declared_slice_length is not None:
                    if received > declared_slice_length:
                        raise ValueError(
                            "CNINFO range recovery received bytes beyond declared slice"
                        )
                    if received < declared_slice_length:
                        last_transient = last_transient or ChunkedEncodingError(
                            "CNINFO closed byte-range slice ended early"
                        )
                        continue

                if stream_failed:
                    continue
                if not assembled:
                    raise ValueError("official filing attachment is empty")
                if expected_total is None:
                    assert stable_retrieval_url is not None
                    return bytes(assembled), stable_retrieval_url
                if len(assembled) == expected_total:
                    assert stable_retrieval_url is not None
                    return bytes(assembled), stable_retrieval_url
                if len(assembled) < expected_total:
                    continue
                raise ValueError(
                    "CNINFO range recovery assembled length disagrees with declared total"
                )
            finally:
                if response is not None:
                    response.close()
    finally:
        session.close()

    raise RuntimeError(
        "CNINFO exact closed byte-range recovery exhausted"
    ) from last_transient


def _candidate_error_summary(candidate_url: str, exc: BaseException) -> str:
    host = urlparse(candidate_url).hostname or "unknown-host"
    detail = " ".join(str(exc).split())
    if len(detail) > 240:
        detail = detail[:237] + "..."
    return f"{host}: {type(exc).__name__}: {detail}"


def _download_cninfo_with_range_resume(
    canonical_url: str,
    fallback_url: str,
    *,
    timeout: float,
) -> tuple[bytes, str]:
    """Try strict closed-range reconstruction independently on CNINFO URLs."""

    last_error: BaseException | None = None
    errors: list[str] = []
    for candidate_url in (canonical_url, fallback_url):
        try:
            return _download_cninfo_candidate_with_range_resume(
                canonical_url,
                candidate_url,
                timeout=timeout,
            )
        except Exception as exc:
            last_error = exc
            errors.append(_candidate_error_summary(candidate_url, exc))
    diagnostic = " | ".join(errors)
    raise RuntimeError(
        "CNINFO same-provider closed byte-range recovery exhausted without "
        f"official PDF bytes; {diagnostic}"
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
    same-provider HTTPS session/browser paths and, if those exhaust, strict
    closed byte ranges against those exact same URLs. No source search,
    document substitution, HTTP downgrade, or evidence-rule change is allowed.
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
            transport_method = "cninfo_same_provider_closed_range"

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
