import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

from tech_sentiment.innovation_drug_931152_issuer_identity_v1 import (
    EXPECTED_MEMBER_COUNT,
    MAPPING_REGISTRY_ID,
    REGISTRY_ID,
    membership_symbols,
    resolve_issuer_identities,
)

ROOT=Path(__file__).resolve().parents[1]
MEMBERSHIP=ROOT/'data/reference/sector_931152_kpi_membership_scope_v1.csv'


def test_official_issuer_resolver_covers_exact_frozen_member_union_with_no_fuzzy_logic():
    symbols=membership_symbols(pd.read_csv(MEMBERSHIP,dtype={'symbol':str}))
    seen=[]
    def fetcher(url,referer):
        query=parse_qs(urlparse(url).query); symbol=(query.get('STOCK_CODE') or query.get('secCode'))[0]
        seen.append(symbol)
        if 'query.sse.com.cn' in url:
            payload={'result':[{'A_STOCK_CODE':symbol,'FULL_NAME':f'官方公司{symbol}股份有限公司','SEC_NAME_CN':f'沪{symbol}'}]}
            return 'cb931152('+json.dumps(payload,ensure_ascii=False)+')'
        return json.dumps({'code':'0','data':{'agdm':symbol,'gsqc':f'官方公司{symbol}股份有限公司','agdjc':f'深{symbol}'}},ensure_ascii=False)
    result=resolve_issuer_identities(
        symbols,fetcher=fetcher,membership_scope_sha256='a'*64,captured_at='2026-09-30T18:00:00+08:00'
    )
    assert len(seen)==EXPECTED_MEMBER_COUNT==86
    assert result.registry['registry_id']==REGISTRY_ID
    assert result.registry['resolved_issuer_rows']==86
    assert result.registry['unresolved_issuer_rows']==0
    assert result.mapping_registry['registry_id']==MAPPING_REGISTRY_ID
    assert len(result.mapping_registry['mappings'])==86
    assert result.mapping_registry['fuzzy_matching_allowed'] is False
    assert result.mapping_registry['substring_matching_allowed'] is False
    assert result.mapping_registry['affiliate_inference_allowed'] is False
    assert result.mapping_registry['multi_entity_exact_fanout_allowed'] is True
    assert len({x['listed_issuer_legal_name'] for x in result.rows})==86
    star=next(x for x in result.rows if x['symbol'].startswith('688'))
    assert 'STOCK_TYPE=8' in star['source_api_url']
