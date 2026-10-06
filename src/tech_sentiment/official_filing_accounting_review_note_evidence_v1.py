from __future__ import annotations
import hashlib,re
from typing import Any
from .official_filing_facts import _normalize_pdf_unicode

PARSER_VERSION='official-filing-accounting-review-note-evidence-v1-exact-heading'
_TOP_HEADING_RE=re.compile(r'^\s*(?P<num>\d{1,3})\s*[、.．]\s*(?P<title>.+?)\s*$')

def _compact(v:object)->str: return ''.join(str(v).split())
def _lines(text:str)->list[str]:
    clean=_normalize_pdf_unicode(text).replace('：',':').replace('．','.').replace('，',',')
    return [line.rstrip() for line in clean.splitlines() if line.strip()]

def extract_review_note_section(text:str,*,note_reference:str,source_row_label:str)->dict[str,Any]:
    note=str(note_reference).strip(); label=_compact(source_row_label)
    if not re.fullmatch(r'\d{1,3}',note): raise ValueError(f'unsupported note reference: {note!r}')
    lines=_lines(text); target=int(note); candidates=[]; label_candidates=[]
    for i,line in enumerate(lines):
        m=_TOP_HEADING_RE.match(line)
        if not m: continue
        window=''.join(_compact(x) for x in lines[i:min(len(lines),i+3)])
        if label and label not in window: continue
        label_candidates.append((i,int(m.group('num'))))
        if int(m.group('num'))==target: candidates.append(i)
    resolved=target; matched=True
    if not candidates:
        nums={num for _,num in label_candidates}
        if len(nums)!=1: raise ValueError(f'official filing note heading not found or label fallback ambiguous: {note}:{source_row_label}')
        resolved=next(iter(nums)); candidates=[i for i,num in label_candidates if num==resolved]; matched=False
    start=candidates[-1]
    end=min(len(lines),start+500)
    for j in range(start+1,min(len(lines),start+500)):
        m=_TOP_HEADING_RE.match(lines[j])
        if m and int(m.group('num'))!=resolved:
            end=j; break
    section='\n'.join(lines[start:end]).strip()
    if not section: raise ValueError('empty note section')
    if label not in _compact(section[:max(len(section),1)]): raise ValueError('note section label mismatch')
    return {
        'requested_note_reference':note,
        'resolved_note_reference':str(resolved),
        'note_reference_match':matched,
        'note_heading':lines[start].strip(),
        'note_section_text':section,
        'note_section_sha256':hashlib.sha256(section.encode('utf-8')).hexdigest(),
        'note_section_line_count':end-start,
        'parser_version':PARSER_VERSION,
        'private_classification_applied':False,
        'outcome_read':False,
    }

__all__=['PARSER_VERSION','extract_review_note_section']
