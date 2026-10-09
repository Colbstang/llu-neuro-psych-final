// Dedicated learning-objective library. The host app owns view navigation and
// calls mountObjectivesPage(container) whenever the Objectives view is shown.
state.objectives ||= {};
let objectiveView = 'all';
let objectiveSearch = '';
let objectiveSection = '';
let objectiveTopic = '';
let objectiveQuiz = '';

function objectiveRecord(id) {
  const record = state.objectives[id] || {};
  // Preserve the earlier review values while presenting the requested
  // Good / Bad / Unrated model.
  const review = record.review === 'reviewed' ? 'good'
    : record.review === 'later' ? 'bad'
    : ['good', 'bad'].includes(record.review) ? record.review : 'unrated';
  return { ...record, review, notes: record.notes || '' };
}

function objectiveWrite(id) {
  const current = objectiveRecord(id);
  state.objectives[id] = { ...state.objectives[id], review: current.review, notes: current.notes };
  return state.objectives[id];
}

function objectiveSource(o) {
  const link = (label, url, page, path = o.source_path) => {
    const href = url || (path ? `file://${encodeURI(path)}` : '');
    const fragment = page ? `#page=${page}` : '';
    const name = `${label}${page ? ` · PDF p. ${page}` : ''}`;
    return href ? `<a href="${esc(href + fragment)}" target="_blank" rel="noopener">${esc(name)}</a>` : `<span>${esc(name)}</span>`;
  };
  const primary = link(o.lecture, o.source_url, o.source_page);
  const duplicates = (o.duplicates || []).map(d => link(`${d.lecture}${d.week ? ` · Week ${d.week}` : ''}`, null, d.source_page, d.source_path));
  return [primary, ...duplicates].join(' · ') + (o.source_kind?.includes('transcription') ? ' · Canvas transcription' : '');
}

function objectiveAnswerSources(answer) {
  const seen = new Set();
  const sources = [...(answer?.sources || []), ...(answer?.figures || []).filter(f => f.url).map(f => ({ url: f.url, page: f.page, label: f.source_label || 'Lecture image' }))];
  return sources.filter(s => {
    const key = `${String(s.url || '').split('#')[0]}#page=${s.page || ''}`;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  }).map(s => {
    const label = String(s.label || 'Lecture source').replace(/(?:[,·]\s*)?PDF\s*p(?:age)?\.?\s*\d+\s*$/i, '').replace(/\.pdf\s*$/i, '').trim();
    const url = String(s.url || '').split('#')[0];
    return `<a href="${esc(url)}${s.page ? '#page=' + s.page : ''}" target="_blank" rel="noopener">${esc(label || 'Lecture source')}${s.page ? ' · p. ' + s.page : ''}</a>`;
  }).join(' · ') + (answer?.external_sources || []).map(s => ` · <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a>`).join('');
}

