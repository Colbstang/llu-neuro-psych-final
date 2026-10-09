#!/usr/bin/env python3
"""Build an ignored, personal edition from a local app bundle/source pack.

The private payload and copied source assets stay below local-private/.
"""
from __future__ import annotations

import argparse
import html
import hashlib
import json
import shutil
import urllib.parse
import re
from pathlib import Path
from html.parser import HTMLParser

import build_public

ROOT=Path(__file__).resolve().parent
PRIVATE=ROOT/'local-private'
RUNTIME=PRIVATE/'runtime'
ALLOWED_HTML_TAGS={'p','div','span','a','b','i','u','em','strong','br','img','table','tr','td','th','thead','tbody','tfoot','ul','ol','li','sub','sup','blockquote','code','pre','mark','del','ins','details','summary'}
VOID_HTML_TAGS={'br','img'}
DROP_CONTENT_TAGS={'script','style','iframe','object','embed','svg','math','form','input','button','select','option','textarea','video','audio','canvas','template','noscript','link','meta','base'}
VOID_DROP_TAGS={'embed','input','link','meta','base'}

def assigned_json(text: str, marker: str):
    start=text.find(marker)
    if start<0: raise ValueError(f'Expected {marker!r} was not found.')
    start+=len(marker)
    while start<len(text) and text[start].isspace(): start+=1
    value,_=json.JSONDecoder().raw_decode(text[start:])
    return value

def load_data(path: Path|None, pack_path: Path|None) -> tuple[dict,dict]:
    anki={"notes":[],"cards":[],"scope":{},"graph":{}}
    if pack_path:
        pack=json.loads(pack_path.read_text())
        if pack.get('format')!='llu-neuro-private-source-pack-v1':
            raise ValueError('Source pack must use llu-neuro-private-source-pack-v1.')
        imported=pack.get('data',{})
        data=(imported if imported.get('pages') else json.loads((ROOT/'data/public-study-guide.json').read_text()))
        if data is not imported:
            data.update(imported)
        for key in ('objectives','objective_answers','questions','question_annotations','references','book_pages','book_text_layers','source_catalog','anki_topic_media','pathology_images','review_games','rapid_pathology'):
            if key in pack: data[key]=pack[key]
        if isinstance(pack.get('assets'),dict): data.setdefault('assets',{}).update(pack['assets'])
        if isinstance(pack.get('anki_context'),dict): anki=pack['anki_context']
        elif isinstance(data.get('anki_context'),dict): anki=data['anki_context']
        return data,anki
    if path is None: raise ValueError('Provide --bundle or --pack under local-private/.')
    data=assigned_json(path.read_text(errors='replace'),'const DATA=')
    return data,anki

def local_copy(url: str, base: Path, output: Path) -> str:
    if not url:return ''
    if url.startswith(('http://','https://')):return urllib.parse.quote(url,safe='/:?#[]@!$&()*+,;=%')
    if url.startswith(('@asset:','#')):return url
    if url.lower().startswith('data:'):
        return url if re.match(r'^data:image/(?:png|jpe?g|gif|webp);base64,',url,re.I) and len(url)<=20_000_000 else ''
    parsed=urllib.parse.urlsplit(urllib.parse.unquote(url))
    if parsed.scheme and parsed.scheme.lower()!='file':return ''
    before,sep,fragment=url.partition('#')
    decoded=urllib.parse.unquote(before)
    if decoded.startswith('file://'): decoded=decoded[7:]
    candidates=[]
    path=Path(decoded)
    if path.is_absolute(): candidates.append(path)
    else: candidates.extend([base/path,base.parent/path,base.parent/'Question Appendix'/path,PRIVATE/'media'/path.name,ROOT/path])
    source=next((p.resolve() for p in candidates if p.is_file()),None)
    if source is None:
        return '' if path.is_absolute() or decoded.startswith('../') else urllib.parse.quote(url,safe='/:?#[]@!$&()*+,;=%')
    digest=hashlib.sha256(str(source).encode()).hexdigest()[:10]
    name=f'{digest}-{source.name}'
    target=output/'imported-sources'/name
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists(): shutil.copy2(source,target)
    encoded_name=urllib.parse.quote(name,safe='-._~')
    return f'imported-sources/{encoded_name}'+(sep+fragment if sep else '')

