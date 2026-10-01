"""Capture only the missing public disclosure window; no state qualification."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
import time

import pandas as pd

from .cninfo_direct import fetch_cninfo_announcements_direct
from .filing_materialization import (
    _document_identity, _entity_id, _filing_error_severity,
    _select_primary_numeric_filing_candidates, _symbol_query_identity,
)
from .immutable_checkpoint import ImmutableCheckpointStore
from .index_history import read_anchor_csv, read_adjustments_csv, reconstruct_index_history
from .official_filing_facts import (
    FILING_FACT_COLUMNS, FILING_PARSER_VERSION, build_filing_fact_rows,
    classify_official_filing_presentation, download_official_document, extract_pdf_text,
)
from .pit_public_materialization import (
    CNINFO_SOURCE_ID, _market_available_date, _parse_document_identity,
    _publication_has_precise_clock, _real_trading_calendar,
)

CONTRACT_ID = "CROSS_SECTOR_FUNDAMENTAL_DATE_INCREMENT_V1"
VERSION = "cross-sector-fundamental-date-increment-v1"
GROUPS = ("STAR50", "ChiNext50", "INNOVATION_DRUG", "DEFENSE", "CORE_BETA")
FILES = ("versioned_filing_facts.csv", "entity_window_receipts.csv", "document_receipts.csv", "errors.csv")
ENTITY_COLUMNS = ("entity_id", "symbol", "query_status", "numeric_documents", "parsed_documents", "soft_parse_gaps", "hard_failures")
DOCUMENT_COLUMNS = ("entity_id", "document_id", "publication_timestamp", "evidence_available_date", "state", "document_url", "document_sha256")
ERROR_COLUMNS = ("entity_id", "document_id", "phase", "severity", "error")
FALSE_FLAGS = ("private_qualification_granted", "fundamental_state_qualification_claimed", "outcome_read", "pairwise_builder_run", "evidence_qualification_changed", "production_changed", "trading_authority_changed")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def load_contract(root: Path, path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    _require(contract.get("contract_id") == CONTRACT_ID, "increment contract drift")
    _require(contract.get("baseline_cutoff_date") == "2026-09-29" and contract.get("target_date") == "2026-09-30", "increment date drift")
    _require(contract.get("publication_window") == {"start_date": "2026-09-29", "end_date": "2026-09-30"}, "publication window drift")
    _require(set(contract.get("memberships", {})) == set(GROUPS), "frozen membership groups drift")
    _require(contract.get("scope_unique_entities") == 572, "frozen union drift")
    _require(contract.get("work_unit_count") == 16 and contract.get("provider_max_parallel") == 4, "bounded unit plan drift")
    _require(contract.get("hard_failure_breaker") == 3, "hard failure breaker drift")
    _require(all(contract.get("authority", {}).get(flag) is False for flag in FALSE_FLAGS), "increment authority drift")
    for relative, digest in contract["inherited_source_sha256"].items():
        _require(_digest(root / relative) == digest, f"frozen source digest drift: {relative}")
    return contract


def build_scope(root: Path, contract: dict) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    memberships = {}
    target = pd.Timestamp(contract["target_date"])
    for group, spec in contract["memberships"].items():
        expected = 500 if group == "CORE_BETA" else 50
        for key in ("path", "anchor_path", "adjustments_path"):
            if key in spec:
                path = (root / spec[key]).resolve()
                _require(path.is_relative_to(root.resolve()), "membership outside source root")
                _require(_digest(path) == spec[key.replace("_path", "_sha256") if key != "path" else "sha256"], "membership digest drift")
        if "anchor_path" in spec:
            history, _, _ = reconstruct_index_history(
                read_anchor_csv(root / spec["anchor_path"]), read_adjustments_csv(root / spec["adjustments_path"]),
                history_start="2021-06-15", history_end=contract["target_date"],
                anchor_effective_date=spec["anchor_effective_date"], expected_constituents=expected,
                index_code=spec["index_code"],
            )
            active = history[(history["effective_start"] <= target) & (history["effective_end"] >= target)]
            symbols = sorted(active["symbol"].astype(str))
        else:
            frame = pd.read_csv(root / spec["path"], dtype={"symbol": str, "index_code": str})
            _require(set(frame["snapshot_date"]) == {contract["target_date"]}, "membership date drift")
            _require(set(frame["domain_id"]) == {group} and set(frame["index_code"]) == {spec["index_code"]}, "membership domain/index drift")
            _require(frame["point_in_time"].astype(str).str.lower().eq("true").all(), "non-PIT membership")
            symbols = sorted(frame["symbol"].astype(str))
            _require(frame["entity_id"].tolist() == frame["symbol"].map(_entity_id).tolist(), "membership entity identity drift")
        _require(len(symbols) == expected and len(set(symbols)) == expected, "membership count/duplicates drift")
        _require(all(re.fullmatch(r"[0-9]{6}", symbol) for symbol in symbols), "symbol identity drift")
        memberships[group] = pd.DataFrame({"symbol": symbols, "entity_id": [_entity_id(symbol) for symbol in symbols], "group": group, "membership_effective_date": contract["target_date"]})
    symbols = sorted(set().union(*(set(frame["symbol"]) for frame in memberships.values())))
    _require(len(symbols) == contract["scope_unique_entities"], "exact membership union drift")
    return pd.DataFrame({"symbol": symbols, "entity_id": [_entity_id(symbol) for symbol in symbols]}), memberships


def unit_symbols(scope: pd.DataFrame, index: int, count: int = 16) -> list[str]:
    _require(count == 16 and 0 <= index < count, "invalid increment work unit")
    _require(not scope["symbol"].duplicated().any(), "duplicate scope symbols")
    return sorted(scope["symbol"].astype(str))[index::count]


def probe_document(spec: dict) -> dict:
    downloaded = download_official_document(spec["document_url"])
    _require(downloaded.sha256 == spec["document_sha256"], "pilot document SHA changed")
    text = extract_pdf_text(downloaded.content)
    facts = build_filing_fact_rows(
        entity_id=spec["entity_id"], evidence_available_date=spec["evidence_available_date"],
        publication_timestamp=spec["publication_timestamp"], source_identity=CNINFO_SOURCE_ID,
        provider="CNINFO", document_id=spec["document_id"],
        revision_id=f"DOCUMENT:{spec['document_id']}:SHA256:{downloaded.sha256}",
        document_url=downloaded.url, document_sha256=downloaded.sha256, text=text,
    )
    _require({"OPERATING_REVENUE", "NET_PROFIT_PARENT", "OPERATING_CASH_FLOW_NET", "NET_PROFIT_MARGIN"}.issubset(facts["fact_type"]), "pilot core facts missing")
    return {"document_id": spec["document_id"], "document_sha256": downloaded.sha256,
            "retrieval_url": downloaded.retrieval_url, "fact_rows": len(facts), "parser_version": FILING_PARSER_VERSION}


def capture_increment(*, symbols: list[str], contract: dict, trading_dates, source_commit: str,
                      checkpoint_dir: Path, pilot_checkpoint_dir: Path | None = None) -> dict:
    _require(re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None, "source commit malformed")
    _require(len(symbols) == len(set(symbols)) and bool(symbols), "capture scope empty or duplicate")
    calendar = _real_trading_calendar(trading_dates)
    baseline, target = pd.Timestamp(contract["baseline_cutoff_date"]), pd.Timestamp(contract["target_date"])
    _require(baseline in calendar and target in calendar, "exact target sessions missing")
    _require(len(calendar[(calendar > baseline) & (calendar <= target)]) == 1, "baseline must be immediate preceding witnessed session")
    store = ImmutableCheckpointStore(checkpoint_dir)
    pilot = ImmutableCheckpointStore(pilot_checkpoint_dir) if pilot_checkpoint_dir is not None else None
    parts, entities, documents, errors = [], [], [], []
    counters = dict(executed_queries=0, resumed_queries=0, executed_documents=0, resumed_documents=0)
    failures = Counter()
    started = time.monotonic()
    breaker = False

    def load(identity):
        loaded = store.load(identity)
        return loaded if loaded is not None or pilot is None else pilot.load(identity)

    def error(entity, document, phase, exc, severity="HARD_FAILURE"):
        nonlocal breaker
        errors.append(dict(entity_id=entity, document_id=document, phase=phase, severity=severity, error=f"{type(exc).__name__}: {exc}"))
        if severity == "HARD_FAILURE":
            signature = (phase, type(exc).__name__, re.sub(r"\d+", "#", str(exc))[:160])
            failures[signature] += 1
            breaker = breaker or failures[signature] >= contract["hard_failure_breaker"]

    for symbol in sorted(symbols):
        if breaker:
            break
        entity = _entity_id(symbol)
        row = dict(entity_id=entity, symbol=symbol, query_status="NOT_COMPLETED", numeric_documents=0, parsed_documents=0, soft_parse_gaps=0, hard_failures=0)
        entities.append(row)
        identity = _symbol_query_identity(source_commit=source_commit, symbol=symbol,
            query_start=contract["publication_window"]["start_date"], query_end=contract["publication_window"]["end_date"])
        try:
            cached = load(identity)
            if cached is None:
                raw = fetch_cninfo_announcements_direct(symbol=symbol,
                    start_date=contract["publication_window"]["start_date"], end_date=contract["publication_window"]["end_date"])
                store.save(identity, frames={"announcements": raw}, metadata={"entity_id": entity})
                counters["executed_queries"] += 1
            else:
                raw = cached.frames["announcements"]
                counters["resumed_queries"] += 1
            if len(raw):
                _require(raw["代码"].astype(str).str.zfill(6).eq(symbol).all(), "query symbol mismatch")
                dates = pd.to_datetime(raw["公告时间"], errors="raise", format="mixed").dt.normalize()
                _require(dates.between(baseline, target).all(), "query publication outside frozen increment")
            candidates = _select_primary_numeric_filing_candidates(raw)
            row["query_status"] = "EXACT_INCREMENT_QUERY_COMPLETE"
        except Exception as exc:
            row["query_status"], row["hard_failures"] = "QUERY_FAILED", 1
            error(entity, "QUERY", "QUERY", exc)
            continue
        for _, announcement in candidates.iterrows():
            if breaker:
                break
            publication = announcement["公告时间"]
            document = "UNKNOWN"
            try:
                document, _ = _parse_document_identity(announcement["公告链接"])
                published = pd.Timestamp(publication)
                # A missing future calendar session cannot promote a target-day
                # date-only/after-close disclosure into this window.
                if published.normalize() == target and (not _publication_has_precise_clock(publication) or published.time() > pd.Timestamp("15:00:00").time()):
                    documents.append(dict(entity_id=entity, document_id=document, publication_timestamp=str(publication), evidence_available_date=None,
                        state="NOT_AVAILABLE_BY_TARGET_CLOSE", document_url=announcement.get("公告附件链接"), document_sha256=None))
                    continue
                available, _ = _market_available_date(publication, trading_dates=calendar)
                if available <= baseline:
                    continue
                _require(available == target, "increment availability outside target session")
                row["numeric_documents"] += 1
                attachment = announcement.get("公告附件链接")
                _require(pd.notna(attachment) and bool(str(attachment).strip()), "FINANCIAL_FILING_MISSING_IMMUTABLE_ATTACHMENT_URL")
                doc_identity = _document_identity(source_commit=source_commit, symbol=symbol, document_id=document, attachment_url=str(attachment))
                cached = load(doc_identity)
                if cached is None:
                    downloaded = download_official_document(str(attachment))
                    text = extract_pdf_text(downloaded.content)
                    facts = build_filing_fact_rows(entity_id=entity, evidence_available_date=available,
                        publication_timestamp=publication, source_identity=CNINFO_SOURCE_ID, provider="CNINFO", document_id=document,
                        revision_id=f"DOCUMENT:{document}:SHA256:{downloaded.sha256}", document_url=downloaded.url,
                        document_sha256=downloaded.sha256, text=text)
                    _require(len(facts) > 0, "filing parser produced no standardized facts")
                    facts["document_presentation_variant"] = classify_official_filing_presentation(text)
                    facts["filing_title"] = str(announcement["公告标题"])
                    store.save(doc_identity, frames={"facts": facts}, metadata={"publication_timestamp": str(publication), "document_sha256": downloaded.sha256})
                    counters["executed_documents"] += 1
                else:
                    facts = cached.frames["facts"]
                    counters["resumed_documents"] += 1
                _require(facts["entity_id"].eq(entity).all() and facts["document_id"].astype(str).eq(str(document)).all(), "cached document identity drift")
                _require(facts["parser_version"].eq(FILING_PARSER_VERSION).all(), "cached parser drift")
                _require(pd.to_datetime(facts["evidence_available_date"]).eq(target).all(), "cached availability drift")
                _require(pd.to_datetime(facts["publication_timestamp"]).eq(pd.Timestamp(publication)).all(), "cached publication drift")
                parts.append(facts)
                row["parsed_documents"] += 1
                documents.append(dict(entity_id=entity, document_id=str(document), publication_timestamp=str(publication), evidence_available_date=str(available.date()),
                    state="PARSED_PUBLIC_DELTA_FACTS", document_url=str(attachment), document_sha256=str(facts.iloc[0]["document_sha256"])))
            except Exception as exc:
                severity = _filing_error_severity(exc)
                row["soft_parse_gaps" if severity == "SOFT_DATA_INSUFFICIENCY" else "hard_failures"] += 1
                error(entity, document, "DOCUMENT", exc, severity)
                documents.append(dict(entity_id=entity, document_id=str(document), publication_timestamp=str(publication), evidence_available_date=None,
                    state=severity, document_url=announcement.get("公告附件链接"), document_sha256=None))
        elapsed = time.monotonic() - started
        print(json.dumps({"planned": len(symbols), "completed": len(entities), "elapsed_seconds": round(elapsed, 2),
            "eta_seconds": round(elapsed / len(entities) * (len(symbols) - len(entities)), 2), "checkpoint_watermark": entity,
            "hard_failures": sum(item["hard_failures"] for item in entities), **counters}), flush=True)
    facts = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=(*FILING_FACT_COLUMNS, "document_presentation_variant", "filing_title"))
    if len(facts):
        _require(not facts.duplicated(["entity_id", "document_id", "revision_id", "fact_type", "period_end"]).any(), "duplicate increment fact identity")
    return {"facts": facts, "entities": pd.DataFrame(entities, columns=ENTITY_COLUMNS),
            "documents": pd.DataFrame(documents, columns=DOCUMENT_COLUMNS), "errors": pd.DataFrame(errors, columns=ERROR_COLUMNS),
            "counters": counters, "breaker_fired": breaker,
            "complete": len(entities) == len(symbols) and all(row["query_status"] == "EXACT_INCREMENT_QUERY_COMPLETE" and row["hard_failures"] == 0 for row in entities)}


def write_capture(result: dict, out: Path, *, source_commit: str, contract_sha256: str,
                  scope_sha256: str, symbols: list[str], unit_index: int, producer_sha256: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    for name, key in zip(FILES, ("facts", "entities", "documents", "errors")):
        result[key].to_csv(out / name, index=False)
    manifest = {"schema_version": VERSION, "source_commit": source_commit, "contract_sha256": contract_sha256,
        "scope_sha256": scope_sha256, "producer_sha256": producer_sha256, "target_date": "2026-09-30",
        "baseline_cutoff_date": "2026-09-29", "publication_window": {"start_date": "2026-09-29", "end_date": "2026-09-30"},
        "unit_index": unit_index, "unit_count": 16, "symbols": sorted(symbols), "complete": result["complete"],
        "breaker_fired": result["breaker_fired"], "counters": result["counters"], "parser_version": FILING_PARSER_VERSION,
        "soft_parse_gaps": int(result["entities"]["soft_parse_gaps"].sum()), "file_sha256": {name: _digest(out / name) for name in FILES},
        **{flag: False for flag in FALSE_FLAGS}}
    (out / "stage_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def validate_capture(path: Path, *, source_commit: str, contract_sha256: str, scope_sha256: str, producer_sha256: str,
                     symbols: list[str], unit_index: int) -> dict:
    manifest = json.loads((path / "stage_manifest.json").read_text(encoding="utf-8"))
    expected = {"schema_version": VERSION, "source_commit": source_commit, "contract_sha256": contract_sha256,
        "scope_sha256": scope_sha256, "producer_sha256": producer_sha256, "symbols": sorted(symbols),
        "unit_index": unit_index, "unit_count": 16, "complete": True, "breaker_fired": False,
        "target_date": "2026-09-30", "baseline_cutoff_date": "2026-09-29", "parser_version": FILING_PARSER_VERSION,
        "publication_window": {"start_date": "2026-09-29", "end_date": "2026-09-30"}, **{flag: False for flag in FALSE_FLAGS}}
    for key, value in expected.items():
        _require(manifest.get(key) == value and (not isinstance(value, bool) or manifest[key] is value), f"immutable increment unit drift: {key}")
    _require(set(manifest["file_sha256"]) == set(FILES), "unit file allowlist drift")
    for name in FILES:
        _require(_digest(path / name) == manifest["file_sha256"][name], "unit file digest drift")
    entities = pd.read_csv(path / "entity_window_receipts.csv", dtype={"symbol": str})
    _require(sorted(entities["symbol"]) == sorted(symbols) and not entities["symbol"].duplicated().any(), "unit entity completeness drift")
    _require(entities["entity_id"].tolist() == entities["symbol"].map(_entity_id).tolist(), "unit entity mapping drift")
    _require(entities["query_status"].eq("EXACT_INCREMENT_QUERY_COMPLETE").all() and entities["hard_failures"].eq(0).all(), "unit incomplete query/parse window")
    _require((entities["parsed_documents"] + entities["soft_parse_gaps"] == entities["numeric_documents"]).all(), "unit document accounting drift")
    facts = pd.read_csv(path / "versioned_filing_facts.csv", dtype={"entity_id": str, "document_id": str})
    _require(set(FILING_FACT_COLUMNS).issubset(facts.columns), "unit fact schema drift")
    if len(facts):
        _require(set(facts["entity_id"]).issubset({_entity_id(symbol) for symbol in symbols}), "unit fact entity outside scope")
        _require(facts["parser_version"].eq(FILING_PARSER_VERSION).all(), "unit fact parser drift")
        _require(pd.to_datetime(facts["evidence_available_date"]).eq(pd.Timestamp("2026-09-30")).all(), "unit fact availability drift")
        _require((pd.to_datetime(facts["period_end"]) <= pd.Timestamp("2026-09-30")).all(), "unit future accounting period")
        _require(facts["document_sha256"].astype(str).str.fullmatch(r"[0-9a-f]{64}").all(), "unit fact document digest malformed")
        _require(not facts.duplicated(["entity_id", "document_id", "revision_id", "fact_type", "period_end"]).any(), "unit duplicate fact identity")
    _require(manifest["soft_parse_gaps"] == int(entities["soft_parse_gaps"].sum()), "unit soft parse gap count drift")
    return manifest
