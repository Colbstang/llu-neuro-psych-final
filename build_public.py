#!/usr/bin/env python3
"""Build the portable public app from its sanitized JSON dataset.

An optional private bundle can be supplied to refresh the guide text before
sanitization. Never commit that bundle; place it under local-private/.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GENERATED_ASSETS = {
    'assets/generated/intracranial-compartments.png',
    'assets/generated/white-matter-cell-targets.png',
    'assets/generated/antiseizure-targets.png',
    'assets/generated/path-completion/imnm.png',
    'assets/generated/path-completion/cpt2-periodic-v2.png',
    'assets/generated/path-completion/feeding-patterns.png',
    'assets/generated/path-completion/apnea-mechanics.png',
}
CSS = [
    'guide_style.css', 'objective_style.css', 'reading_tools.css',
    'continuous_style.css', 'workspace_style.css', 'book_highlights.css',
    'freehand.css', 'study_practice.css', 'anki_context.css',
    'objective_compact.css', 'week8_style.css', 'image_navigation.css',
    'semantic_search.css', 'source_viewer.css',
]
JS = [
    'guide_app.js', 'study_scope.js', 'reference_panel.js',
    'image_navigation.js', 'book_highlights.js', 'objective_ui.js',
    'workspace_views.js', 'reading_tools.js', 'freehand.js',
    'study_practice.js', 'guide_skim.js', 'anki_context.js',
    'semantic_search.js', 'source_viewer.js',
]


class PublicHTML(HTMLParser):
    """Keep explanatory markup, remove embeds/links and bank-specific passages."""

    BLOCKED = re.compile(
        r'question[- ]bank|question appendix|original question|question stem|'
        r'banked question|banked questions|saved-bank application|'
        r'apply the same mechanism to your banked questions|captured question|'
        r'specific tested question|(?:saved\s+)?(?:AMBOSS|UWorld)\s+Q\d+', re.I
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {'script', 'style', 'iframe', 'object', 'img', 'a'}:
            if tag == 'a':
                self.stack.append((tag, False))
            elif tag in {'script', 'style', 'iframe', 'object'}:
                self.drop_depth += 1
                self.stack.append((tag, True))
            return
        if self.drop_depth:
            self.stack.append((tag, True))
            return
        if tag not in {'p','div','span','b','i','u','em','strong','br','table','tr','td','th','thead','tbody','ul','ol','li','sub','sup','blockquote','code'}:
            self.stack.append((tag, False))
            return
        safe_attrs = ''
        if tag in {'td','th'}:
            values = dict(attrs)
            for name in ('colspan','rowspan'):
                value = values.get(name)
                if value and re.fullmatch(r'\d{1,2}', value) and int(value) < 20:
                    safe_attrs += f' {name}="{value}"'
        self.output.append(f'<{tag}{safe_attrs}>')
        self.stack.append((tag, False))

    def handle_endtag(self, tag: str) -> None:
        if not self.stack:
            return
        # Find matching tag and close malformed nesting conservatively.
        index = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        closing = self.stack[index:]
        del self.stack[index:]
        for name, skipped in reversed(closing):
            if name in {'script','style','iframe','object'} and skipped:
                self.drop_depth = max(0, self.drop_depth-1)
            elif not skipped and name in {'p','div','span','b','i','u','em','strong','table','tr','td','th','thead','tbody','ul','ol','li','sub','sup','blockquote','code'}:
                self.output.append(f'</{name}>')

    def handle_data(self, data: str) -> None:
        if self.drop_depth:
            return
        if any(skipped for _, skipped in self.stack):
            return
        self.output.append(html.escape(data))


def sanitize_html(value: str) -> str:
    value = re.sub(
        r'<p\b[^>]*>(?:(?!</p>).)*(?:question[- ]bank|question appendix|original question|question stem|banked question|saved-bank application|captured question|specific tested question|(?:saved\s+)?(?:AMBOSS|UWorld)\s+Q\d+)(?:(?!</p>).)*</p>',
        '', str(value or ''), flags=re.I | re.S,
    )
    value=re.sub(r'Apply the same mechanism to your banked questions','',value,flags=re.I)
    # Keep the educational explanations while removing personal or
    # distribution-specific source naming from the public copy.
    value=re.sub(r"Cole[’']s example is", 'For example,', value, flags=re.I)
    value=re.sub(r"Cole[’']s", 'course', value, flags=re.I)
    value=re.sub(r'\bCole\b', 'course review', value, flags=re.I)
    value=re.sub(r'professor[- ]review', 'course review', value, flags=re.I)
    value=re.sub(r'\bNPS\s+(?:notes|cards)\b', 'course notes', value, flags=re.I)
    value=re.sub(r'(?<=[.!?]\s)course\b', 'Course', value)
    parser = PublicHTML()
    parser.feed(value)
    result = ''.join(parser.output)
    # If the matched attribution appeared after inline markup, remove its whole
    # paragraph as a final conservative cleanup.
    result = re.sub(r'<p>(?:(?!</p>).)*(?:question[- ]bank|question appendix|original question|question stem|banked question|saved-bank application|captured question|specific tested question|(?:saved\s+)?(?:AMBOSS|UWorld)\s+Q\d+)(?:(?!</p>).)*</p>', '', result, flags=re.I | re.S)
    return result


def sanitize(data: dict) -> dict:
    out = copy.deepcopy(data)
    # Asset IDs can change between builds. Verify bytes against the seven
    # reviewed original files instead of retaining hardcoded embedded IDs.
    permitted_hashes = {hashlib.sha256((ROOT/path).read_bytes()).digest(): path
                        for path in GENERATED_ASSETS if (ROOT/path).is_file()}
    generated_refs = {}
    for key, value in data.get('assets', {}).items():
        if not isinstance(value, str) or not value.startswith('data:image/'):
            continue
        try:
            path = permitted_hashes.get(hashlib.sha256(base64.b64decode(value.split(',', 1)[1], validate=True)).digest())
        except (ValueError, IndexError):
            continue
        if path:
            generated_refs['@asset:'+key] = path
    def public_image(src):
        return generated_refs.get(src, src if src in GENERATED_ASSETS else '')
    pages = []
    for page in out.get('pages', []):
        p = {k: page[k] for k in ('id','title','short_title','subtitle','week','keywords','blocks') if k in page}
        p['source_pages'] = []
        p['lecture_sources'] = []
        clean_blocks = []
        for block in page.get('blocks', []):
            b = {k: block[k] for k in ('id','title','summary','skim','html','figures') if k in block}
            b['professor_pages'] = []
            b['lecture_sources'] = []
            b['lo_ids'] = []
            b['html'] = sanitize_html(b.get('html',''))
            b['figures'] = [
                {**{k: f[k] for k in ('caption','answer','prompt','generated') if k in f}, 'src': public_image(f.get('src',''))}
                for f in b.get('figures', []) if f.get('generated') is True and public_image(f.get('src',''))
            ]
            clean_blocks.append(b)
        p['blocks'] = clean_blocks
        pages.append(p)
    out['pages'] = pages
    # No private course objectives, question-bank records, Anki exports, book
    # excerpts, PDF layers, citation maps, or source links in the public payload.
    out['objectives'] = []
    out['objective_answers'] = {}
    out['objective_aliases'] = {}
    out['objective_coverage'] = {'objective_count':0,'inline_objective_count':0,'inline_placements':0,'scope':'Optional private objective records may be imported in a local build.'}
    out['questions'] = []
    out['question_annotations'] = []
    out['question_auto_links'] = []
    out['references'] = {}
    out['book_pages'] = {'books':{'First Aid':{'label':'First Aid','url':'','images':{}},'Pathoma':{'label':'Pathoma','url':'','images':{}}},'keywords':{}}
    out['book_link_focus'] = {'topics':{}}
    out['book_text_layers'] = {'books':{}}
    out['anki_topic_media'] = {'topics':{}}
    out['source_catalog'] = {'documents':[]}
    out['source_notes'] = {'scope':'Optional private source notes are not included in this public dataset.','notes':[]}
    out['source_priority'] = []
    out['review_games'] = {
        kind: {'categories': list(game.get('categories', [])),
               'cases': [{**{k: c[k] for k in ('id','row_index','name','traits','clarification_html') if k in c}, 'sources': [], 'figures': []}
                         for c in game.get('cases', [])]}
        for kind, game in out.get('review_games', {}).items() if kind in {'bugs','drugs'}
    }
    for game in out['review_games'].values():
        for case in game['cases']:
            if 'clarification_html' in case: case['clarification_html'] = sanitize_html(case['clarification_html'])
    out['rapid_pathology'] = []
    out['assets'] = {}
    out['asset_keys'] = {}
    # Drop course and private filesystem citations while retaining the public
    # Quiz 7 / Week 8 scope and priority labels used by the runtime.
    week8 = out.get('week8', {})
    out['week8'] = {k:v for k,v in week8.items() if k in {'enabled','priority_quiz','priority_week','label','scope_label','scope_rows'}}
    out['week8'].update({'objective_ids':[],'supporting_objective_ids':[],'question_ids':[],'source_gaps':[]})
    final = out.get('final', {})
    out['final'] = {k:v for k,v in final.items() if k in {'enabled','priority_week','priority_quiz'}}
    out['word_budget_note'] = 'Detailed guide: no page cap. Use learned-section compaction and skim mode for rapid review.'
    out['word_budget'] = {'words_per_page_equivalent':700,'total_limit':None,'skim_target_page_equivalents':80,'comparison_sheets_excluded':True}
    # Keep only stable public content in comparison sheets and preserve row order.
    sheets=[]
    for sheet in out.get('comparison_sheets',[]):
        s={k:sheet[k] for k in ('id','title','scope_note','columns','rows') if k in sheet}
        if 'scope_note' in s:
            s['scope_note']=sanitize_html(s['scope_note'])
        s['rows']=[{'cells':[sanitize_html(cell) for cell in r.get('cells',[])]} for r in s.get('rows',[])]
        sheets.append(s)
    out['comparison_sheets']=sheets
    # Preserve only the generated original schematics; no Anki, slide, book,
    # lecture-screenshot, or web-reference imagery enters the public payload.
    out['pathology_images'] = {
        str(index): [
            {**{k:image[k] for k in ('caption','answer','prompt','source_kind','recall_prompt','notes') if k in image}, 'src': public_image(image.get('src',''))}
            for image in images
            if image.get('source_kind') == 'Native generated conceptual teaching diagram'
            and public_image(image.get('src',''))
        ]
        for index,images in out.get('pathology_images',{}).items()
        if any(image.get('source_kind') == 'Native generated conceptual teaching diagram'
               and public_image(image.get('src','')) for image in images)
    }
    return out


def build(data: dict, output: Path) -> None:
    template=(ROOT/'guide_template.html').read_text()
    css='\n'.join((ROOT/f).read_text() for f in CSS)
    js='\n'.join((ROOT/f).read_text() for f in JS)+'\ninitReadingTools();render();save();'
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')
    output.write_text(template.replace('__CSS__',css).replace('__DATA__',payload).replace('__JS__',js))


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,default=ROOT/'data/public-study-guide.json')
    parser.add_argument('--output',type=Path,default=ROOT/'index.html')
    parser.add_argument('--sanitize',action='store_true',help='Sanitize a copy of the selected JSON before building.')
    args=parser.parse_args()
    data=json.loads(args.data.read_text())
    if args.sanitize:
        data=sanitize(data)
        args.data.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    build(data,args.output)
    print(f'Built {args.output} ({len(data.get("pages",[]))} chapters).')

if __name__=='__main__':
    main()
