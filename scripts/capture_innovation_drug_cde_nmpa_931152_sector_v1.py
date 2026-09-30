from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import pandas as pd

from tech_sentiment.innovation_drug_931152_issuer_identity_v1 import EXPECTED_MEMBER_COUNT, REGISTRY_ID
from tech_sentiment.innovation_drug_cde_nmpa_browser_capture_v1 import (
    RAW_COLUMNS,
    SOURCE_SPECS,
    capture_cde_nmpa_via_browser,
)

SHANGHAI=ZoneInfo("Asia/Shanghai")


def _normalize_projection_text(frame: pd.DataFrame) -> pd.DataFrame:
    out=frame.copy()
    for column in ("applicant","drug_name","acceptance_no","indication","registration_class","status"):
        if column in out.columns:
            out[column]=out[column].astype(str).map(lambda value: re.sub(r"\s+", " ", value).strip())
    return out


def _load_checkpoint(path: Path, *, company: str, entity_id: str, captured_at: str) -> tuple[pd.DataFrame,dict] | None:
    report_path=path/'capture_report.json'; raw_path=path/'raw_snapshot.csv'
    if not report_path.exists() or not raw_path.exists(): return None
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('target_company_query')!=company or report.get('query_entity_id')!=entity_id: return None
    if report.get('captured_at')!=captured_at: return None
    sources=report.get('sources') or {}
    if set(sources)!=set(SOURCE_SPECS): return None
    if any((sources[name] or {}).get('capture_status')!='COMPLETE' for name in SOURCE_SPECS): return None
    frame=pd.read_csv(raw_path,dtype=str,keep_default_na=False)
    if list(frame.columns)!=RAW_COLUMNS: return None
    if int(report.get('raw_rows',-1))!=len(frame): return None
    return frame,report


