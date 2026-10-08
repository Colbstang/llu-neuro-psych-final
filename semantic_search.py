"""Local, read-only semantic retrieval over the explicitly exported Anki scope.

The model is downloaded once from its official repository. Queries and cards
never leave this computer. This does not open or modify the Anki collection.
"""
from pathlib import Path
import argparse, hashlib, html, json, platform, re, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parent
MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
REVISION = '1110a243fdf4706b3f48f1d95db1a4f5529b4d41'
ONNX_FILE = 'onnx/model_qint8_arm64.onnx' if platform.machine().lower() in {'arm64','aarch64'} else 'onnx/model.onnx'
MODEL_ROOT = ROOT / 'assets/models/minilm'
INDEX_ROOT = ROOT / 'semantic_index'
SCHEMA = 2
RERANK_MODEL = 'cross-encoder/ms-marco-MiniLM-L6-v2'
RERANK_REVISION = '233902d25c440f23af6f7d6e94d2946bac0bee0a'

def library():
    source = (ROOT / 'anki_context_data.js').read_text()
    prefix = 'window.ANKI_CONTEXT='
    if not source.startswith(prefix):
        raise ValueError('Unexpected exported card-library format')
    return json.loads(source[len(prefix):].rstrip().removesuffix(';'))

def plaintext(value):
    value = html.unescape(re.sub(r'<[^>]+>', ' ', str(value or '')))
    value = re.sub(r'\{\{c\d+::', '', value, flags=re.I)
    value = value.replace('}}', '').replace('::', ' ')
    return re.sub(r'\s+', ' ', value).strip()

def card_text(note, card):
    front = plaintext(note.get('html') or note.get('plain'))
    target = int(card.get('ord', 0)) + 1
    answers = re.findall(r'\{\{c' + str(target) + r'::(.*?)(?:\}\})', note.get('html', ''), re.I | re.S)
    fact = ' '.join(plaintext(a.split('::')[0]) for a in answers)
    extra = plaintext(note.get('extraHtml'))
    # Front and tested cloze dominate; very long reference tables do not drown
    # the card's specific fact. Original images remain available through IDs.
    text = front[:1600]
    if fact:
        text += ' Tested fact: ' + fact[:500]
    if extra:
        text += ' Explanation: ' + extra[:600]
    if len(front) < 30:
        tags = [plaintext(t.split('::')[-1].replace('_', ' ')) for t in note.get('courseTags', [])]
        text += ' Topic: ' + ' '.join(tags)[-300:]
    return text.strip() or 'Original image-occlusion card'

def ensure_model():
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    for remote, local in [(ONNX_FILE, 'model.onnx'), ('tokenizer.json', 'tokenizer.json')]:
        path = MODEL_ROOT / local
        if path.is_file() and path.stat().st_size > 1000:
            continue
        url = f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{remote}'
        request = urllib.request.Request(url, headers={'User-Agent': 'LLU-local-study-guide/1.0'})
        temporary = path.with_suffix(path.suffix + '.partial')
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(path)
    manifest = {'model': MODEL, 'revision': REVISION, 'dimensions': 384, 'max_tokens': 256,
                'pooling': 'attention-mask mean pooling, L2 normalization',
                'source': f'https://huggingface.co/{MODEL}',
                'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in MODEL_ROOT.iterdir() if p.suffix in ['.onnx', '.json'] and p.name != 'manifest.json'}}
    (MODEL_ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2))

