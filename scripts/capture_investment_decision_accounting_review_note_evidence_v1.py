from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import pandas as pd
from tech_sentiment.official_filing_accounting_review_note_evidence_v1 import PARSER_VERSION,extract_review_note_section
from tech_sentiment.official_filing_facts import download_official_document,extract_pdf_text

SCHEMA='investment-decision-accounting-review-note-evidence-v1'
REQ_SCHEMA='accounting-review-classification-note-request-v1'

def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def doc_text(*,doc_id:str,url:str,expected:str,cache:Path)->str:
    pd_=cache/'documents'; td=cache/'text'; pd_.mkdir(parents=True,exist_ok=True); td.mkdir(parents=True,exist_ok=True)
    pp=pd_/f'{expected}.pdf'; tp=td/f'{expected}.txt'
    if pp.exists(): content=pp.read_bytes();
    else:
        d=download_official_document(url)
        if d.sha256!=expected: raise ValueError(f'document sha mismatch: {doc_id}')
        content=d.content; pp.write_bytes(content)
    if sha(content)!=expected: raise ValueError(f'cached document sha mismatch: {doc_id}')
    if tp.exists(): return tp.read_text(encoding='utf-8')
    text=extract_pdf_text(content); tp.write_text(text,encoding='utf-8'); return text

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--request-csv',type=Path,required=True); p.add_argument('--source-commit',required=True); p.add_argument('--out-dir',type=Path,required=True); p.add_argument('--cache-dir',type=Path,required=True); a=p.parse_args()
    req=pd.read_csv(a.request_csv,dtype=str,keep_default_na=False)
    if not req['schema_version'].eq(REQ_SCHEMA).all(): raise ValueError('note request schema drift')
    rows=[]; errors=[]; cache={}
    for r in req.to_dict('records'):
        try:
            did=str(r['document_id'])
            if did not in cache:
                cache[did]=doc_text(doc_id=did,url=str(r['document_url']),expected=str(r['document_sha256']),cache=a.cache_dir)
            ev=extract_review_note_section(cache[did],note_reference=str(r['note_reference']),source_row_label=str(r['source_row_label']))
            rows.append({
                'schema_version':SCHEMA,'entity_id':str(r['entity_id']),'period_end':str(r['period_end']),'fact_type':str(r['fact_type']),
                'note_reference':str(r['note_reference']),'source_row_label':str(r['source_row_label']),'current_value_cny':str(r['current_value_cny']),
                'evidence_available_date':str(r['evidence_available_date']),'publication_timestamp':str(r['publication_timestamp']),
                'source_identity':str(r['source_identity']),'provider':str(r['provider']),'document_id':did,'revision_id':str(r['revision_id']),
                'document_url':str(r['document_url']),'document_sha256':str(r['document_sha256']),'source_row_sha256':str(r['source_row_sha256']),
                **ev,
            })
        except Exception as exc:
            errors.append({'entity_id':str(r['entity_id']),'period_end':str(r['period_end']),'fact_type':str(r['fact_type']),'document_id':str(r['document_id']),'error':f'{type(exc).__name__}: {exc}'})
    out=pd.DataFrame(rows); err=pd.DataFrame(errors,columns=['entity_id','period_end','fact_type','document_id','error'])
    if len(out): out=out.sort_values(['entity_id','period_end','fact_type']).reset_index(drop=True)
    a.out_dir.mkdir(parents=True,exist_ok=True); op=a.out_dir/'accounting_review_note_evidence.csv'; ep=a.out_dir/'errors.csv'; out.to_csv(op,index=False); err.to_csv(ep,index=False)
    manifest={'schema_version':'investment-decision-accounting-review-note-evidence-manifest-v1','status':'PUBLIC_OFFICIAL_REVIEW_NOTE_EVIDENCE_CAPTURED_NO_PRIVATE_CLASSIFICATION','source_commit':a.source_commit,'parser_version':PARSER_VERSION,'request_sha256':sha(a.request_csv.read_bytes()),'requested_row_count':len(req),'requested_document_count':int(req.document_id.nunique()),'captured_row_count':len(out),'error_row_count':len(err),'request_semantics_interpreted':False,'private_classification_applied':False,'historical_forward_outcome_read':False,'prospective_forward_outcome_read':False,'evidence_qualification_changed':False,'production_changed':False,'portfolio_action_emitted':False,'trading_authority_changed':False,'files':{op.name:sha(op.read_bytes()),ep.name:sha(ep.read_bytes())}}
    (a.out_dir/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(manifest,ensure_ascii=False)); return 2 if len(err) else 0
if __name__=='__main__': raise SystemExit(main())