def main()->int:
    p=argparse.ArgumentParser(description='Capture all frozen 931152 issuer legal-name queries from official CDE endpoints via one real browser CDP session.')
    p.add_argument('--cdp-url',default='http://127.0.0.1:9223')
    p.add_argument('--issuer-registry-json',type=Path,required=True)
    p.add_argument('--captured-at',help='Timezone-aware batch capture timestamp; defaults to current Asia/Shanghai time.')
    p.add_argument('--page-size',type=int,default=500)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args()
    registry=json.loads(a.issuer_registry_json.read_text(encoding='utf-8'))
    if registry.get('registry_id')!=REGISTRY_ID or int(registry.get('resolved_issuer_rows',-1))!=EXPECTED_MEMBER_COUNT:
        raise ValueError('931152 issuer registry not fully resolved')
    issuers=list(registry.get('issuers') or [])
    if len(issuers)!=EXPECTED_MEMBER_COUNT: raise ValueError('931152 issuer query set must contain exact 86 issuers')
    when=datetime.fromisoformat(a.captured_at) if a.captured_at else datetime.now(SHANGHAI)
    if when.tzinfo is None: raise ValueError('--captured-at must be timezone-aware')
    when=when.astimezone(SHANGHAI)
    captured_at=when.isoformat(timespec='seconds')
    a.output_dir.mkdir(parents=True,exist_ok=True)
    queries_dir=a.output_dir/'queries'; queries_dir.mkdir(exist_ok=True)
    frames=[]; query_rows=[]; errors=[]
    for index,issuer in enumerate(sorted(issuers,key=lambda x:str(x['symbol'])),1):
        symbol=str(issuer['symbol']); entity_id=str(issuer['entity_id']); company=str(issuer['listed_issuer_legal_name'])
        qdir=queries_dir/symbol; qdir.mkdir(exist_ok=True)
        checkpoint=_load_checkpoint(qdir,company=company,entity_id=entity_id,captured_at=captured_at)
        try:
            if checkpoint is None:
                result=capture_cde_nmpa_via_browser(
                    cdp_url=a.cdp_url,target_company=company,page_size=a.page_size,
                    captured_at=when,allow_empty=True,
                )
                frame=result.raw_rows.copy(); report=dict(result.report)
                report['query_symbol']=symbol; report['query_entity_id']=entity_id
                frame.to_csv(qdir/'raw_snapshot.csv',index=False)
                (qdir/'raw_responses.json').write_text(json.dumps(result.raw_responses,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
                (qdir/'detail_responses.json').write_text(json.dumps(result.detail_responses,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
                (qdir/'capture_report.json').write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
                mode='captured'
            else:
                frame,report=checkpoint; mode='checkpoint'
            augmented=_normalize_projection_text(frame)
            augmented['capture_query_symbol']=symbol; augmented['capture_query_entity_id']=entity_id; augmented['capture_query_company']=company
            frames.append(augmented)
            source_totals={name:int(report['sources'][name]['records_captured']) for name in SOURCE_SPECS}
            query_rows.append({
                'symbol':symbol,'entity_id':entity_id,'company_query':company,'raw_rows':int(len(frame)),
                **{f'{name}_rows':source_totals[name] for name in SOURCE_SPECS},
                'all_source_categories_complete':all(report['sources'][name]['capture_status']=='COMPLETE' for name in SOURCE_SPECS),
                'mode':mode,
            })
            print(f'[{index}/{len(issuers)}] {symbol} {company}: {len(frame)} rows ({mode})',flush=True)
        except Exception as exc:
            errors.append({'symbol':symbol,'entity_id':entity_id,'company_query':company,'error':repr(exc)})
            print(f'[{index}/{len(issuers)}] ERROR {symbol} {company}: {exc!r}',flush=True)
            break
    if errors:
        (a.output_dir/'query_errors.json').write_text(json.dumps(errors,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        raise RuntimeError('931152 CDE query set stopped after first error; checkpoints preserved')
    aggregate=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame(columns=RAW_COLUMNS+['capture_query_symbol','capture_query_entity_id','capture_query_company'])
    aggregate.to_csv(a.output_dir/'cde_nmpa_931152_raw_query_rows.csv',index=False)
    aggregate_raw_responses = {}
    aggregate_detail_responses = {}
    for issuer in sorted(issuers,key=lambda x:str(x['symbol'])):
        symbol=str(issuer['symbol']); qdir=queries_dir/symbol
        aggregate_raw_responses[symbol]=json.loads((qdir/'raw_responses.json').read_text(encoding='utf-8'))
        aggregate_detail_responses[symbol]=json.loads((qdir/'detail_responses.json').read_text(encoding='utf-8'))
    (a.output_dir/'cde_nmpa_931152_official_raw_responses.json').write_text(
        json.dumps(aggregate_raw_responses,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8'
    )
    (a.output_dir/'cde_nmpa_931152_official_detail_responses.json').write_text(
        json.dumps(aggregate_detail_responses,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8'
    )
    completed=datetime.now(SHANGHAI).isoformat(timespec='seconds')
    receipt={
        'receipt_id':'INNOVATION_DRUG_CDE_NMPA_931152_QUERY_RECEIPT_V1',
        'date':'2026-09-30','index_code':'931152','captured_at':captured_at,'batch_completed_at':completed,
        'transport':'LOCAL_REAL_BROWSER_CDP_SAME_ORIGIN_OFFICIAL_ENDPOINTS','company_query_count':len(query_rows),
        'complete_company_query_count':sum(bool(x['all_source_categories_complete']) for x in query_rows),
        'query_error_count':0,'all_source_categories_complete':all(bool(x['all_source_categories_complete']) for x in query_rows),
        'raw_query_rows_before_cross_query_dedup':int(len(aggregate)),
        'zero_result_company_query_count':sum(int(x['raw_rows'])==0 for x in query_rows),
        'query_identity':'CURRENT_OFFICIAL_LISTED_ISSUER_LEGAL_NAME_EXACT',
        'completeness_semantics':'ALL_FROZEN_MEMBER_COMPANY_QUERIES_EXHAUSTIVE_PAGINATION',
        'historical_legal_name_alias_completeness_claimed':False,'affiliate_completeness_claimed':False,
        'full_source_category_completeness_claimed':False,'waf_bypass_used':False,
        'fuzzy_matching_used':False,'substring_matching_used':False,'affiliate_inference_used':False,
        'historical_outcomes_read':False,'prospective_outcomes_read':False,'direction_classified':False,
        'predictive_weight_assigned':False,'sector_score_computed':False,'evidence_qualification_changed':False,
        'production_permission':'NONE','trading_authority':False,'queries':query_rows,
    }
    (a.output_dir/'query_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    (a.output_dir/'query_errors.json').write_text('[]\n',encoding='utf-8')
    print(json.dumps({k:receipt[k] for k in ('company_query_count','complete_company_query_count','raw_query_rows_before_cross_query_dedup','zero_result_company_query_count')},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