class Encoder:
    def __init__(self):
        ensure_model()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 3
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(MODEL_ROOT / 'model.onnx'), sess_options=options, providers=['CPUExecutionProvider'])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(MODEL_ROOT / 'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding(pad_id=0, pad_token='[PAD]')
        self.lock = Lock()

    def encode(self, texts):
        with self.lock:
            tokens = self.tokenizer.encode_batch(texts)
            mask = np.asarray([t.attention_mask for t in tokens], dtype=np.int64)
            feed = {'input_ids': np.asarray([t.ids for t in tokens], dtype=np.int64), 'attention_mask': mask,
                    'token_type_ids': np.asarray([t.type_ids for t in tokens], dtype=np.int64)}
            output = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
            if output.ndim == 3:
                weighted = output * mask[..., None]
                vectors = weighted.sum(axis=1) / np.maximum(mask.sum(axis=1)[:, None], 1)
            else:
                vectors = output
            vectors = vectors.astype(np.float32)
            vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
            return vectors

class Reranker:
    """Read each retrieved card together with the exact highlighted sentence."""
    def __init__(self):
        root=ROOT/'assets/models/reranker';root.mkdir(parents=True,exist_ok=True)
        for remote,local in [(ONNX_FILE,'model.onnx'),('tokenizer.json','tokenizer.json')]:
            path=root/local
            if path.exists() and path.stat().st_size>1000:continue
            request=urllib.request.Request(f'https://huggingface.co/{RERANK_MODEL}/resolve/{RERANK_REVISION}/{remote}',headers={'User-Agent':'LLU-local-study-guide/1.0'})
            temporary=path.with_suffix(path.suffix+'.partial')
            with urllib.request.urlopen(request,timeout=120) as response,temporary.open('wb') as output:
                while chunk:=response.read(1024*1024):output.write(chunk)
            temporary.replace(path)
        (root/'manifest.json').write_text(json.dumps({'model':RERANK_MODEL,'revision':RERANK_REVISION,'source':f'https://huggingface.co/{RERANK_MODEL}',
            'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir() if p.name in ['model.onnx','tokenizer.json']}},indent=2))
        options=ort.SessionOptions();options.intra_op_num_threads=3;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(root/'model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        self.inputs={i.name for i in self.session.get_inputs()}
        self.tokenizer=Tokenizer.from_file(str(root/'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=384);self.tokenizer.enable_padding(pad_id=0,pad_token='[PAD]')
        self.lock=Lock()
    def score(self,query,texts):
        scores=[]
        with self.lock:
            for at in range(0,len(texts),24):
                encoded=self.tokenizer.encode_batch([(query,t) for t in texts[at:at+24]])
                feed={'input_ids':np.asarray([t.ids for t in encoded],dtype=np.int64),
                      'attention_mask':np.asarray([t.attention_mask for t in encoded],dtype=np.int64),
                      'token_type_ids':np.asarray([t.type_ids for t in encoded],dtype=np.int64)}
                scores.extend(self.session.run(None,{k:v for k,v in feed.items() if k in self.inputs})[0].reshape(-1).tolist())
        return scores

def prepare(encoder):
    data = library()
    notes = {int(n['id']): n for n in data['notes']}
    records = [{'cardId': int(c['id']), 'noteId': int(c['note']), 'kind': notes[int(c['note'])]['kind'],
                'text': card_text(notes[int(c['note'])], c)} for c in data['cards']]
    digest = hashlib.sha256(json.dumps({'schema': SCHEMA, 'revision': REVISION, 'records': records}, sort_keys=True).encode()).hexdigest()
    INDEX_ROOT.mkdir(exist_ok=True)
    metadata_path, vectors_path = INDEX_ROOT / 'metadata.json', INDEX_ROOT / 'vectors.npz'
    if metadata_path.exists() and vectors_path.exists():
        metadata = json.loads(metadata_path.read_text())
        if metadata.get('fingerprint') == digest:
            return metadata, np.load(vectors_path)['vectors']
    if not records:
        metadata = {'schema': SCHEMA, 'model': MODEL, 'revision': REVISION, 'fingerprint': digest,
                    'dimensions': 384, 'card_count': 0, 'note_count': len(notes),
                    'scope': data.get('scope', {}), 'cards': [], 'build_seconds': 0}
        vectors = np.empty((0, 384), dtype=np.float32)
        np.savez_compressed(vectors_path, vectors=vectors)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False))
        return metadata, vectors
    # Encode unique texts once, then keep a vector for every original card ID.
    unique = list(dict.fromkeys(r['text'] for r in records))
    vectors = []
    start = time.time()
    for at in range(0, len(unique), 48):
        vectors.append(encoder.encode(unique[at:at+48]))
        if at % 480 == 0:
            print(f'Indexing allowed cards: {min(at+48,len(unique))}/{len(unique)} texts', flush=True)
    encoded = np.concatenate(vectors)
    positions = {text: i for i, text in enumerate(unique)}
    card_vectors = encoded[[positions[r['text']] for r in records]]
    metadata = {'schema': SCHEMA, 'model': MODEL, 'revision': REVISION, 'fingerprint': digest,
                'dimensions': int(card_vectors.shape[1]), 'card_count': len(records), 'note_count': len(notes),
                'scope': data['scope'], 'cards': records, 'build_seconds': round(time.time()-start, 2)}
    np.savez_compressed(vectors_path, vectors=card_vectors)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False))
    print(f'Ready: {len(records)} allowed cards, {len(notes)} notes, {card_vectors.shape[1]} dimensions', flush=True)
    return metadata, card_vectors

STOP = set('a an the and or of to in on for with from is are be this that which has have patient patients finding findings'.split())
def terms(text):
    return {w for w in re.findall(r'[a-z0-9]+', text.lower()) if len(w)>2 and w not in STOP}

def normalize_query(text):
    # Preserve quantitative and clinical discriminators before encoding.
    text=re.sub(r'\bthree\s+(?:per second|hertz|hz)\b','3 Hz',text,flags=re.I)
    text=re.sub(r'\bbleeding\b','hemorrhage (bleeding)',text,flags=re.I)
    return text

class Search:
    def __init__(self):
        self.encoder = Encoder()
        self.metadata, self.vectors = prepare(self.encoder)
        self.records = self.metadata['cards']
        self.tokens = [terms(r['text']) for r in self.records]
        self.reranker = Reranker()
        exported=library()
        self.fronts={int(n['id']):plaintext(n.get('html') or n.get('plain')) for n in exported['notes']}
        topic_path=ROOT/'anki_topic_media.json'
        topics=json.loads(topic_path.read_text()).get('topics',{}) if topic_path.is_file() else {}
        week_path=ROOT/'week8_references.json'
        if week_path.is_file():topics.update(json.loads(week_path.read_text()).get('anki_topic_media',{}))
        self.topic_patterns=[]
        for topic,config in topics.items():
            patterns=[]
            for pattern in config.get('patterns',[]):
                try:patterns.append(re.compile(pattern,re.I))
                except re.error:pass
            if patterns:self.topic_patterns.append((topic,patterns))

    def query(self, text, kind='all', limit=8):
        raw_text=text;text=normalize_query(text)
        vector = self.encoder.encode([text])[0]
        cosines = self.vectors @ vector
        query_tokens = terms(text)
        # Small exact-term boost protects clinical discriminators; similarity
        # is still determined principally by the actual sentence embeddings.
        boosts = np.asarray([.045 * len(query_tokens & t) / max(1, len(query_tokens)) for t in self.tokens])
        ranking = cosines + boosts
        allowed = [i for i, r in enumerate(self.records) if kind=='all' or r['kind']==kind]
        named=[(topic,patterns) for topic,patterns in self.topic_patterns if any(p.search(raw_text) for p in patterns)]
        if named:
            focused=[i for i in allowed if any(p.search(self.fronts[self.records[i]['noteId']]) for _,patterns in named for p in patterns)]
            if focused:allowed=focused
        # A hemorrhage query must not be redirected to a CSF-leak card merely
        # because both describe sudden headache and cerebrospinal fluid.
        if re.search(r'\b(?:bleeding|hemorrhag\w*)\b',raw_text,re.I):
            focused=[i for i in allowed if re.search(r'hemorrhag|bleed|hematoma|xanthochrom',self.records[i]['text'],re.I)]
            if focused:allowed=focused
        ordered = sorted(allowed, key=lambda i: float(ranking[i]), reverse=True)
        if not ordered:
            return []
        candidates=ordered[:96]
        reranked=self.reranker.score(text,[self.records[i]['text'] for i in candidates])
        rerank_scores=dict(zip(candidates,reranked))
        ordered=sorted(candidates,key=lambda i:rerank_scores[i]+.15*float(ranking[i]),reverse=True)
        ceiling = rerank_scores[ordered[0]]
        floor = max(-5., ceiling-4.5)
        results = []
        seen_notes=set()
        for i in ordered:
            if rerank_scores[i] < floor or self.records[i]['noteId'] in seen_notes:
                continue
            record = self.records[i]
            seen_notes.add(record['noteId'])
            results.append({'cardId': record['cardId'], 'noteId': record['noteId'], 'kind': record['kind'],
                            'similarity': round(float(cosines[i]), 4), 'score': round(rerank_scores[i], 4),
                            'reason': 'Sentence match, checked against the card’s specific fact'})
            if len(results) >= min(12, max(1, int(limit))):
                break
        return results

def serve(search, port):
    from reference_search import ReferenceSearch, ReferenceIndexNotReady
    references = ReferenceSearch(ROOT, search.encoder, search.reranker)
    allowed_origins = {'null', f'http://127.0.0.1:{port}', f'http://localhost:{port}',
                       'http://127.0.0.1:8767', 'http://localhost:8767'}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # No query or card text is logged.
        def allowed(self):
            return self.headers.get('Origin') in allowed_origins or not self.headers.get('Origin')
        def reply(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            origin = self.headers.get('Origin')
            if origin in allowed_origins:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_OPTIONS(self):
            self.reply(200 if self.allowed() else 403, {})
        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {'error':'Origin is not permitted'})
            if self.path != '/health':
                return self.reply(404, {'error':'Unknown endpoint'})
            self.reply(200, {'ready': True, 'version':3, 'reference_documents':len(references.documents), 'books_ready':references.vectors is not None, 'model': MODEL, 'revision': REVISION,
                             'card_count': len(search.records), 'dimensions': 384, 'local_only': True})
        def do_POST(self):
            if not self.allowed():
                return self.reply(403, {'error':'Origin is not permitted'})
            if self.path not in ['/search', '/source-page']:
                return self.reply(404, {'error':'Unknown endpoint'})
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384:
                return self.reply(413, {'error':'Query is too large'})
            try:
                request = json.loads(self.rfile.read(length))
                if self.path == '/source-page':
                    page = request.get('page')
                    if not isinstance(page, int) or isinstance(page, bool):
                        return self.reply(400, {'error':'Physical PDF page must be an integer'})
                    return self.reply(200, references.source_page(str(request.get('documentId','')), page, str(request.get('query',''))[:4000]))
                text = str(request.get('query', '')).strip()
                if not text or len(text)>4000:
                    return self.reply(400, {'error':'Select a phrase or sentence, up to 4000 characters'})
                kind = request.get('kind', 'all')
                if kind not in ['all', 'Ty', 'AnKing', 'NPS']:
                    return self.reply(400, {'error':'Unknown card scope'})
                results = search.query(text, kind, request.get('limit', 8))
                books = references.search_books(text, 3) if request.get('include_books') else None
                self.reply(200, {'matches': results, 'books': books, 'method': 'local-sentence-embeddings', 'reranker': RERANK_MODEL,
                                 'scope_count':len(search.records), 'model': MODEL, 'local_only': True})
            except ReferenceIndexNotReady as error:
                self.reply(503, {'error':str(error)})
            except (ValueError, TypeError, json.JSONDecodeError):
                self.reply(400, {'error':'Invalid search request'})
    print(f'Local semantic card search ready at 127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--query')
    parser.add_argument('--port', type=int, default=8768)
    args = parser.parse_args()
    search = Search()
    if args.query:
        print(json.dumps(search.query(args.query), indent=2))
    elif not args.prepare:
        serve(search, args.port)