function objectiveCard(o) {
  const r = objectiveRecord(o.id), answer = DATA.objective_answers[o.id];
  const tags = [...new Set(answer?.quiz_tags || o.quiz_tags || [])].filter(t => t !== objectiveQuiz && !(week8Scope() && !objectiveQuiz));
  const images = objectiveFiguresMarkup(o,answer);
  const answerMarkup = answer
    ? `<details class="objective-answer" data-lo-reveal="${esc(o.id)}" ${state.open['lo:' + o.id] !== false ? 'open' : ''}>
        <summary>Answer</summary>
        <div class="objective-answer-content"><div class="editable objective-answer-text" data-edit="lo-answer-${esc(o.id)}" contenteditable="false">${typeof decorateReading === 'function' ? decorateReading('lo-answer-' + o.id, keywordify(answer.html)) : answer.html}</div>
        ${images}</div>
      </details>`
    : '<p class="objective-later-chapter">Answer not drafted yet.</p>';
  return `<article class="objective-card" data-objective-card="${esc(o.id)}">
    <div class="objective-card-heading"><p class="objective-text">${esc(o.text)}</p></div>
    <div class="objective-controls">
    <div class="objective-rating" role="group" aria-label="Rate objective">
      <button type="button" data-objective-review="${esc(o.id)}" data-value="good" aria-pressed="${r.review === 'good'}">Good</button>
      <button type="button" data-objective-review="${esc(o.id)}" data-value="bad" aria-pressed="${r.review === 'bad'}">Bad</button>
      ${r.review === 'unrated' ? '<span class="objective-status-label">Unrated</span>' : ''}
    </div>
    ${tags.length ? `<div class="objective-quiz-tags">${tags.map(t=>`<button data-objective-quiz-tag="${esc(t)}">${esc(t)}</button>`).join('')}</div>` : ''}
    <details class="objective-sources" data-lo-sources="${esc(o.id)}" ${state.open['lo-sources:' + o.id] ? 'open' : ''}><summary>Sources</summary><div class="objective-source-detail"><p><strong>Objective:</strong> ${objectiveSource(o)}</p>${answer ? `<p><strong>Answer:</strong> ${objectiveAnswerSources(answer)}</p>` : ''}${answer?.quiz_basis ? `<p class="objective-scope-basis">${esc(answer.quiz_basis)}</p>` : ''}</div></details>
    </div>
    ${answerMarkup}
    <details class="objective-notes" data-lo-notes="${esc(o.id)}" ${state.open['lo-notes:' + o.id] ? 'open' : ''}><summary>${r.notes ? 'Notes · saved' : 'Notes'}</summary>
    <label class="objective-note-label" for="objective-note-${esc(o.id)}">My notes</label>
    <textarea id="objective-note-${esc(o.id)}" class="objective-note" data-objective-note="${esc(o.id)}" rows="2" placeholder="Notes for this objective…">${esc(r.notes)}</textarea>
    </details>
  </article>`;
}

function objectiveMarkup(sectionId) {
  const os = DATA.objectives.filter(o => o.sections?.includes(sectionId));
  if (!os.length) return '';
  return `<div class="inline-objectives-link"><button type="button" data-objective-section="${esc(sectionId)}">${os.length} learning objective${os.length === 1 ? '' : 's'} · Review →</button></div>`;
}

function objectivePageMarkup() {
  return `<main class="objective-page" aria-labelledby="objectives-title">
    <header class="objective-page-header"><div><p class="objective-kicker">COURSE OBJECTIVES · VERBATIM</p><h1 id="objectives-title">Learning objectives</h1>
      <p class="objective-coverage-note">Verbatim objectives, written answers, relevant source images, and saved review notes.</p></div>
      <label class="objective-search-label">Search all objectives<input id="objective-search" type="search" value="${esc(objectiveSearch)}" placeholder="Objective, lecture, source…"></label>
    </header>
    <div class="objective-page-toolbar"><div class="objective-filters" role="group" aria-label="Filter objectives">
      ${['all', 'good', 'bad', 'unrated'].map(v => `<button type="button" data-objective-filter="${v}" aria-pressed="${objectiveView === v}">${v[0].toUpperCase() + v.slice(1)}</button>`).join('')}
    </div><label class="objective-quiz-filter">Quiz<select id="objective-quiz"><option value="">All quizzes</option>${[...new Set(scopeObjectives().flatMap(o=>DATA.objective_answers[o.id]?.quiz_tags||o.quiz_tags||[]))].sort().map(q=>`<option value="${esc(q)}" ${objectiveQuiz===q?'selected':''}>${esc(q)}</option>`).join('')}</select></label><p id="objective-count" class="objective-count" aria-live="polite"></p></div>
    <div id="objective-list" class="objective-list"></div>
  </main>`;
}

