/* Reading annotations for the study guide host app.
   Host dependencies: state, save, $, esc, DATA, pages, showSidePanel,
   render, keywordify, openQuestions. Call initReadingTools() once. */
(function (root) {
  'use strict';

  const HIGHLIGHTS_KEY = 'readingHighlights';
  const QUESTION_ANCHORS_KEY = 'questionAnchors';
  const GENERATED_SELECTOR = '[data-reading-generated]';
  let initialized = false;
  let selectedHighlightId = '';
  let lastSelection = null;
  let questionPicker = null;
  let selectionTools = null;

  function annotations(key) {
    if (!Array.isArray(state[key])) state[key] = [];
    return state[key];
  }

  function normalizedText(s) { return String(s || '').replace(/\s+/g, ' ').trim(); }
  function makeId(prefix) {
    return prefix + '-' + (root.crypto && crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
  }
  function safeEsc(s) { return typeof esc === 'function' ? esc(s) : String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  function editableFor(node) {
    const el = node && (node.nodeType === 1 ? node : node.parentElement);
    return el && el.closest ? el.closest('.editable') : null;
  }

  function textMap(container) {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || parent.closest('script,style,noscript,template')) return NodeFilter.FILTER_REJECT;
        return node.nodeValue ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    const nodes = [];
    let text = '', node;
    while ((node = walker.nextNode())) {
      const start = text.length;
      text += node.nodeValue;
      nodes.push({ node, start, end: text.length });
    }
    return { text, nodes };
  }

  function pointOffset(map, node, offset, container) {
    const item = map.nodes.find(x => x.node === node);
    if (item) return item.start + Math.max(0, Math.min(offset, node.nodeValue.length));
    try { const range = document.createRange(); range.selectNodeContents(container); range.setEnd(node, offset); return range.toString().length; } catch { return null; }
  }

  function rangeFromSelection(editable) {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount || sel.isCollapsed) return null;
    const range = sel.getRangeAt(0);
    if (!editable.contains(range.startContainer) || !editable.contains(range.endContainer)) return null;
    const map = textMap(editable);
    const start = pointOffset(map, range.startContainer, range.startOffset, editable);
    const end = pointOffset(map, range.endContainer, range.endOffset, editable);
    if (start === null || end === null || end <= start) return null;
    const quote = map.text.slice(start, end);
    if (!normalizedText(quote)) return null;
    return { block_id: editable.dataset.edit, quote, start, end, context_before: map.text.slice(Math.max(0, start - 48), start), context_after: map.text.slice(end, end + 48) };
  }

  function currentSelection() {
    const sel = window.getSelection();
    const editable = sel && sel.rangeCount ? editableFor(sel.getRangeAt(0).commonAncestorContainer) : null;
    const selected = editable && rangeFromSelection(editable);
    if (selected) return selected;
    // Toolbar focus can collapse the DOM range; retain the user's last selection
    // only while its text is still present in a visible reading block.
    if (!lastSelection) return null;
    const block = [...document.querySelectorAll('.editable[data-edit]')].find(el => el.dataset.edit === lastSelection.block_id);
    return block && block.getClientRects().length && anchorOffset(lastSelection, textMap(block).text) ? lastSelection : null;
  }

  function excludedContext(target) {
    if (target && target.closest && target.closest('input,textarea,select,[contenteditable="true"],dialog[open]')) return true;
    if (document.querySelector('dialog[open]')) return true;
    const editable = target && target.closest ? target.closest('.editable') : null;
    return !!(editable && (editable.classList.contains('editing') || editable.getAttribute('contenteditable') === 'true'));
  }

  function rememberSelection() {
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount || sel.isCollapsed) return;
    const editable = editableFor(sel.getRangeAt(0).commonAncestorContainer);
    if (!editable || editable.classList.contains('editing') || editable.getAttribute('contenteditable') === 'true') return;
    const info = rangeFromSelection(editable);
    if (info) { lastSelection = info; showSelectionTools(sel.getRangeAt(0).getBoundingClientRect(),false); }
  }

  function anchorOffset(anchor, text) {
    let start = Number.isInteger(anchor.start) ? anchor.start : -1;
    let end = Number.isInteger(anchor.end) ? anchor.end : start + String(anchor.quote || '').length;
    const quote = String(anchor.quote || '');
    if (!quote) return null;
    if (start >= 0 && end <= text.length && text.slice(start, end) === quote) return { start, end };
    const candidates = [];
    let at = -1;
    while ((at = text.indexOf(quote, at + 1)) >= 0) candidates.push(at);
    if (!candidates.length) return null;
    const before = String(anchor.context_before || '');
    const after = String(anchor.context_after || '');
    candidates.sort((a, b) => {
      const score = x => {
        const pre = before ? text.slice(Math.max(0, x - before.length), x) : '';
        const post = after ? text.slice(x + quote.length, x + quote.length + after.length) : '';
        return Math.abs(x - Math.max(0, start)) + (before && pre !== before ? 2000 : 0) + (after && post !== after ? 2000 : 0);
      };
      return score(a) - score(b);
    });
    return { start: candidates[0], end: candidates[0] + quote.length };
  }

  function unwrapGenerated(container) {
    container.querySelectorAll(GENERATED_SELECTOR).forEach(span => {
      const parent = span.parentNode;
      while (span.firstChild) parent.insertBefore(span.firstChild, span);
      parent.removeChild(span);
    });
    container.normalize();
  }

  function decorateReading(blockId, html) {
    const box = document.createElement('div');
    box.innerHTML = html || '';
    unwrapGenerated(box);
    const map = textMap(box);
    const items = [];
    annotations(HIGHLIGHTS_KEY).filter(a => a.block_id === blockId).forEach(a => {
      const p = anchorOffset(a, map.text);
      if (p) items.push({ ...p, type: 'highlight', id: a.id });
    });
    annotations(QUESTION_ANCHORS_KEY).filter(a => a.block_id === blockId).forEach(a => {
      const p = anchorOffset(a, map.text);
      if (p) items.push({ ...p, type: 'question', ids: [a.question_id] });
    });
    (DATA.question_annotations || []).filter(a => a.block_id === blockId && (DATA.questions || []).some(q => q.id === a.question_id && /wrong/i.test(q.source_status || ''))).forEach(a => {
      const p = anchorOffset(a, map.text);
      if (p) items.push({ ...p, type: 'question', ids: [a.question_id] });
    });

    for (const entry of map.nodes.slice().reverse()) {
      const relevant = items.filter(a => a.start < entry.end && a.end > entry.start);
      if (!relevant.length) continue;
      const points = new Set([entry.start, entry.end]);
      relevant.forEach(a => { points.add(Math.max(entry.start, a.start)); points.add(Math.min(entry.end, a.end)); });
      const cuts = Array.from(points).sort((a, b) => a - b);
      const frag = document.createDocumentFragment();
      for (let i = 0; i < cuts.length - 1; i++) {
        const from = cuts[i], to = cuts[i + 1];
        if (to <= from) continue;
        const text = entry.node.nodeValue.slice(from - entry.start, to - entry.start);
        const active = relevant.filter(a => a.start <= from && a.end >= to);
        if (!active.length) { frag.append(document.createTextNode(text)); continue; }
        const qItems = active.filter(a => a.type === 'question');
        const qIds = Array.from(new Set(qItems.flatMap(a => a.ids || [a.id]).filter(Boolean)));
        const h = active.find(a => a.type === 'highlight');
        const span = document.createElement('span');
        span.dataset.readingGenerated = 'true';
        if (h) { span.classList.add('reading-highlight'); span.dataset.highlightId = h.id; }
        if (qIds.length) { span.classList.add('wrong-question-anchor'); span.dataset.wrongQuestion = qIds.join(','); span.tabIndex = 0; span.setAttribute('role', 'button'); span.setAttribute('aria-label', 'Open linked question' + (qIds.length > 1 ? 's ' : ' ') + qIds.join(', ')); }
        span.textContent = text;
        frag.append(span);
      }
      entry.node.replaceWith(frag);
    }
    return box.innerHTML;
  }

  function refreshReadingBlock(blockId) {
    document.querySelectorAll('.editable').forEach(el=>{if(el.dataset.edit===blockId)el.innerHTML=decorateReading(blockId,el.innerHTML)});
  }
  function findTopic(item) {
    if (state.guideHighlightBookTopics?.[item.id]) return state.guideHighlightBookTopics[item.id];
    if (item.topic) return item.topic;
    const keys=Object.keys(DATA.book_pages.keywords).sort((a,b)=>b.length-a.length);
    const quote=item.quote.toLowerCase();
    return keys.find(k=>quote.includes(k.toLowerCase())) || '';
  }
  function topicForSelection(picked) {
    const direct=findTopic(picked);if(direct)return direct;
    const sel=window.getSelection(),node=sel?.rangeCount?sel.getRangeAt(0).startContainer:null;
    const parent=node?.nodeType===1?node:node?.parentElement;
    const para=parent?.closest('p,td,li');
    const candidates=para?[...para.querySelectorAll('[data-keyword]')]:[];
    return candidates[0]?.dataset.keyword||'';
  }
  function showSelectionTools(rect,clicked=false,questionIds='') {
    if(!rect||(!rect.width&&!rect.height))return;
    if(!selectionTools){selectionTools=document.createElement('div');selectionTools.className='selection-tools';document.body.append(selectionTools);selectionTools.addEventListener('pointerdown',e=>e.preventDefault());}
    selectionTools.innerHTML=clicked?'<button data-selection-remove>Remove highlight</button><button data-selection-reference>Book references</button>':'<button data-selection-highlight>Highlight · H</button>';if(clicked&&questionIds)selectionTools.innerHTML+=`<button data-selection-question="${safeEsc(questionIds)}">Missed question</button>`;
    selectionTools.innerHTML+='<button data-selection-anki>Related cards</button>';
    selectionTools.dataset.forHighlight=clicked?'true':'false';
    selectionTools.hidden=false;
    selectionTools.style.left=Math.max(100,Math.min(window.innerWidth-selectionTools.offsetWidth-8,rect.left))+'px';
    selectionTools.style.top=Math.max(65,rect.top-38)+'px';
  }
  function openHighlight(item) {
    if(typeof root.openPassageSearch==='function'){root.openPassageSearch(item);return;}
    const topic=findTopic(item);
    if(topic){const node=[...document.querySelectorAll('[data-edit]')].find(el=>el.dataset.edit===item.block_id);if(node)setReferenceContext(topic,node);openHighlightedTopic(topic,item.id);return}
    showSidePanel('References for highlighted text',`<p>“${safeEsc(item.quote)}”</p><p>Choose its reference topic. This link will be retained with the highlight.</p><div class="reference-topic-picker">${Object.keys(DATA.book_pages.keywords).map(key=>`<button data-highlight-topic="${safeEsc(key)}" data-highlight-topic-id="${item.id}">${safeEsc(key)}</button>`).join('')}</div>`);
  }
  function toggleHighlight(preferClicked = false) {
    const picked = currentSelection();
    const list = annotations(HIGHLIGHTS_KEY);
    const clicked = preferClicked && selectedHighlightId ? list.find(a => a.id === selectedHighlightId) : null;
    if (!picked && !clicked) { $('#save-status').textContent='Select some reading text first'; return; }
    const existing = clicked || list.find(a => a.block_id === picked.block_id && a.start === picked.start && a.end === picked.end && a.quote === picked.quote);
    const blockId=existing?.block_id||picked.block_id;
    if (existing) { state[HIGHLIGHTS_KEY] = list.filter(a => a.id !== existing.id); selectedHighlightId = ''; }
    else { const item = { ...picked, id: makeId('highlight'), topic:topicForSelection(picked) }; list.push(item);selectedHighlightId = item.id; }
    save();refreshReadingBlock(blockId);lastSelection=null;window.getSelection()?.removeAllRanges();if(selectionTools)selectionTools.hidden=true;
    if(!existing&&!blockId.startsWith('book-text-')){const item=list.find(a=>a.id===selectedHighlightId);if(item&&typeof root.openPassageSearch==='function')root.openPassageSearch(item);}
  }

  function questionTitle(q) { return String(q.title || q.id || 'Question').replace(/^(AMBOSS|UWorld)\s*[-–]\s*/, ''); }
  function questionScreenshot(q) {
    return questionImageUrl(q);
  }
  function questionDetail(q, otherIds = []) {
    const image = questionScreenshot(q);
    const others = otherIds.length ? `<div class="reading-other-linked"><strong>Other linked questions</strong>${otherIds.map(id => `<button type="button" data-open-wrong-question="${safeEsc(id)}">${safeEsc(id)} · ${safeEsc(questionTitle((DATA.questions || []).find(item => item.id === id) || {id}))}</button>`).join('')}</div>` : '';
    return `<div class="reading-question-detail"><p class="reading-source-id">${safeEsc(q.id)} · ${safeEsc(q.source_status || q.source || '')}</p><h3>${safeEsc(questionTitle(q))}</h3><p><strong>Fact that triggered the miss</strong><br>${safeEsc(q.trigger || '')}</p><p><strong>Key fact</strong><br>${safeEsc(q.key_fact || '')}</p><p><strong>Trap</strong><br>${safeEsc(q.trap || '')}</p>${image ? `<details class="reading-screenshot"><summary>Show original screenshot</summary><button class="source-image-button" data-source-image="${image}" data-image-caption="${safeEsc(q.title || q.id)}"><img loading="lazy" src="${image}" alt="${safeEsc(q.title || q.id)} original screenshot"></button></details>` : ''}<button type="button" data-reading-review="${safeEsc(q.id)}">${state.questions && state.questions[q.id] && state.questions[q.id].review === 'reviewed' ? 'Reviewed' : 'Mark reviewed'}</button><button type="button" data-reading-review-later="${safeEsc(q.id)}">${state.questions && state.questions[q.id] && state.questions[q.id].review === 'later' ? 'Remove from review later' : 'Review later'}</button>${others}</div>`;
  }
  function openWrongQuestion(ids) {
    const list = Array.isArray(ids) ? ids : String(ids || '').split(',').filter(Boolean);
    const q = (DATA.questions || []).find(item => item.id === list[0]);
    if (q) showSidePanel(questionTitle(q), questionDetail(q, list.slice(1)));
  }

  function beginQuestionLink() {
    const picked = currentSelection();
    if (!picked) return;
    questionPicker = picked;
    renderQuestionPicker('');
  }
  function renderQuestionPicker(term) {
    const query = String(term || '').trim().toLowerCase();
    const qs = (DATA.questions || []).filter(q => /wrong/i.test(q.source_status || '') && (!query || [q.id, q.title, q.trigger, q.key_fact, q.trap].join(' ').toLowerCase().includes(query)));
    const html = `<div class="reading-question-picker"><p>Select the appendix question that was missed for “${safeEsc(questionPicker.quote)}”.</p><label>Search questions<input id="reading-question-search" type="search" value="${safeEsc(term || '')}" placeholder="Search title, fact, or ID"></label><div class="reading-question-options">${qs.map(q => `<button type="button" data-link-wrong-question="${safeEsc(q.id)}"><strong>${safeEsc(questionTitle(q))}</strong><small>${safeEsc(q.id)} · ${safeEsc(q.source_status || q.source || '')}</small><span>${safeEsc(q.trigger || '')}</span></button>`).join('') || '<p>No appendix questions match.</p>'}</div></div>`;
    showSidePanel('Link selection to a missed question', html);
    const input = document.querySelector('#reading-question-search');
    if (input) input.addEventListener('input', () => { const value = input.value, at = input.selectionStart; renderQuestionPicker(value); const next = document.querySelector('#reading-question-search'); next.focus(); next.setSelectionRange(at, at); });
  }

  function linkQuestion(questionId) {
    if (!questionPicker) return;
    const q = (DATA.questions || []).find(item => item.id === questionId);
    if (!q) return;
    const list = annotations(QUESTION_ANCHORS_KEY);
    const matches = list.find(a => a.block_id === questionPicker.block_id && a.start === questionPicker.start && a.end === questionPicker.end && a.quote === questionPicker.quote);
    if (matches) matches.question_id = questionId;
    else list.push({ ...questionPicker, question_id: questionId });
    questionPicker = null;
    save();
    if (typeof render === 'function') render();
    openWrongQuestion(questionId);
  }

  function keyHandler(e) {
    if ((e.key.toLowerCase() !== 'h' && e.code !== 'KeyH') || e.metaKey || (e.ctrlKey && !e.shiftKey)) return;
    if(e.repeat)return;
    const target = e.target;
    if (excludedContext(target)) return;
    if (!currentSelection()) return;
    e.preventDefault();e.stopImmediatePropagation();
    toggleHighlight();
  }

  function initReadingTools() {
    if (initialized) return;
    initialized = true;
    document.addEventListener('selectionchange', rememberSelection);
    document.addEventListener('pointerdown',e=>{if(!e.target.closest('.selection-tools,.reading-highlight')&&selectionTools)selectionTools.hidden=true});

    let lastHandledH=0;
    window.addEventListener('keydown', e=>{const before=e.defaultPrevented;keyHandler(e);if(!before&&e.defaultPrevented)lastHandledH=Date.now()}, true);
    window.addEventListener('keyup',e=>{if(Date.now()-lastHandledH<1200&&(e.key.toLowerCase()==='h'||e.code==='KeyH')){e.preventDefault();e.stopImmediatePropagation()}},true);

    document.addEventListener('keydown', e => {
      const anchor = e.target.closest && e.target.closest('[data-wrong-question]');
      if (anchor && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openWrongQuestion(anchor.dataset.wrongQuestion); }
    });
    document.addEventListener('click', e => {
      if(e.target.closest('[data-selection-highlight]')){toggleHighlight();return}
      if(e.target.closest('[data-selection-remove]')){toggleHighlight(true);return}
      if(e.target.closest('[data-selection-anki]')){const highlighted=annotations(HIGHLIGHTS_KEY).find(a=>a.id===selectedHighlightId);const picked=selectionTools?.dataset.forHighlight==='true'?highlighted:currentSelection();if(picked&&typeof root.openAnkiContext==='function'){const node=[...document.querySelectorAll('[data-edit]')].find(el=>el.dataset.edit===picked.block_id);root.openAnkiContext({kind:'selection',text:picked.quote,blockId:picked.block_id,node,title:'Cards for selected text'});if(selectionTools)selectionTools.hidden=true;}return}
      if(e.target.closest('[data-selection-reference]')){const item=annotations(HIGHLIGHTS_KEY).find(a=>a.id===selectedHighlightId);if(item)openHighlight(item);return}
      const chosen=e.target.closest('[data-highlight-topic]');if(chosen){const item=annotations(HIGHLIGHTS_KEY).find(a=>a.id===chosen.dataset.highlightTopicId);if(item){item.topic=chosen.dataset.highlightTopic;save();openHighlightedTopic(item.topic,item.id)}return}
      const quickQuestion=e.target.closest('[data-selection-question]');if(quickQuestion){openWrongQuestion(quickQuestion.dataset.selectionQuestion);return}
      const yellow = e.target.closest && e.target.closest('.reading-highlight');
      const wrong = e.target.closest && e.target.closest('[data-wrong-question]');
      if (wrong) { if(yellow){selectedHighlightId=yellow.dataset.highlightId||'';showSelectionTools(yellow.getBoundingClientRect(),true,wrong.dataset.wrongQuestion)}e.preventDefault(); openWrongQuestion(wrong.dataset.wrongQuestion); return; }
      if (yellow) { selectedHighlightId = yellow.dataset.highlightId || ''; const item=annotations(HIGHLIGHTS_KEY).find(a=>a.id===selectedHighlightId);showSelectionTools(yellow.getBoundingClientRect(),true,'');if(item&&!yellow.closest('.book-text-layer,.book-fallback-text'))openHighlight(item);return; }
      const otherQuestion = e.target.closest && e.target.closest('[data-open-wrong-question]');
      if (otherQuestion) { e.preventDefault(); openWrongQuestion(otherQuestion.dataset.openWrongQuestion); return; }
      if (e.target.closest && e.target.closest('#highlight-selection')) { e.preventDefault(); toggleHighlight(); return; }
      if (e.target.closest && e.target.closest('#link-question-selection')) { e.preventDefault(); beginQuestionLink(); return; }
      const link = e.target.closest && e.target.closest('[data-link-wrong-question]');
      if (link) { e.preventDefault(); linkQuestion(link.dataset.linkWrongQuestion); return; }
      const reviewed = e.target.closest && e.target.closest('[data-reading-review]');
      if (reviewed) { state.questions = state.questions || {}; state.questions[reviewed.dataset.readingReview] = state.questions[reviewed.dataset.readingReview] || {sections:[],tags:[],review:'not_queued',note:''}; state.questions[reviewed.dataset.readingReview].review = 'reviewed'; save(); if (typeof updateQuestionLinks === 'function') updateQuestionLinks(); openWrongQuestion(reviewed.dataset.readingReview); return; }
      const later = e.target.closest && e.target.closest('[data-reading-review-later]');
      if (later) { state.questions = state.questions || {}; state.questions[later.dataset.readingReviewLater] = state.questions[later.dataset.readingReviewLater] || {sections:[],tags:[],review:'not_queued',note:''}; const record = state.questions[later.dataset.readingReviewLater]; record.review = record.review === 'later' ? 'not_queued' : 'later'; save(); if (typeof updateQuestionLinks === 'function') updateQuestionLinks(); openWrongQuestion(later.dataset.readingReviewLater); }
    });
  }

  root.decorateReading = decorateReading;
  root.initReadingTools = initReadingTools;
  root.toggleReadingHighlight = toggleHighlight;
  root.beginReadingQuestionLink = beginQuestionLink;
})(window);
