from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from tech_sentiment.capital_input_data import fetch_sse_szse_a_share_turnover_history
from tech_sentiment.financing_materialization import materialize_financing_history
from tech_sentiment.market_regime_broad_market_capital_v0_input import (
    append_exact_history, build_summary, normalize_financing, normalize_turnover,
    sha256_path, validate_summary, write_bundle_manifest,
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    ap=argparse.ArgumentParser(description="Materialize outcome-blind broad-market capital raw input for Market Regime V0.1.")
    ap.add_argument("--seed-capital-root",type=Path,required=True)
    ap.add_argument("--seed-financing-root",type=Path,required=True)
    ap.add_argument("--seed-capital-manifest",type=Path,required=True)
    ap.add_argument("--seed-financing-manifest",type=Path,required=True)
    ap.add_argument("--calendar-csv",type=Path,required=True)
    ap.add_argument("--market-date",required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    args=ap.parse_args()

    cap_manifest=_json(args.seed_capital_manifest)
    fin_manifest=_json(args.seed_financing_manifest)
    seed_turnover_path=args.seed_capital_root/'capital_input_qualification/sse_szse_a_share_turnover.csv'
    seed_financing_path=args.seed_financing_root/'financing_materialization/financing_canonical_cny.csv'
    seed_calendar_path=args.seed_capital_root/'capital_input_qualification/trading_calendar.csv'
    seed_turnover=normalize_turnover(pd.read_csv(seed_turnover_path))
    seed_financing=normalize_financing(pd.read_csv(seed_financing_path))
    seed_calendar=pd.read_csv(seed_calendar_path,parse_dates=['date'])
    recent_calendar=pd.read_csv(args.calendar_csv,parse_dates=['date'])
    market_date=pd.Timestamp(args.market_date).normalize()
    seed_end=pd.Timestamp(seed_turnover['date'].max()).normalize()
    recent_dates=sorted(
        pd.to_datetime(recent_calendar.loc[(recent_calendar['date']>seed_end)&(recent_calendar['date']<=market_date),'date']).dt.normalize().unique()
    )
    if not recent_dates:
        raise ValueError("no post-seed trading dates found")

    turnover_fresh=fetch_sse_szse_a_share_turnover_history(trading_dates=recent_dates,sleep_seconds=0.02,retry_attempts=3,retry_backoff_seconds=0.2)
    financing_fresh=materialize_financing_history(recent_dates,retry_attempts=3,retry_backoff_seconds=0.2)
    turnover=append_exact_history(seed_turnover,turnover_fresh.combined,label='turnover')
    financing=append_exact_history(seed_financing,financing_fresh.canonical,label='financing')
    trading_calendar=pd.DatetimeIndex(
        pd.concat([seed_calendar[['date']],recent_calendar[['date']]],ignore_index=True)['date']
    ).normalize().sort_values().unique()
    trading_calendar=trading_calendar[trading_calendar<=market_date]

    seed_lineage={
        "release_tag":"v4a-stage-bundles-v1",
        "capital":{
            "asset_base":"v4a-capital-20220104-20260917-6f8ed7d3811fa72ae9bd",
            "bundle_identity":cap_manifest.get('bundle_identity'),
            "archive_sha256":cap_manifest.get('archive_sha256'),
            "stage_receipt_sha256":cap_manifest.get('stage_receipt_sha256'),
            "seed_csv_sha256":sha256_path(seed_turnover_path),
            "end_date":cap_manifest.get('end_date'),
        },
        "financing":{
            "asset_base":"v4a-financing-20220104-20260917-50c5cefbed3574a68989",
            "bundle_identity":fin_manifest.get('bundle_identity'),
            "archive_sha256":fin_manifest.get('archive_sha256'),
            "stage_receipt_sha256":fin_manifest.get('stage_receipt_sha256'),
            "seed_csv_sha256":sha256_path(seed_financing_path),
            "end_date":fin_manifest.get('end_date'),
        },
    }
    summary=build_summary(
        turnover,financing,trading_calendar=trading_calendar,market_date=args.market_date,
        fresh_turnover_error_rows=len(turnover_fresh.errors),fresh_financing_error_rows=len(financing_fresh.errors),
        seed_lineage=seed_lineage,
    )
    summary['captured_at']=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    validate_summary(summary)

    out=args.output_dir; out.mkdir(parents=True,exist_ok=True)
    turnover.to_csv(out/'turnover_history.csv',index=False)
    financing.to_csv(out/'financing_history.csv',index=False)
    turnover_fresh.sse.to_csv(out/'fresh_sse_turnover.csv',index=False)
    turnover_fresh.szse.to_csv(out/'fresh_szse_turnover.csv',index=False)
    turnover_fresh.combined.to_csv(out/'fresh_combined_turnover.csv',index=False)
    turnover_fresh.errors.to_csv(out/'fresh_turnover_errors.csv',index=False)
    financing_fresh.raw.to_csv(out/'fresh_financing_raw_aligned.csv',index=False)
    financing_fresh.canonical.to_csv(out/'fresh_financing_canonical.csv',index=False)
    financing_fresh.errors.to_csv(out/'fresh_financing_errors.csv',index=False)
    pd.DataFrame({'date':trading_calendar}).to_csv(out/'trading_calendar.csv',index=False)
    (out/'input_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    receipt={
        'schema_version':'market-regime-broad-market-capital-v0-capture-receipt-v1',
        'market_date':args.market_date,
        'seed_end_date':str(seed_end.date()),
        'fresh_trading_dates':[str(pd.Timestamp(x).date()) for x in recent_dates],
        'fresh_turnover_error_rows':len(turnover_fresh.errors),
        'fresh_financing_error_rows':len(financing_fresh.errors),
        'financing_latest_available_observation_date':summary['financing']['latest_available_observation_date'],
        'financing_publication_lag_trading_days':summary['financing']['publication_lag_trading_days'],
        'automatic_trigger':False,
    }
    (out/'capture_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    members=[
        'turnover_history.csv','financing_history.csv','fresh_sse_turnover.csv','fresh_szse_turnover.csv',
        'fresh_combined_turnover.csv','fresh_turnover_errors.csv','fresh_financing_raw_aligned.csv',
        'fresh_financing_canonical.csv','fresh_financing_errors.csv','trading_calendar.csv','input_summary.json','capture_receipt.json',
    ]
    rels=[str((out/name)) for name in members]
    manifest=write_bundle_manifest(Path.cwd(),rels,out/'bundle_manifest.json',market_date=args.market_date)
    print(json.dumps({'status':summary['status'],'market_date':summary['market_date'],'turnover_latest':summary['turnover']['latest_observation_date'],'financing_latest':summary['financing']['latest_available_observation_date'],'financing_lag':summary['financing']['publication_lag_trading_days'],'bundle_files':len(manifest['files'])},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