function renderObjectiveList(container) {
  const list = container.querySelector('#objective-list');
  if (!list) return;
  const term = objectiveSearch.trim().toLowerCase();
  const os = scopeObjectives().filter(o => {
    const r = objectiveRecord(o.id);
    return (objectiveView === 'all' || r.review === objectiveView)
      && (!objectiveSection || o.sections?.includes(objectiveSection))
      && (!objectiveTopic || studyDashboardGraph().get(objectiveTopic)?.blocks.some(block=>o.sections?.includes(block.id)))
      && (!objectiveQuiz || (DATA.objective_answers[o.id]?.quiz_tags||o.quiz_tags||[]).includes(objectiveQuiz))
      && (!term || [o.text, o.lecture, o.source_path].join(' ').toLowerCase().includes(term));
  });
  container.querySelector('#objective-count').textContent = `${os.length} / ${scopeObjectives().length} objectives`;
  list.innerHTML = os.length ? os.map(objectiveCard).join('') : '<p class="objective-empty">No objectives match this filter.</p>';
}

function renderObjectivesPage(container) {
  container.innerHTML = objectivePageMarkup();
  renderObjectiveList(container);
  return container;
}

function mountObjectivesPage(container, options = {}) {
  objectiveSection = options.section || '';
  objectiveTopic = options.topic || '';
  renderObjectivesPage(container);
  if(objectiveTopic){const note=document.createElement('p');note.className='study-dashboard-sync';note.textContent='Topic: '+(studyDashboardGraph().get(objectiveTopic)?.title||objectiveTopic);container.querySelector('.objective-page-header').after(note)}
  const onInput = e => {
    if (e.target.id === 'objective-search') { objectiveSearch = e.target.value; renderObjectiveList(container); }
    if (e.target.matches('[data-objective-note]')) {
      const id = e.target.dataset.objectiveNote;
      objectiveWrite(id).notes = e.target.value;
      const summary = e.target.closest('.objective-notes')?.querySelector('summary');
      if (summary) summary.textContent = e.target.value ? 'Notes · saved' : 'Notes';
      save();
    }
  };
  const onClick = e => {
    const quizTag = e.target.closest('[data-objective-quiz-tag]');
    if (quizTag) { objectiveQuiz = quizTag.dataset.objectiveQuizTag; renderObjectivesPage(container); return; }
    const image = e.target.closest('[data-lo-image]');
    if (image) { const id=image.dataset.objectiveId;openImageGallery({key:'lo:'+id,title:'Objective source images',images:DATA.objective_answers[id].figures,index:Number(image.dataset.imageIndex)});return; }
    const filter = e.target.closest('[data-objective-filter]');
    if (filter) { objectiveView = filter.dataset.objectiveFilter; renderObjectivesPage(container); return; }
    const rating = e.target.closest('[data-objective-review]');
    if (rating) {
      const id = rating.dataset.objectiveReview, value = rating.dataset.value;
      objectiveWrite(id).review = objectiveRecord(id).review === value ? 'unrated' : value;
      save(); renderObjectiveList(container);
    }
  };
  const onToggle = e => {
    if (e.target.matches('details[data-lo-reveal]') && container.contains(e.target)) {
      state.open['lo:' + e.target.dataset.loReveal] = e.target.open; save();
    }
    if (e.target.matches('details[data-lo-notes]') && container.contains(e.target)) {
      state.open['lo-notes:' + e.target.dataset.loNotes] = e.target.open; save();
    }
    if (e.target.matches('details[data-lo-sources]') && container.contains(e.target)) {
      state.open['lo-sources:' + e.target.dataset.loSources] = e.target.open; save();
    }
  };
  container.addEventListener('input', onInput);
  const onChange = e => { if(e.target.id==='objective-quiz'){objectiveQuiz=e.target.value;renderObjectiveList(container)} };
  container.addEventListener('change', onChange);
  container.addEventListener('click', onClick);
  container.addEventListener('toggle', onToggle, true);
  return { render: () => renderObjectivesPage(container), destroy() {
    container.removeEventListener('input', onInput);
    container.removeEventListener('change', onChange);
    container.removeEventListener('click', onClick);
    container.removeEventListener('toggle', onToggle, true);
  } };
}

// Classic-script exports used by guide_app.js and the dedicated view shell.
window.objectiveMarkup = objectiveMarkup;
window.objectivePageMarkup = objectivePageMarkup;
window.renderObjectivesPage = renderObjectivesPage;
window.mountObjectivesPage = mountObjectivesPage;
