import json
from pathlib import Path

import pandas as pd

from tech_sentiment.innovation_drug_cde_nmpa_931152_sector_v1 import (
    build_sector_manifest,
    materialize_931152_sector_cde,
)
from tech_sentiment.innovation_drug_cde_nmpa_browser_capture_v1 import RAW_COLUMNS, normalize_cde_browser_capture_rows
from tech_sentiment.innovation_drug_cde_nmpa_snapshot_v1 import load_cde_snapshot_contract
from tech_sentiment.innovation_drug_sector_kpi_raw_v1 import validate_931152_membership_scope

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/'reference/innovation_drug_cde_nmpa_snapshot_v1_contract.json'
MEMBERSHIP=ROOT/'data/reference/sector_931152_kpi_membership_scope_v1.csv'
URL_PRIORITY='https://www.cde.org.cn/main/xxgk/listpage/2f78f372d351c6851af7431c7710a731'
URL_IMPLIED='https://www.cde.org.cn/main/xxgk/listpage/4b5255eb0a84820cef4ca3e8b6bbe20c'


def _mapping():
    return {'mappings':[
        {'applicant_name_exact':'江苏恒瑞医药股份有限公司','entity_id':'600276.SH','mapping_basis':'EXACT_LISTED_ISSUER_LEGAL_NAME','evidence':{'source_url':'https://www.sse.com.cn/600276'}},
        {'applicant_name_exact':'丽珠医药集团股份有限公司','entity_id':'000513.SZ','mapping_basis':'EXACT_LISTED_ISSUER_LEGAL_NAME','evidence':{'source_url':'https://www.szse.cn/000513'}},
    ]}


def _receipt():
    return {
        'receipt_id':'INNOVATION_DRUG_CDE_NMPA_931152_QUERY_RECEIPT_V1',
        'company_query_count':86,'complete_company_query_count':86,'query_error_count':0,
        'all_source_categories_complete':True,'full_source_category_completeness_claimed':False,
        'queries':[{'raw_rows':0} for _ in range(86)],
    }


def test_empty_company_query_is_valid_complete_query_result():
    frame=normalize_cde_browser_capture_rows(
        {'priority':[],'breakthrough':[],'clinical':[],'conditional':[]},
        {'priority':{},'breakthrough':{}},allow_empty=True,
    )
    assert frame.empty
    assert list(frame.columns)==RAW_COLUMNS


def test_sector_multi_entity_exact_token_fanout_and_pit_filtering_are_explicit():
    contract=load_cde_snapshot_contract(CONTRACT)
    membership=validate_931152_membership_scope(pd.read_csv(MEMBERSHIP,dtype={'symbol':str}))
    manifest=build_sector_manifest(
        contract=contract,mapping_registry_sha256='a'*64,captured_at='2026-09-30T18:00:00+08:00',
        membership_scope_sha256='b'*64,issuer_identity_registry_sha256='c'*64,query_receipt_sha256='d'*64,
    )
    raw=pd.DataFrame([
        {
            'category':'纳入优先审评品种名单','source_url':URL_PRIORITY,'source_record_id':'shared-historical',
            'applicant':'丽珠医药集团股份有限公司;江苏恒瑞医药股份有限公司','drug_name':'TEST-HIST','acceptance_no':'A1',
            'indication':'I','registration_class':'','publication_date':'2021-01-04','approval_date':'','status':'已纳入',
        },
        {
            'category':'临床试验默示许可','source_url':URL_IMPLIED,'source_record_id':'shared-first-observed',
            'applicant':'丽珠医药集团股份有限公司;江苏恒瑞医药股份有限公司','drug_name':'TEST-FWD','acceptance_no':'A2',
            'indication':'I','registration_class':'','publication_date':'','approval_date':'','status':'',
        },
    ])
    result=materialize_931152_sector_cde(
        raw=raw,query_receipt=_receipt(),contract=contract,mapping_registry=_mapping(),membership=membership,
        trading_dates=pd.to_datetime(['2021-01-04','2021-01-05','2026-09-30','2026-10-08']),manifest=manifest,
    )
    assert result.summary['multi_entity_fanout_source_rows']==2
    assert len(result.all_events)==4
    assert set(result.all_events['entity_id'])=={'600276.SH','000513.SZ'}
    assert len(result.pit_events)==2
    assert set(result.pit_events['entity_id'])=={'600276.SH','000513.SZ'}
    assert len(result.excluded_events)==2
    assert set(result.excluded_events['pit_exclusion_reason'])=={'AFTER_QUALIFIED_931152_MEMBERSHIP_SCOPE_END'}
    assert result.summary['pit_member_cde_latest_evidence_available_date']=='2021-01-05'
    assert result.summary['formal_sector_kpi_state']=='DATA_INSUFFICIENT'
    assert result.summary['sector_kpi_qualified'] is False
    assert result.summary['event_weight_defined'] is False
    assert result.summary['sector_score_defined'] is False
    assert result.summary['historical_outcomes_read'] is False
    assert result.summary['prospective_outcomes_read'] is False
    assert result.summary['production_permission']=='NONE'
    assert result.summary['trading_authority'] is False
    assert result.manifest['capture_scope']['full_source_category_completeness_claimed'] is False
    assert result.manifest['capture_scope']['historical_legal_name_alias_completeness_claimed'] is False


