from tech_sentiment.official_filing_accounting_review_note_evidence_v1 import extract_review_note_section

def test_extracts_exact_top_level_note_and_stops_at_next_note():
 text='''财务报表附注\n1、货币资金\n项目 期末余额\n库存现金 10\n银行存款 90\n2、交易性金融资产\n项目 期末余额\n债券 5'''
 x=extract_review_note_section(text,note_reference='1',source_row_label='货币资金')
 assert x['note_heading']=='1、货币资金'
 assert '银行存款 90' in x['note_section_text']
 assert '交易性金融资产' not in x['note_section_text']
 assert x['private_classification_applied'] is False

def test_toc_like_earlier_candidate_does_not_override_last_actual_note():
 text='''目录\n1、货币资金 10\n财务报表附注\n1、货币资金\n受限制的货币资金 20\n2、其他应收款\n押金 1'''
 x=extract_review_note_section(text,note_reference='1',source_row_label='货币资金')
 assert '受限制的货币资金 20' in x['note_section_text']


def test_exact_label_fallback_records_reference_mismatch_without_silent_correction():
 text="""财务报表附注
23．递延所得税资产、递延所得税负债
二、递延所得税负债 90
24．其他非流动资产
项目 1"""
 x=extract_review_note_section(text,note_reference='42',source_row_label='递延所得税负债')
 assert x['resolved_note_reference']=='23'
 assert x['requested_note_reference']=='42'
 assert x['note_reference_match'] is False
 assert '二、递延所得税负债 90' in x['note_section_text']