def safe_html_url(value: str, kind: str, base: Path, output: Path, preserve_anki_media: bool=False) -> str:
    raw=html.unescape(str(value or '')).strip()
    if not raw or raw.startswith('#'):
        return raw
    if kind=='src' and re.fullmatch(r'@asset:asset\d+',raw):
        return raw
    if kind=='src' and re.match(r'^data:image/(?:png|jpe?g|gif|webp);base64,',raw,re.I):
        return raw if len(raw)<=20_000_000 else ''
    decoded=urllib.parse.unquote(raw)
    if decoded.startswith('//'):
        return ''
    parsed=urllib.parse.urlsplit(decoded)
    if parsed.scheme:
        if kind=='href' and parsed.scheme.lower() in {'http','https'}:
            return raw
        if parsed.scheme.lower()!='file':
            return ''
    if kind=='src' and parsed.scheme in {'http','https'}:
        return ''
    if preserve_anki_media and re.match(r'^(?:\.\./)?Anki/media/',decoded,re.I):
        return raw
    return local_copy(raw,base,output)

class SafeHTML(HTMLParser):
    """Allow passive study markup, with URL and attribute filtering."""
    def __init__(self,base:Path,output:Path,preserve_anki_media:bool=False):
        super().__init__(convert_charrefs=True)
        self.base=base;self.output=output;self.preserve_anki_media=preserve_anki_media
        self.parts=[];self.stack=[];self.drop_depth=0

    def handle_starttag(self,tag,attrs):
        tag=tag.lower()
        if self.drop_depth:
            self.stack.append((tag,False,False));return
        if tag in DROP_CONTENT_TAGS:
            if tag in VOID_DROP_TAGS:return
            self.stack.append((tag,False,True));self.drop_depth+=1;return
        if tag not in ALLOWED_HTML_TAGS:
            self.stack.append((tag,False,False));return
        safe=[]
        values=dict(attrs)
        class_value=values.get('class')
        if class_value and re.fullmatch(r'[A-Za-z0-9_ -]{1,256}',class_value):
            safe.append(('class',' '.join(class_value.split())))
        for name in ('alt','title'):
            if values.get(name) is not None and tag in {'img','a','span','div','td','th'}:
                safe.append((name,values[name]))
        if tag=='a' and values.get('href'):
            href=safe_html_url(values['href'],'href',self.base,self.output,self.preserve_anki_media)
            if href:safe.append(('href',href))
        if tag=='img':
            src=safe_html_url(values.get('src',''),'src',self.base,self.output,self.preserve_anki_media)
            if src:safe.append(('src',src))
        for name in ('width','height'):
            value=values.get(name)
            if value and value.isdigit() and 0<int(value)<=4096:safe.append((name,value))
        for name in ('colspan','rowspan'):
            value=values.get(name)
            if value and value.isdigit() and 0<int(value)<=100:safe.append((name,value))
        attrs_text=''.join(f' {name}="{html.escape(str(value),quote=True)}"' for name,value in safe)
        self.parts.append(f'<{tag}{attrs_text}>')
        emit_end=tag not in VOID_HTML_TAGS
        self.stack.append((tag,emit_end,False))

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag.lower() not in VOID_HTML_TAGS:self.handle_endtag(tag)

    def handle_endtag(self,tag):
        tag=tag.lower()
        index=next((i for i in range(len(self.stack)-1,-1,-1) if self.stack[i][0]==tag),None)
        if index is None:return
        closing=self.stack[index:];del self.stack[index:]
        for name,emit,drop in reversed(closing):
            if drop:self.drop_depth=max(0,self.drop_depth-1)
            elif emit:self.parts.append(f'</{name}>')

    def handle_data(self,data):
        if not self.drop_depth:self.parts.append(html.escape(data,quote=False))

def looks_like_html(value: str) -> bool:
    return bool(re.search(r'<\s*/?\s*[A-Za-z][^>]*>',value))

def sanitize_html(value: str,base:Path,output:Path,preserve_anki_media:bool=False) -> str:
    parser=SafeHTML(base,output,preserve_anki_media)
    parser.feed(value);parser.close()
    for name,emit,drop in reversed(parser.stack):
        if emit:parser.parts.append(f'</{name}>')
    return ''.join(parser.parts)

def rewrite_urls(value, base: Path, output: Path, preserve_anki_media:bool=False):
    if isinstance(value,dict):
        for key,item in list(value.items()):
            if isinstance(item,str) and looks_like_html(item):
                value[key]=sanitize_html(item,base,output,preserve_anki_media)
            elif isinstance(item,str) and (key.endswith('url') or key in {'path','source_path','absolute_path','pdf_path','local_path','src','screenshot'}):
                value[key]=local_copy(item,base,output)
            elif key=='aliases' and isinstance(item,list):
                value[key]=[local_copy(alias,base,output) if isinstance(alias,str) else alias for alias in item]
            else: rewrite_urls(item,base,output,preserve_anki_media)
    elif isinstance(value,list):
        for index,item in enumerate(value):
            if isinstance(item,str) and looks_like_html(item):value[index]=sanitize_html(item,base,output,preserve_anki_media)
            else:rewrite_urls(item,base,output,preserve_anki_media)

