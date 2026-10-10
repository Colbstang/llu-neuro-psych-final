/* Reusable medical-term recognition. Definitions are source text, not AI output. */
(function (scope) {
  'use strict';
  const punctuation = /[\s\-\u2010-\u2015]+/g;
  const letter = /[\p{L}\p{N}_]/u;
  function normalizedAlias(value) {
    return String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
      .replace(/[βϐ]/g, ' beta ').replace(/α/g, ' alpha ').replace(/γ/g, ' gamma ')
      .toLowerCase().replace(/[‘’]/g, "'").replace(punctuation, ' ').replace(/\s+/g, ' ').trim();
  }
  function escapeRegex(value) { return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
  function aliasPattern(alias) {
    return String(alias).trim().split(/[\s\-\u2010-\u2015]+/).map(part => escapeRegex(part).replace(/['‘’]/g, "['‘’]")).join('[\\s\\-\\u2010-\\u2015]+');
  }
  function compileLexicon(records) {
    const options = new Map(), labels = new Map();
    for (const record of records || []) {
      if (!record.id || !record.title) continue;
      for (const alias of [record.title, ...(record.aliases || [])]) {
        if (typeof alias !== 'string' || alias.trim().length < 3 || alias.length > 120) continue;
        const key = normalizedAlias(alias);
        if (!key) continue;
        if (!options.has(key)) options.set(key, new Set());
        options.get(key).add(record.id);
        if (!labels.has(key)) labels.set(key, new Set());
        labels.get(key).add(alias.trim());
      }
    }
    const lookup = new Map(), patterns = [];
    for (const [key, ids] of options) {
      // A shared abbreviation must not silently pick one diagnosis.
      if (ids.size !== 1) continue;
      lookup.set(key, [...ids][0]);
      for (const label of labels.get(key)) patterns.push(label);
    }
    patterns.sort((a, b) => b.length - a.length || a.localeCompare(b));
    return {lookup, regex: patterns.length ? new RegExp(patterns.map(aliasPattern).join('|'), 'giu') : null};
  }
  function findTerms(text, compiledOrRecords) {
    const compiled = Array.isArray(compiledOrRecords) ? compileLexicon(compiledOrRecords) : compiledOrRecords;
    if (!compiled || !compiled.regex || !text) return [];
    const regex = new RegExp(compiled.regex.source, compiled.regex.flags), matches = [];
    for (const match of String(text).matchAll(regex)) {
      const start = match.index, end = start + match[0].length;
      if ((start && letter.test(text[start - 1])) || (end < text.length && letter.test(text[end]))) continue;
      const id = compiled.lookup.get(normalizedAlias(match[0]));
      if (id) matches.push({id, start, end, text: match[0]});
    }
    return matches;
  }
  function safeLink(value, image = false) {
    try {
      const url = new URL(value);
      if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')) return '';
      const hosts = image ? ['upload.wikimedia.org', 'mdwiki.org', 'www.mdwiki.org']
        : ['mdwiki.org', 'www.mdwiki.org', 'en.wikipedia.org', 'commons.wikimedia.org', 'creativecommons.org'];
      return hosts.includes(url.hostname) ? url.href : '';
    } catch (_) { return ''; }
  }
  const STUB_DEFINITION = /^\s*(?:[^.\n]{1,100}\s+)?may refer to\s*:/i;
  function isUsableRecord(record) {
    return !!record && !STUB_DEFINITION.test(String(record.definition || record.summary || ''));
  }
  function createLookupService(options = {}) {
    const fetchImpl = options.fetch || (typeof fetch === 'function' ? fetch.bind(scope) : null);
    const endpoint = options.endpoint || '/api/term';
    const maxEntries = Math.max(1, Math.min(200, options.maxEntries || 100));
    const ttlMs = Math.max(1000, Math.min(30 * 60 * 1000, options.ttlMs || 5 * 60 * 1000));
    const timeoutMs = Math.max(1000, Math.min(45000, options.timeoutMs || 45000));
    const maxPrefetches = Math.max(1, Math.min(30, options.maxPrefetches || 12));
    const maxConcurrentPrefetch = Math.max(1, Math.min(3, options.maxConcurrentPrefetch || 2));
    const maxQueuedPrefetch = Math.max(1, Math.min(12, options.maxQueuedPrefetch || 6));
    const memo = new Map(), inflight = new Map(), queued = new Map();
    let activePrefetch = 0, startedPrefetch = 0;

    function cacheGet(key) {
      const entry = memo.get(key);
      if (!entry) return undefined;
      if (Date.now() - entry.at > ttlMs || (entry.result?.ok && !isUsableRecord(entry.result.record))) {
        memo.delete(key); return undefined;
      }
      memo.delete(key); memo.set(key, entry);
      return entry.result;
    }
    function cacheSet(key, result) {
      if (!result?.ok || !isUsableRecord(result.record)) return;
      memo.delete(key); memo.set(key, {at: Date.now(), result});
      while (memo.size > maxEntries) memo.delete(memo.keys().next().value);
    }
    function remember(record) {
      if (!record?.id || !isUsableRecord(record)) return;
      const result = {ok: true, record};
      cacheSet('id:' + record.id, result);
      for (const alias of [record.title, ...(record.aliases || [])]) {
        if (typeof alias === 'string' && alias.trim()) cacheSet('q:' + normalizedAlias(alias), result);
      }
    }
    function keyFor(query, isId) { return (isId ? 'id:' : 'q:') + (isId ? String(query) : normalizedAlias(query)); }
    function start(query, isId, background = false) {
      const key = keyFor(query, isId), existing = inflight.get(key);
      if (existing) {
        if (!background) existing.backgroundOnly = false;
        return existing;
      }
      if (!fetchImpl) return {promise: Promise.reject(new Error('Lookup is unavailable')), consumers: 0};
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      const requestKey = isId ? 'id' : 'q';
      const url = endpoint + '?' + new URLSearchParams({[requestKey]: String(query)});
      const entry = {controller, backgroundOnly: background, consumers: 0, promise: null};
      entry.promise = Promise.resolve().then(() => fetchImpl(url, {signal: controller.signal})).then(async response => {
        const result = await response.json();
        if (result?.ok && isUsableRecord(result.record)) {
          cacheSet(key, result);
          cacheSet('id:' + result.record.id, result);
          if (!isId) cacheSet('q:' + normalizedAlias(query), result);
          return result;
        }
        if (result?.ok && result.record && !isUsableRecord(result.record))
          return {ok:false,error:'ambiguous_term',candidates:result.candidates || []};
        return result;
      }).finally(() => {
        clearTimeout(timer);
        if (inflight.get(key) === entry) inflight.delete(key);
      });
      inflight.set(key, entry);
      return entry;
    }
    function consume(entry, signal) {
      entry.consumers++;
      let released = false;
      const release = () => {
        if (released) return;
        released = true;
        entry.consumers = Math.max(0, entry.consumers - 1);
      };
      if (!signal) return entry.promise.finally(release);
      if (signal.aborted) {
        release();
        if (!entry.consumers && !entry.backgroundOnly) entry.controller.abort();
        return Promise.reject(Object.assign(new Error('Lookup cancelled'), {name: 'AbortError'}));
      }
      return new Promise((resolve, reject) => {
        const abort = () => {
          release();
          if (!entry.consumers) entry.controller.abort();
          reject(Object.assign(new Error('Lookup cancelled'), {name: 'AbortError'}));
        };
        signal.addEventListener('abort', abort, {once: true});
        entry.promise.then(resolve, reject).finally(() => {
          signal.removeEventListener('abort', abort);
          release();
        });
      });
    }
    function lookup(query, isId = false, signal) {
      if (typeof query !== 'string' || !query.trim()) return Promise.resolve({ok:false,error:'invalid_query',candidates:[]});
      const key = keyFor(query, isId), cached = cacheGet(key);
      if (cached) return Promise.resolve(cached);
      return consume(start(query, isId, false), signal);
    }
    function drainPrefetch() {
      while (activePrefetch < maxConcurrentPrefetch && queued.size) {
        const [key, item] = queued.entries().next().value;
        queued.delete(key);
        if (cacheGet(key) || inflight.has(key) || startedPrefetch >= maxPrefetches) continue;
        startedPrefetch++;
        activePrefetch++;
        start(item.query, item.isId, true).promise.catch(() => {}).finally(() => {
          activePrefetch--;
          drainPrefetch();
        });
      }
      if (startedPrefetch >= maxPrefetches) queued.clear();
    }
    function prefetch(query, isId = true) {
      if (typeof query !== 'string' || !query.trim()) return false;
      const key = keyFor(query, isId);
      if (cacheGet(key) || inflight.has(key) || queued.has(key) || startedPrefetch >= maxPrefetches) return false;
      if (queued.size >= maxQueuedPrefetch) return false;
      queued.set(key, {query, isId});
      drainPrefetch();
      return true;
    }
    function cancelPrefetch(query, isId = true) {
      const key = keyFor(query, isId);
      queued.delete(key);
      const entry = inflight.get(key);
      if (entry?.backgroundOnly) entry.controller.abort();
    }
    return {lookup, prefetch, cancelPrefetch, remember, cacheSize: () => memo.size,
      prefetchState: () => ({active: activePrefetch, queued: queued.size, started: startedPrefetch})};
  }
  const lookupService = createLookupService();
  function createImageFigure(record, documentRef) {
    const doc = documentRef || (typeof document !== 'undefined' ? document : null);
    const image = record?.image, url = safeLink(image?.url, true);
    const fileUrl = safeLink(image?.file_url), licenseUrl = safeLink(image?.license_url);
    if (!doc || !url || !fileUrl || !licenseUrl || !image?.license) return null;
    const figure = doc.createElement('figure'); figure.className = 'definition-figure';
    const pixels = doc.createElement('img'); pixels.src = url; pixels.alt = image.alt || record.title || 'Medical illustration';
    pixels.loading = 'lazy'; pixels.decoding = 'async'; pixels.referrerPolicy = 'no-referrer';
    pixels.addEventListener('error', () => figure.remove(), {once: true}); figure.append(pixels);
    const caption = doc.createElement('figcaption');
    const artist = doc.createTextNode((image.artist || 'Image contributors') + ' · ');
    const license = doc.createElement('a'); license.href = licenseUrl; license.textContent = image.license;
    license.target = '_blank'; license.rel = 'noopener noreferrer';
    const separator = doc.createTextNode(' · ');
    const source = doc.createElement('a'); source.href = fileUrl; source.textContent = 'Image source';
    source.target = '_blank'; source.rel = 'noopener noreferrer';
    caption.append(artist, license, separator, source); figure.append(caption); return figure;
  }
  class TermScanner {
    constructor(root, records, options = {}) {
      this.root = root; this.compiled = compileLexicon(records); this.options = options; this.enabled = true;
      this.visibleTermIds = new Set();
      this.prefetchObserver = options.prefetch !== false && typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
        for (const entry of entries) {
          const id = entry.target.dataset.termId;
          if (!id) continue;
          if (entry.isIntersecting) { lookupService.prefetch(id, true); this.prefetchObserver.unobserve(entry.target); }
        }
      }, {rootMargin: '140px 0px', threshold: 0}) : null;
      this.onClick = event => { const node = event.target.closest?.('.med-term'); if (node && root.contains(node)) options.onOpen?.(node.dataset.termId, node); };
      this.onOver = event => { const node = event.target.closest?.('.med-term'); if (node && root.contains(node) && !node.contains(event.relatedTarget)) options.onHover?.(node.dataset.termId, node); };
      this.onFocus = event => { const node = event.target.closest?.('.med-term'); if (node) options.onHover?.(node.dataset.termId, node, true); };
      root.addEventListener('click', this.onClick); root.addEventListener('mouseover', this.onOver); root.addEventListener('focusin', this.onFocus);
      this.observer = new MutationObserver(() => { clearTimeout(this.timer); this.timer = setTimeout(() => this.scan(), 100); });
      this.scan();
    }
    setEnabled(enabled) { this.enabled = !!enabled; this.scan(); }
    scan() {
      this.observer.disconnect();
      this.prefetchObserver?.disconnect();
      for (const node of this.root.querySelectorAll('.med-term')) node.replaceWith(document.createTextNode(node.textContent));
      this.root.normalize();
      let total = 0;
      if (this.enabled) {
        const walker = document.createTreeWalker(this.root, NodeFilter.SHOW_TEXT, {
          acceptNode: node => node.parentElement?.closest('a,button,script,style,textarea,input,select,code,pre,[contenteditable="true"],[data-no-scan],h1,h2,h3,h4,[hidden]')
            ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
        });
        const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
        for (const node of nodes) {
          const matches = findTerms(node.nodeValue, this.compiled); if (!matches.length) continue;
          const fragment = document.createDocumentFragment(); let cursor = 0;
          for (const match of matches) {
            fragment.append(document.createTextNode(node.nodeValue.slice(cursor, match.start)));
            const button = document.createElement('button'); button.type = 'button'; button.className = 'med-term';
            button.dataset.termId = match.id; button.textContent = match.text; button.setAttribute('aria-label', 'Define ' + match.text);
            fragment.append(button); cursor = match.end; total++;
          }
          fragment.append(document.createTextNode(node.nodeValue.slice(cursor))); node.replaceWith(fragment);
        }
      }
      const termNodes = [...this.root.querySelectorAll('.med-term')];
      const currentIds = new Set(termNodes.map(node => node.dataset.termId).filter(Boolean));
      for (const id of this.visibleTermIds) if (!currentIds.has(id)) lookupService.cancelPrefetch(id, true);
      this.visibleTermIds = currentIds;
      if (this.prefetchObserver) for (const node of termNodes) this.prefetchObserver.observe(node);
      this.options.onTerms?.(termNodes);
      this.options.onScan?.(total, this.enabled);
      this.observer.observe(this.root, {childList: true, subtree: true, characterData: true});
      return total;
    }
    destroy() { this.observer.disconnect(); this.prefetchObserver?.disconnect(); for (const id of this.visibleTermIds) lookupService.cancelPrefetch(id, true); clearTimeout(this.timer); this.setEnabled(false); this.observer.disconnect(); this.root.removeEventListener('click', this.onClick); this.root.removeEventListener('mouseover', this.onOver); this.root.removeEventListener('focusin', this.onFocus); }
  }
  const api = {normalizedAlias, compileLexicon, findTerms, safeLink, createLookupService,
    lookup: lookupService.lookup, prefetch: lookupService.prefetch, cancelPrefetch: lookupService.cancelPrefetch,
    createImageFigure, TermScanner, attach: (root, records, options) => new TermScanner(root, records, options)};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  scope.MedicalTermCards = api;
  if (typeof document === 'undefined') return;

  const byId = id => document.getElementById(id);
  const reading = byId('reading'); if (!reading || !byId('lookup-form') || !byId('auto-terms')) return;
  const panel = byId('definition'), hover = byId('term-hover');
  const STORE = 'term-cards-pilot-v1';
  const samples = {
    renal: {title: 'Nephrotic and nephritic patterns', parts: [
      ['p', 'Nephrotic syndrome brings proteinuria, hypoalbuminemia, edema, and hyperlipidemia together. The glomerulus is the filtration structure to understand first.'],
      ['h3', 'Start with the pattern'],
      ['p', 'Minimal change disease, focal segmental glomerulosclerosis, and membranous nephropathy are causes of nephrotic syndrome. Compare the syndrome before comparing the individual diseases.'],
      ['p', 'Nephritic syndrome emphasizes inflammation and hematuria. Glomerulonephritis can impair kidney filtration; IgA nephropathy and Goodpasture syndrome are useful terms to unpack.'],
      ['key', 'Use the term cards to clarify the vocabulary, then return to the disease comparison.']
    ], sources: ['Nephrotic_syndrome', 'Nephritic_syndrome']},
    immunology: {title: 'The immune vocabulary behind a mechanism', parts: [
      ['p', 'The complement system helps antibodies and phagocytes clear microbes. Opsonization makes a target easier for a phagocyte to recognize and ingest.'],
      ['h3', 'Connect the parts'],
      ['p', 'The innate immune system provides early defenses, including neutrophils and macrophages. The adaptive immune system includes B cells, T cells, and antibodies.'],
      ['p', 'A cytokine is a signaling molecule. Interferon, interleukin, and tumor necrosis factor are related terms worth distinguishing before learning a drug mechanism.'],
      ['key', 'A short definition should explain the word without replacing the surrounding study guide.']
    ], sources: ['Complement_system', 'Innate_immune_system', 'Adaptive_immune_system', 'Cytokine']},
    neuro: {title: 'Connect the word to the neurological process', parts: [
      ['p', 'Multiple sclerosis involves demyelination in the central nervous system. Oligodendrocytes form central nervous system myelin. Interferon beta-1a is a drug used in multiple sclerosis.'],
      ['h3', 'Keep the location clear'],
      ['p', 'Guillain–Barré syndrome affects the peripheral nervous system. Myasthenia gravis concerns transmission at the neuromuscular junction.'],
      ['p', 'Delirium and dementia describe different clinical syndromes. Parkinson’s disease, Huntington’s disease, and epilepsy have their own mechanisms and patterns.'],
      ['key', 'Open a definition, read the context, and keep your place in the text.']
    ], sources: ['Multiple_sclerosis', 'Oligodendrocyte', 'Interferon_beta-1a', 'Guillain%E2%80%93Barr%C3%A9_syndrome', 'Myasthenia_gravis']}
  };
  let lexicon = [], scanner, selected = '', hoverTimer, closeTimer, hoverController = null, panelController = null, hoverSerial = 0, panelSerial = 0, history = [], historyAt = -1, staticMode = false;
  let preference = true; try { preference = JSON.parse(localStorage.getItem(STORE) || '{}').automatic !== false; } catch (_) {}
  byId('auto-terms').checked = preference;
  function el(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
  function link(text, url, image = false) { const node = el('a', text); node.href = safeLink(url, image) || '#'; node.target = '_blank'; node.rel = 'noopener noreferrer'; return node; }
  function closeHover() { clearTimeout(hoverTimer); clearTimeout(closeTimer); hoverSerial++; hoverController?.abort(); hoverController = null; hover.hidden = true; }
  function renderSample(name) {
    closeHover(); selected = ''; byId('define-selection').disabled = true;
    document.querySelectorAll('[data-sample]').forEach(node => node.setAttribute('aria-pressed', String(node.dataset.sample === name)));
    byId('custom-editor').hidden = name !== 'custom';
    reading.replaceChildren();
    if (name === 'custom') {
      const text = byId('reading-input').value.trim();
      reading.append(el('h2', 'Your reading'));
      for (const part of (text || 'Paste a paragraph above, then choose Read this text.').split(/\n\s*\n/)) reading.append(el('p', part));
    } else {
      const sample = samples[name]; reading.append(el('h2', sample.title));
      for (const [kind, text] of sample.parts) reading.append(el(kind === 'key' ? 'p' : kind, text, kind === 'key' ? 'key-line' : ''));
      const sources = el('div', 'Reading sample · Sources: ', 'reading-source'); sources.dataset.noScan = '';
      sample.sources.forEach((title, i) => { if (i) sources.append(document.createTextNode(' · ')); sources.append(link(decodeURIComponent(title).replaceAll('_', ' '), 'https://mdwiki.org/wiki/' + title)); }); reading.append(sources);
    }
    scanner?.scan();
  }
  async function lookup(query, isId = false, signal) {
    if (staticMode && !isId) {
      const hits = lexicon.filter(record => [record.title, ...(record.aliases || [])].some(label => normalizedAlias(label) === normalizedAlias(query)));
      if (hits.length === 1) return lookupService.lookup(hits[0].id, true, signal);
      if (hits.length > 1) return {ok:false, error:'ambiguous_term', candidates:hits.map(record => ({title:record.title}))};
      return {ok:false, error:'source_unavailable', candidates:[]};
    }
    return lookupService.lookup(query, isId, signal);
  }
  function attribution(record) {
    const source = record.source || {}, node = el('div', undefined, 'attribution');
    node.append(document.createTextNode('Text: '), link(source.name || 'MDWiki', source.url), document.createTextNode(source.original_url ? ' / Wikipedia contributors · ' : ' contributors · '), link(source.license || 'Source license', source.license_url));
    if (safeLink(source.history_url)) node.append(document.createTextNode(' · '), link('History', source.history_url));
    if (safeLink(source.original_url)) node.append(document.createTextNode(' · '), link('Original article', source.original_url));
    if (safeLink(source.original_history_url)) node.append(document.createTextNode(' · '), link('Original contributors', source.original_history_url));
    node.append(el('div', source.changes || 'Lead excerpt; shortened for display.'));
    if (source.fetched_at) node.append(el('div', 'Retrieved ' + String(source.fetched_at).slice(0, 10)));
    return node;
  }
  function syncHistory() { byId('term-back').disabled = historyAt <= 0; byId('term-forward').disabled = historyAt >= history.length - 1; }
  function setCurrent(record) { reading.querySelectorAll('.med-term').forEach(node => node.classList.toggle('is-current', node.dataset.termId === record.id)); }
  function renderRecord(record) {
    panel.replaceChildren();
    const source = el('div', undefined, 'definition-source'); source.append(el('span', undefined, 'source-dot'), el('span', record.source?.name || 'MDWiki')); panel.append(source, el('h2', record.title));
    const definition = record.definition || record.summary || ''; panel.append(el('p', definition, 'definition-lead'));
    const figure = createImageFigure(record, document); if (figure) panel.append(figure);
    const summary = record.summary || '', remainder = summary.startsWith(definition) ? summary.slice(definition.length).trim() : summary;
    if (remainder && remainder !== definition) { const details = el('details', undefined, 'more-background'); details.append(el('summary', 'More background')); const body = el('div', undefined, 'definition-body'); remainder.split(/\n\s*\n|\n/).filter(Boolean).forEach(p => body.append(el('p', p))); details.append(body); panel.append(details); }
    const actions = el('div', undefined, 'source-actions'); actions.append(link('Read full article ↗', record.source?.url)); if (safeLink(record.source?.revision_url)) actions.append(link('This source revision ↗', record.source.revision_url)); panel.append(actions);
    const related = [...new Set(findTerms(summary, scanner?.compiled).map(match => match.id))].filter(id => id !== record.id).slice(0, 5);
    if (related.length) { panel.append(el('p', 'Related terms', 'related-label')); const nodes = el('div', undefined, 'related-terms'); for (const id of related) { const entry = lexicon.find(row => row.id === id), button = el('button', entry?.title || id); button.type = 'button'; button.onclick = () => openTerm(id, true); nodes.append(button); } panel.append(nodes); }
    panel.append(attribution(record)); setCurrent(record);
  }
  async function openTerm(query, isId = false, navigate = false) {
    const serial = ++panelSerial; panelController?.abort(); panelController = new AbortController(); closeHover(); panel.replaceChildren(el('p', 'Looking up ' + (lexicon.find(row => row.id === query)?.title || query) + '…', 'selection-hint'));
    try {
      const result = await lookup(query, isId, panelController.signal); if (serial !== panelSerial) return;
      if (!result.ok) { renderMiss(query, result); return; }
      const record = result.record;
      if (!lexicon.some(row => row.id === record.id)) { lexicon.push({id:record.id, title:record.title, aliases:record.aliases || [], source:record.source}); if (scanner) { scanner.compiled = compileLexicon(lexicon); scanner.scan(); } }
      renderRecord(record);
      if (!navigate && history[historyAt] !== record.id) { history = history.slice(0, historyAt + 1); history.push(record.id); historyAt = history.length - 1; } syncHistory();
      const pane = document.querySelector('.definition-pane'); pane.scrollTop = 0;
      if (matchMedia('(max-width:700px)').matches) pane.scrollIntoView({behavior: 'smooth', block: 'start'});
    } catch (_) { if (serial === panelSerial) panel.replaceChildren(el('h2', 'Lookup unavailable'), el('p', 'The local lookup service could not respond. Cached definitions remain available; try another known term.', 'lookup-error')); }
  }
  function renderMiss(query, result) {
    if (result.error === 'content_unavailable' || result.error === 'source_unavailable') {
      panel.replaceChildren(el('h2', result.error === 'content_unavailable' ? 'Open the source article' : 'Source unavailable'));
      panel.append(el('p', result.error === 'content_unavailable' ? 'MDWiki has a matching article, but its introductory text could not be retrieved.' : 'The local definitions still work. A new lookup needs the MDWiki service to respond.', 'lookup-error'));
      for (const candidate of result.candidates || []) if (safeLink(candidate.url)) panel.append(link('Read ' + candidate.title + ' ↗', candidate.url));
      return;
    }
    panel.replaceChildren(el('h2', 'Choose the specific term'));
    const message = result.error === 'invalid_query' ? 'Select a short medical term, up to 160 characters.' : 'No exact definition matched “' + query + '”.';
    panel.append(el('p', message, 'lookup-error'));
    if (result.candidates?.length) { const choices = el('div', undefined, 'related-terms'); for (const candidate of result.candidates) { const button = el('button', candidate.title); button.type = 'button'; button.onclick = () => openTerm(candidate.title); choices.append(button); } panel.append(choices); }
    else panel.append(el('p', 'Try the full name or select a more specific phrase. This pilot recognizes its local vocabulary; typed lookups can request another MDWiki article.', 'selection-hint'));
  }
  function placeHover(anchor) {
    const rect = anchor.getBoundingClientRect(), card = hover.getBoundingClientRect();
    hover.style.left = Math.max(12, Math.min(rect.left, innerWidth - card.width - 12)) + 'px';
    hover.style.top = (rect.bottom + card.height + 16 <= innerHeight ? rect.bottom + 9 : Math.max(12, rect.top - card.height - 9)) + 'px';
  }
  function scheduleHover(id, node, immediate = false) {
    clearTimeout(hoverTimer); clearTimeout(closeTimer); hoverController?.abort(); hoverController = new AbortController(); const controller = hoverController, serial = ++hoverSerial;
    hoverTimer = setTimeout(async () => {
      if (!node.isConnected) return;
      try {
        const result = await lookup(id, true, controller.signal); if (serial !== hoverSerial || !result.ok) return;
        hover.replaceChildren(el('h3', result.record.title), el('p', result.record.definition || result.record.summary));
        const figure = createImageFigure(result.record, document);
        if (figure) { figure.classList.add('term-hover-figure'); hover.append(figure); }
        const foot = el('div', undefined, 'hover-footer'); foot.append(el('span', 'MDWiki · ' + (result.record.source?.license || '')));
        const button = el('button', 'Keep beside reading →'); button.type = 'button'; button.onclick = () => openTerm(id, true); foot.append(button); hover.append(foot); hover.hidden = false; placeHover(node);
      } catch (_) { /* Hover stays unobtrusive when the source is unavailable. */ }
    }, immediate ? 0 : 350);
  }
  reading.addEventListener('mouseout', event => { if (event.target.closest?.('.med-term') && !hover.contains(event.relatedTarget)) { clearTimeout(hoverTimer); closeTimer = setTimeout(closeHover, 220); } });
  hover.addEventListener('mouseenter', () => clearTimeout(closeTimer)); hover.addEventListener('mouseleave', () => { closeTimer = setTimeout(closeHover, 160); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeHover(); });
  addEventListener('scroll', closeHover, true); addEventListener('resize', closeHover);
  document.addEventListener('selectionchange', () => {
    const selection = getSelection(), range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    if (range && reading.contains(range.commonAncestorContainer)) { selected = selection.toString().trim(); byId('define-selection').disabled = !selected || selected.length > 160; }
    else { selected = ''; byId('define-selection').disabled = true; }
  });
  byId('define-selection').addEventListener('pointerdown', event => event.preventDefault());
  byId('define-selection').onclick = () => { if (selected && selected.length <= 160) openTerm(selected); };
  byId('lookup-form').onsubmit = event => { event.preventDefault(); const query = byId('term-query').value.trim(); if (query) openTerm(query); };
  byId('term-back').onclick = () => { if (historyAt > 0) { historyAt--; syncHistory(); openTerm(history[historyAt], true, true); } };
  byId('term-forward').onclick = () => { if (historyAt < history.length - 1) { historyAt++; syncHistory(); openTerm(history[historyAt], true, true); } };
  document.querySelectorAll('[data-sample]').forEach(button => button.onclick = () => renderSample(button.dataset.sample));
  byId('scan-text').onclick = () => renderSample('custom');
  byId('auto-terms').onchange = event => { preference = event.target.checked; closeHover(); scanner?.setEnabled(preference); try { localStorage.setItem(STORE, JSON.stringify({automatic: preference})); } catch (_) {} };
  renderSample('renal');
  async function boot() {
    scope.MedicalSelectionLookup?.attach(reading, query => openTerm(query));
    try {
      let result;
      try { const response = await fetch('/api/terms'); if (!response.ok) throw new Error('No local service'); result = await response.json(); if (!result.ok || !Array.isArray(result.records)) throw new Error('No lexicon'); }
      catch (_) { const response = await fetch('data/terms.json'); const pack = await response.json(); result = {records: pack.records}; staticMode = true; for (const record of pack.records || []) lookupService.remember(record); }
      lexicon = result.records || [];
      scanner = new TermScanner(reading, lexicon, {prefetch: !staticMode, onOpen: (id) => openTerm(id, true), onHover: scheduleHover, onScan: (count, enabled) => { byId('scanner-status').textContent = enabled ? count + ' terms recognized · ' + lexicon.length + ' local definitions' : 'Automatic definitions off · Manual lookup available'; }});
      scanner.setEnabled(preference);
      for (const title of ['Nephrotic syndrome', 'Complement system', 'Multiple sclerosis']) { const record = lexicon.find(row => row.title.toLowerCase() === title.toLowerCase()); if (record) { const button = el('button', record.title); button.type = 'button'; button.onclick = () => openTerm(record.id, true); byId('suggested-terms').append(button); } }
    } catch (_) { byId('scanner-status').textContent = 'Medical vocabulary unavailable'; }
  }
  boot();
})(typeof window !== 'undefined' ? window : this);
