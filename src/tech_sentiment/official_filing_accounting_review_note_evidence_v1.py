from __future__ import annotations
import hashlib,re
from typing import Any
from .official_filing_facts import _normalize_pdf_unicode

PARSER_VERSION='official-filing-accounting-review-note-evidence-v1-exact-heading'
_TOP_HEADING_RE=re.compile(r'^\s*(?P<num>\d{1,3})\s*[、.．]\s*(?P<title>.+?)\s*$')
_CHAPTER_NOTE_REF_RE=re.compile(
    r'^\s*[一二三四五六七八九十百]+'
    r'(?:(?:[、.．\-]\s*[（(]?\s*(?P<n1>\d{1,3})\s*[）)]?)|'
    r'(?:\s*[（(]\s*(?P<n2>\d{1,3})\s*[）)]))\s*$'
)

def _compact(v:object)->str: return ''.join(str(v).split())
def _lines(text:str)->list[str]:
    clean=_normalize_pdf_unicode(text).replace('：',':').replace('．','.').replace('，',',')
    return [line.rstrip() for line in clean.splitlines() if line.strip()]

def _normalized_note_reference(value:object)->str:
    raw=str(value).strip()
    if re.fullmatch(r'\d{1,3}',raw):
        return raw
    m=_CHAPTER_NOTE_REF_RE.fullmatch(raw)
    if m:
        return str(m.group('n1') or m.group('n2'))
    raise ValueError(f'unsupported note reference: {raw!r}')

def _consolidated_item_note_bounds(lines:list[str])->tuple[int,int]|None:
    starts=[]
    for i,line in enumerate(lines):
        c=_compact(line)
        if '合并财务报表' in c and ('项目注释' in c or '项目附注' in c):
            starts.append(i)
    if not starts:
        return None
    # Prefer the last explicit consolidated item-note heading.  This is used
    # only for label fallback after the requested note number failed globally.
    floor=starts[-1]
    ceiling=len(lines)
    for i in range(floor+1,len(lines)):
        c=_compact(lines[i])
        if '母公司财务报表' in c and ('项目注释' in c or '项目附注' in c):
            ceiling=i
            break
    return floor,ceiling


def extract_review_note_section(text:str,*,note_reference:str,source_row_label:str)->dict[str,Any]:
    requested_note=str(note_reference).strip()
    note=_normalized_note_reference(requested_note)
    label=_compact(source_row_label)
    lines=_lines(text); target=int(note); candidates=[]; label_candidates=[]

    # Normal route: preserve the previously successful global exact-number
    # lookup.  Do not require a report-specific section marker here.
    for i,line in enumerate(lines):
        m=_TOP_HEADING_RE.match(line)
        if not m: continue
        window=''.join(_compact(x) for x in lines[i:min(len(lines),i+3)])
        if label and label not in window: continue
        item=(i,int(m.group('num')))
        label_candidates.append(item)
        if item[1]==target: candidates.append(i)

    resolved=target; matched=True
    if not candidates:
        # Fallback is deliberately narrower than the normal route.  A source
        # statement can carry a mistaken note reference (real example: the
        # same number points to two different balance-sheet rows).  We may
        # recover only by exact row-label heading evidence, and when multiple
        # note numbers share that label we narrow to the consolidated statement
        # item-note block.  We never silently rewrite the requested reference.
        fallback=label_candidates
        nums={num for _,num in fallback}
        if len(nums)!=1:
            bounds=_consolidated_item_note_bounds(lines)
            if bounds is not None:
                floor,ceiling=bounds
                fallback=[item for item in fallback if floor<=item[0]<ceiling]
                nums={num for _,num in fallback}
        if len(nums)!=1:
            raise ValueError(f'official filing note heading not found or label fallback ambiguous: {requested_note}:{source_row_label}')
        resolved=next(iter(nums)); candidates=[i for i,num in fallback if num==resolved]; matched=False

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
        'requested_note_reference':requested_note,
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