def rewrite_anki_media(value):
    if isinstance(value,dict):
        for key,item in list(value.items()): value[key]=rewrite_anki_media(item)
        return value
    if isinstance(value,list): return [rewrite_anki_media(item) for item in value]
    if isinstance(value,str):
        def replace_media(match):
            filename=urllib.parse.unquote(match.group(1))
            return 'media/'+urllib.parse.quote(Path(filename).name,safe='-._~')
        return re.sub(r'(?:\.\./)?Anki/media/([^"\'<>\s]+)',replace_media,value)
    return value

def copy_anki_media(anki: dict, source_dirs: list[Path], destination: Path) -> int:
    refs=set()
    def scan(value):
        if isinstance(value,str): refs.update(re.findall(r'(?:\.\./Anki/media/|Anki/media/)([^"\'<>\s]+)',value))
        elif isinstance(value,dict):
            for child in value.values(): scan(child)
        elif isinstance(value,list):
            for child in value: scan(child)
    scan(anki)
    copied=0
    destination.mkdir(parents=True,exist_ok=True)
    for ref in refs:
        name=Path(urllib.parse.unquote(ref)).name
        target=destination/name
        if not name or target.exists():continue
        source=next((directory/name for directory in source_dirs if (directory/name).is_file()),None)
        if source:
            shutil.copy2(source,target);copied+=1
    return copied

def load_anki(path: Path|None) -> dict:
    if path is None: return {"notes":[],"cards":[],"scope":{},"graph":{}}
    data=assigned_json(path.read_text(errors='replace'),'window.ANKI_CONTEXT=')
    return data

def copy_runtime_modules(runtime: Path, source_root: Path=ROOT) -> None:
    """Stage generic search and local speech code without copying private indexes."""
    for name in ('semantic_search.py','reference_search.py','background_reference.py','prepare_reference_catalog.py'):
        source=source_root/name
        if source.is_file():shutil.copy2(source,runtime/name)
    tts_source=source_root/'StudyApp'
    tts_runtime=runtime/'StudyApp'
    for name in ('tts_service.py','tts_worker.py','install_tts.py','tts-requirements.lock'):
        source=tts_source/name
        if source.is_file():
            tts_runtime.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,tts_runtime/name)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--bundle',type=Path,default=PRIVATE/'personal-bundle.html',help='Private compiled app HTML with const DATA=…')
    ap.add_argument('--pack',type=Path,help='Private JSON source pack (alternative to --bundle)')
    ap.add_argument('--anki-context',type=Path,default=PRIVATE/'anki_context_data.js',help='Optional companion Anki JSON assignment')
    ap.add_argument('--media-dir',type=Path,help='Optional local Anki media directory (auto-detected beside the source bundle when omitted)')
    args=ap.parse_args()
    bundle=args.bundle if args.bundle.is_file() else None
    if args.pack:
        data,anki=load_data(None,args.pack)
        base=args.pack.parent
    else:
        data,anki=load_data(bundle,None)
        base=bundle.parent if bundle else PRIVATE
    if args.anki_context.is_file(): anki=load_anki(args.anki_context)
    else:
        anki_path=base/'anki_context_data.js'
        if anki_path.is_file(): anki=load_anki(anki_path)
    copied_media=0
    media_candidates=[args.media_dir] if args.media_dir else [PRIVATE/'media',base/'Anki'/'media',base.parent/'Anki'/'media']
    media_sources=[]
    for candidate in media_candidates:
        if candidate and candidate.is_dir() and candidate.resolve() not in {path.resolve() for path in media_sources}:
            media_sources.append(candidate)
    copied_media=copy_anki_media(anki,media_sources,RUNTIME/'media')
    rewrite_urls(anki,base,RUNTIME,preserve_anki_media=True)
    anki=rewrite_anki_media(anki)
    RUNTIME.mkdir(parents=True,exist_ok=True)
    rewrite_urls(data,base,RUNTIME)
    (RUNTIME/'anki_context_data.js').write_text('window.ANKI_CONTEXT='+json.dumps(anki,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')+';\n')
    (RUNTIME/'anki_topic_media.json').write_text(json.dumps(data.get('anki_topic_media',{'topics':{}}),ensure_ascii=False,indent=2)+'\n')
    (RUNTIME/'week8_references.json').write_text(json.dumps({'anki_topic_media':data.get('week8',{}).get('anki_topic_media',{})},ensure_ascii=False,indent=2)+'\n')
    catalog=data.get('source_catalog',{'documents':[]})
    (RUNTIME/'source_catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n')
    output=RUNTIME/'index.html'
    build_public.build(data,output)
    copy_runtime_modules(RUNTIME)
    shutil.copy2(ROOT/'requirements.txt',RUNTIME/'requirements.txt')
    print(f'Built private edition at {output}; it is ignored by Git. Copied {copied_media} referenced Anki media files.')

if __name__=='__main__': main()