def test_committed_931152_sector_snapshot_is_complete_query_set_but_pit_bounded():
    import hashlib
    base=ROOT/'data/reference/innovation_drug_cde_nmpa_931152_snapshots/2026-09-30'
    identity=ROOT/'data/reference/innovation_drug_931152_issuer_identity_snapshot_2026-09-30'
    status=json.loads((ROOT/'reference/innovation_drug_cde_nmpa_931152_sector_v1_status.json').read_text())
    contract=json.loads((ROOT/'reference/innovation_drug_cde_nmpa_931152_sector_v1_contract.json').read_text())
    receipt=json.loads((base/'query_receipt.json').read_text())
    summary=json.loads((base/'materialized/cde_nmpa_931152_sector_summary.json').read_text())
    issuer=json.loads((identity/'issuer_identity_registry.json').read_text())
    mapping=json.loads((identity/'cde_nmpa_931152_entity_mapping_v1.json').read_text())
    bundle=json.loads((base/'bundle_manifest.json').read_text())
    assert issuer['resolved_issuer_rows']==86 and issuer['unresolved_issuer_rows']==0
    assert len(issuer['issuers'])==86 and len({x['listed_issuer_legal_name'] for x in issuer['issuers']})==86
    assert len(mapping['mappings'])==86
    assert mapping['fuzzy_matching_allowed'] is False
    assert mapping['substring_matching_allowed'] is False
    assert mapping['affiliate_inference_allowed'] is False
    assert receipt['company_query_count']==receipt['complete_company_query_count']==86
    assert receipt['query_error_count']==0 and receipt['all_source_categories_complete'] is True
    assert receipt['raw_query_rows_before_cross_query_dedup']==2042
    assert receipt['zero_result_company_query_count']==36
    assert summary['exact_mapped_projection_rows']==2020
    assert summary['unmapped_source_rows']==22
    assert summary['historical_reconstructable_rows_total']==209
    assert summary['prospective_first_observed_rows_total']==1811
    assert summary['pit_member_cde_event_rows_through_qualified_scope']==85
    assert summary['pit_member_cde_entities_with_events']==12
    assert summary['pit_member_cde_latest_evidence_available_date']=='2026-05-11'
    assert summary['excluded_rows_by_reason']=={
        'AFTER_QUALIFIED_931152_MEMBERSHIP_SCOPE_END':1813,
        'ENTITY_NOT_ACTIVE_931152_MEMBER_AT_EVIDENCE_DATE':122,
    }
    assert status['formal_sector_kpi_state']=='DATA_INSUFFICIENT'
    assert status['sector_kpi_qualified'] is False
    assert status['production_permission']=='NONE'
    assert status['trading_authority'] is False
    assert contract['membership']['scope_end']=='2026-09-11'
    assert contract['membership']['current_constituent_backfill_allowed'] is False
    assert contract['capture_scope']['historical_legal_name_alias_completeness_claimed'] is False
    assert contract['capture_scope']['affiliate_completeness_claimed'] is False
    assert contract['capture_scope']['full_source_category_completeness_claimed'] is False
    assert bundle['public_only'] is True
    assert bundle['contains_private_model_or_signal'] is False
    assert bundle['historical_outcomes_read'] is False
    assert bundle['sector_score_defined'] is False
    membership_bytes=(ROOT/'data/reference/sector_931152_kpi_membership_scope_v1.csv').read_bytes()
    assert issuer['membership_scope_sha256']==hashlib.sha256(membership_bytes).hexdigest()
    pit=pd.read_csv(base/'materialized/cde_nmpa_931152_pit_member_raw_events.csv',dtype=str)
    assert len(pit)==85
    assert set(pit['source_identity'])=={'NMPA_CDE_PUBLISHED_NOTICE_ARCHIVE'}
    assert pd.to_datetime(pit['evidence_available_date']).max()<=pd.Timestamp('2026-09-11')
    unmapped=pd.read_csv(base/'materialized/cde_nmpa_931152_unmapped_source_rows.csv',dtype=str).fillna('')
    mapped_names={x['applicant_name_exact'] for x in mapping['mappings']}
    import re
    for applicant in unmapped['applicant']:
        tokens={x.strip() for x in re.split(r'[;；\n、]+',applicant) if x.strip()}
        assert not (tokens & mapped_names)
