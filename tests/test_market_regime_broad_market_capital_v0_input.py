from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import pytest
from tech_sentiment.market_regime_broad_market_capital_v0_input import (
    STATUS_INSUFFICIENT, STATUS_READY, append_exact_history, build_summary,
    normalize_financing, normalize_turnover, validate_summary,
)


def _turnover(dates):
    return pd.DataFrame([{"date":d,"sse_a_share_turnover_yuan":100.0,"szse_a_share_turnover_yuan":120.0,"amount":220.0,"scope":"SSE_SZSE_A_SHARES","canonical_all_a_state":"INCOMPLETE_BSE_NOT_INCLUDED"} for d in dates])

def _fin(dates):
    return pd.DataFrame([{"date":d,"sse_financing_balance_yuan":1000.0,"szse_financing_balance_yuan":900.0,"financing_balance_yuan":1900.0,"sse_source_unit":"CNY","szse_source_unit":"CNY_100M","canonical_unit":"CNY","sse_source_identity":"SSE_MARGIN_SUMMARY","szse_source_identity":"SZSE_MARGIN_SUMMARY","sse_source_url":"sse","szse_source_url":"szse"} for d in dates])

def test_same_day_operational_ready_allows_one_trading_day_financing_publication_lag():
    cal=pd.bdate_range('2026-06-01',periods=90)
    t=_turnover(cal)
    f=_fin(cal[:-1])
    out=build_summary(t,f,trading_calendar=cal,market_date=str(cal[-1].date()),fresh_turnover_error_rows=0,fresh_financing_error_rows=1,seed_lineage={})
    assert out['status']==STATUS_READY
    assert out['financing']['publication_lag_trading_days']==1
    assert out['financing']['same_observation_date_as_market_date'] is False
    assert out['same_day_operational_readiness']['full_same_observation_date_claimed'] is False
    validate_summary(out)

def test_older_financing_gap_fails_closed():
    cal=pd.bdate_range('2026-06-01',periods=90)
    missing=set([cal[-4],cal[-1]])
    f=_fin([d for d in cal if d not in missing])
    out=build_summary(_turnover(cal),f,trading_calendar=cal,market_date=str(cal[-1].date()),fresh_turnover_error_rows=0,fresh_financing_error_rows=2,seed_lineage={})
    assert out['status']==STATUS_INSUFFICIENT
    assert out['financing']['older_missing_date_count']==1

def test_turnover_and_financing_normalizers_reject_scope_or_unit_drift():
    t=_turnover(['2026-09-30']); t.loc[0,'scope']='ALL_A'
    with pytest.raises(ValueError,match='scope drift'): normalize_turnover(t)
    f=_fin(['2026-09-29']); f.loc[0,'szse_source_unit']='CNY'
    with pytest.raises(ValueError,match='source-unit drift'): normalize_financing(f)

def test_append_history_rejects_overlap():
    with pytest.raises(ValueError,match='overlap'): append_exact_history(_turnover(['2026-09-29']),_turnover(['2026-09-29']),label='turnover')

def test_public_contract_has_no_private_model_or_authority():
    root=Path(__file__).resolve().parents[1]
    c=json.loads((root/'reference/market_regime_broad_market_capital_v0_public_contract.json').read_text())
    assert c['scope']=='SSE_SZSE_A_SHARES'
    assert c['freshness']['financing_max_publication_lag_trading_days']==1
    assert c['freshness']['full_same_observation_date_claimed'] is False
    assert c['scope_limitations']['beijing_stock_exchange_included'] is False
    assert c['authority']['contains_model_output'] is False
    assert c['authority']['contains_private_thresholds'] is False
    assert c['authority']['forward_outcomes_read'] is False
    assert c['authority']['trading_authority'] is False

def test_committed_20260930_bundle_is_ready_and_hash_bound():
    import hashlib
    root=Path(__file__).resolve().parents[1]
    base=root/'data/reference/market_regime_broad_market_capital_v0/2026-09-30'
    summary=json.loads((base/'input_summary.json').read_text())
    manifest=json.loads((base/'bundle_manifest.json').read_text())
    validate_summary(summary)
    assert summary['status']==STATUS_READY
    assert summary['market_date']=='2026-09-30'
    assert summary['trading_day_count']==1150
    assert summary['turnover']['row_count']==1150
    assert summary['turnover']['latest_observation_date']=='2026-09-30'
    assert summary['turnover']['fresh_capture_error_rows']==0
    assert summary['financing']['row_count']==1149
    assert summary['financing']['latest_available_observation_date']=='2026-09-29'
    assert summary['financing']['publication_lag_trading_days']==1
    assert summary['financing']['older_missing_date_count']==0
    assert summary['same_day_operational_readiness']['ready'] is True
    assert manifest['bundle_id']=='MARKET_REGIME_BROAD_MARKET_CAPITAL_V0_2026_09_30'
    assert manifest['contains_model_output'] is False
    assert manifest['contains_private_thresholds'] is False
    for item in manifest['files']:
        path=root/item['path']
        assert path.stat().st_size==item['size_bytes']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
