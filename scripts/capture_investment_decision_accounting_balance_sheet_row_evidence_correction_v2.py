from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import pandas as pd
from tech_sentiment.official_filing_accounting_balance_sheet_row_evidence_correction_v2 import CORRECTION_PARSER_VERSION, extract_row_evidence_correction
from tech_sentiment.official_filing_facts import download_official_document, extract_pdf_text

SCHEMA_VERSION='investment-decision-accounting-balance-sheet-row-evidence-correction-v2'
REQ={'entity_id','period_end','fact_type','source_row_label','statement_unit','evidence_available_date','publication_timestamp','source_identity','provider','document_id','revision_id','document_url','document_sha256','source_row_sha256'}

def _sha(b:bytes)->str: return hashlib.sha256(b).hexdigest()

def _text(row:pd.Series,cache:Path)->str:
    digest=str(row.document_sha256); doc=str(row.document_id); url=str(row.document_url)
    pdf=cache/'documents'/f'{digest}.pdf'; txt=cache/'text'/f'{digest}.txt'; pdf.parent.mkdir(parents=True,exist_ok=True); txt.parent.mkdir(parents=True,exist_ok=True)
    if pdf.exists():
        content=pdf.read_bytes()
        if _sha(content)!=digest: raise ValueError(f'cached document sha mismatch: {doc}')
    else:
        d=download_official_document(url)
        if d.sha256!=digest: raise ValueError(f'official document sha mismatch: {doc}')
        content=d.content; pdf.write_bytes(content)
    if txt.exists(): return txt.read_text(encoding='utf-8')
    text=extract_pdf_text(content); txt.write_text(text,encoding='utf-8'); return text

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--request-csv',type=Path,required=True); p.add_argument('--out-dir',type=Path,required=True); p.add_argument('--cache-dir',type=Path,required=True); p.add_argument('--source-commit',required=True); p.add_argument('--require-no-errors',action='store_true'); a=p.parse_args()
    req=pd.read_csv(a.request_csv,dtype=str,keep_default_na=False); missing=REQ-set(req.columns)
    if missing: raise ValueError(f'correction request columns missing: {sorted(missing)}')
    if req.duplicated(['entity_id','period_end','fact_type','document_id']).any(): raise ValueError('correction request duplicate keys')
    rows=[]; errors=[]
    for doc_id,group in req.groupby('document_id',sort=True):
        identities=group[['document_url','document_sha256']].drop_duplicates()
        if len(identities)!=1: raise ValueError(f'document identity drift: {doc_id}')
        try: text=_text(group.iloc[0],a.cache_dir)
        except Exception as exc:
            errors.append({'document_id':str(doc_id),'entity_id':str(group.iloc[0].entity_id),'fact_type':'DOCUMENT','error':f'{type(exc).__name__}: {exc}'}); continue
        for r in group.itertuples(index=False):
            try:
                c=extract_row_evidence_correction(text,fact_type=str(r.fact_type),supersedes_source_row_sha256=str(r.source_row_sha256))
                if str(c['source_row_label'])!=str(r.source_row_label): raise ValueError('source row label drift')
                if str(c['statement_unit'])!=str(r.statement_unit): raise ValueError('statement unit drift')
                rows.append({'schema_version':SCHEMA_VERSION,'entity_id':str(r.entity_id),'period_end':str(r.period_end),'fact_type':str(r.fact_type),'value':c['current_value_cny'],'unit':'CNY','evidence_available_date':str(r.evidence_available_date),'publication_timestamp':str(r.publication_timestamp),'source_identity':str(r.source_identity),'provider':str(r.provider),'document_id':str(r.document_id),'revision_id':str(r.revision_id),'document_url':str(r.document_url),'document_sha256':str(r.document_sha256),**c})
            except Exception as exc: errors.append({'document_id':str(doc_id),'entity_id':str(r.entity_id),'fact_type':str(r.fact_type),'error':f'{type(exc).__name__}: {exc}'})
    out=pd.DataFrame(rows); err=pd.DataFrame(errors,columns=['document_id','entity_id','fact_type','error']); a.out_dir.mkdir(parents=True,exist_ok=True)
    op=a.out_dir/'accounting_balance_sheet_row_evidence_correction_v2.csv'; ep=a.out_dir/'errors.csv'; out.to_csv(op,index=False); err.to_csv(ep,index=False)
    manifest={'schema_version':'investment-decision-accounting-balance-sheet-row-evidence-correction-manifest-v2','status':'PUBLIC_OFFICIAL_ROW_EVIDENCE_CORRECTION_CAPTURED_NO_PRIVATE_QUALIFICATION','source_commit':a.source_commit,'parser_version':CORRECTION_PARSER_VERSION,'request_sha256':_sha(a.request_csv.read_bytes()),'request_semantics_interpreted':False,'requested_row_count':int(len(req)),'requested_document_count':int(req.document_id.nunique()),'captured_row_count':int(len(out)),'error_row_count':int(len(err)),'supersession_requires_exact_old_source_row_sha':True,'zero_interpretation_applied':False,'private_classification_applied':False,'historical_forward_outcome_read':False,'prospective_forward_outcome_read':False,'evidence_qualification_changed':False,'production_changed':False,'portfolio_action_emitted':False,'trading_authority_changed':False,'files':{op.name:_sha(op.read_bytes()),ep.name:_sha(ep.read_bytes())}}
    (a.out_dir/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(manifest,ensure_ascii=False))
    return 2 if a.require_no_errors and len(err) else 0
if __name__=='__main__': raise SystemExit(main())
