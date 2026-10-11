/* Focused, bounded drug and organism characteristic rounds for the course app. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.StepCoursePractice = api;
  if (root && root.document && typeof DATA !== 'undefined') api.install(root);
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const CHOICE_LIMIT = 8;
  const ROUND_SIZES = [5, 10, 20];

  function key(value) {
    return String(value).normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
  }

  function unique(values) {
    const found = new Set();
    return (values || []).filter(value => {
      const normalized = key(value);
      if (!normalized || found.has(normalized)) return false;
      found.add(normalized);
      return true;
    });
  }

  function traitsFor(kind, record, category) {
    const values = unique(record?.traits?.[category] || []);
    if (kind === 'bugs' && category === 'Gram / stain') {
      return values.filter(value => !['notabacterialgramclassification', 'notapplicablevirus', 'notapplicableparasite'].includes(key(value)));
    }
    return values;
  }

  function matchesFilter(record, category, value) {
    return !category || !value || traitsFor('', record, category).some(item => key(item) === key(value));
  }

  function filterCases(cases, category, value) {
    return (cases || []).filter(record => matchesFilter(record, category, value));
  }

  function boundedChoices(kind, category, answer, candidates, limit = CHOICE_LIMIT, random = Math.random) {
    const valid = traitsFor(kind, answer, category);
    const pool = unique((candidates || []).flatMap(record => traitsFor(kind, record, category)));
    const distractors = pool.filter(value => !valid.some(correct => key(correct) === key(value)));
    for (let i = distractors.length - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      [distractors[i], distractors[j]] = [distractors[j], distractors[i]];
    }
    return shuffle([...valid, ...distractors.slice(0, Math.max(0, limit - valid.length))], random);
  }

  function gradeSelections(kind, record, categories, selections) {
    const missed = [], wrong = [], details = [];
    let correct = 0, extra = 0, total = 0;
    for (const category of categories || []) {
      const expected = traitsFor(kind, record, category);
      if (!expected.length) continue;
      total++;
      const picked = unique(selections?.[category] || []);
      const missing = expected.filter(answer => !picked.some(value => key(answer) === key(value)));
      const invalid = picked.filter(value => !expected.some(answer => key(answer) === key(value)));
      const complete = !missing.length && !invalid.length;
      if (complete) correct++;
      if (missing.length) missed.push(category);
      if (invalid.length) {
        extra += invalid.length;
        wrong.push(...invalid.map(value => `${category}: ${value}`));
      }
      details.push({category, expected, selected: picked, missing, wrong: invalid, correct: complete});
    }
    return {correct, total, extra, missed, wrong, details, perfect: total > 0 && correct === total && !extra};
  }

  function seededRandom(seed) {
    let number = 2166136261;
    for (const character of String(seed)) number = Math.imul(number ^ character.charCodeAt(0), 16777619);
    return () => { number = Math.imul(number ^ (number >>> 15), 1 | number); number ^= number + Math.imul(number ^ (number >>> 7), 61 | number); return ((number ^ (number >>> 14)) >>> 0) / 4294967296; };
  }

  function choicePages(choices, limit = CHOICE_LIMIT) {
    const output = [];
    for (let at = 0; at < choices.length; at += limit) output.push(choices.slice(at, at + limit));
    return output;
  }

  function shuffle(items, random = Math.random) {
    const output = [...items];
    for (let i = output.length - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      [output[i], output[j]] = [output[j], output[i]];
    }
    return output;
  }

  function roundCases(cases, size, random = Math.random) {
    const shuffled = shuffle(cases || [], random);
    const count = size === 'all' ? shuffled.length : Math.min(shuffled.length, Math.max(1, Number(size) || 5));
    return shuffled.slice(0, count);
  }

  function removeLegacyTraitControls(html, kind) {
    const pattern = new RegExp(`<div class="practice-modes">\\s*<button data-trait-start="${kind}">[\\s\\S]*?<\\/div><div id="trait-practice" hidden><\\/div>`);
    return html.replace(pattern, '');
  }

  function isCanonicalOrganismMatch(query, record, normalizer = value => String(value || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()) {
    if (!record?.title || typeof query !== 'string') return false;
    const wanted = normalizer(query);
    return [record.title, ...(record.aliases || [])].some(value => typeof value === 'string' && normalizer(value) === wanted);
  }

  async function lookupOrganismFigure(api, organismName, documentRef) {
    const result = await api.lookup(organismName, false);
    if (!result?.ok || !result.record) {
      return {status: result?.error === 'ambiguous_term' ? 'ambiguous' : 'unavailable', candidates: result?.candidates || []};
    }
    const normalizer = typeof api.normalizedAlias === 'function' ? api.normalizedAlias : undefined;
    if (!isCanonicalOrganismMatch(organismName, result.record, normalizer)) return {status: 'unrelated'};
    const figure = api.createImageFigure(result.record, documentRef);
    return figure ? {status: 'ready', figure, title: result.record.title} : {status: 'no-image', title: result.record.title};
  }

  function install(root) {
    const escText = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    const sessions = new Map(), snapshots = new Map(), requests = new Map(), catalogs = new Map();
    const kinds = ['drugs', 'bugs', 'pathology'];
    const oldComparisonMarkup = comparisonMarkup;

    function sheetFor(kind) { return DATA.comparison_sheets?.find(sheet => sheet.id === kind); }
    function dataFor(kind) {
      if (catalogs.has(kind)) return catalogs.get(kind);
      const original = DATA.review_games?.[kind] || {}, sheet = sheetFor(kind);
      const categories = [...(original.categories || [])];
      const sourceCategories = (sheet?.columns || []).slice(1).map(column => `Source detail · ${column}`);
      for (const category of sourceCategories) if (!categories.includes(category)) categories.push(category);
      const byRow = new Map((original.cases || []).map(record => [record.row_index, record]));
      const plain = value => { const node = root.document.createElement('div'); node.innerHTML = String(value || ''); return node.textContent.replace(/\s+/g, ' ').trim(); };
      const missing = value => !key(value) || ['notspecified', 'notspecifiedinlecture', 'notapplicable', 'none', 'na'].includes(key(value));
      const cases = (sheet?.rows || []).map((row, index) => {
        const record = byRow.get(index) || {};
        const traits = Object.fromEntries(categories.map(category => [category, traitsFor(kind, record, category).filter(value => !missing(value))]));
        sourceCategories.forEach((category, offset) => { const value = plain(row.cells[offset + 1]); traits[category] = missing(value) ? [] : [value]; });
        return {...record, id: record.id || `${kind}-${index}`, name: record.name || plain(row.cells[0]), row_index: index, traits, source_categories: sourceCategories};
      });
      return {kind, categories, cases};
    }
    function scoped(kind) { return (dataFor(kind)?.cases || []).filter(record => scopeRow(kind, record.row_index)); }
    function subsetFor(kind, category, value) { return filterCases(scoped(kind), category, value); }
    function selectedCategories(setup, kind) { return [...setup.querySelectorAll(`[data-course-game-category="${kind}"]:checked`)].map(input => input.value); }
    function progressMarkup(kind) {
      const summary = snapshots.get(kind)?.summary;
      return summary ? `<span>${summary.attempts} checked entries</span><span>${summary.complete} complete</span><span>${summary.due} due for this app</span><span>${summary.unseen} with unseen characteristics</span>` : '<span>Loading saved quiz progress…</span>';
    }
    function configMarkup(kind) {
      const config = dataFor(kind), categories = config?.categories || [];
      const title = {bugs:'Organism', drugs:'Drug', pathology:'Pathology'}[kind];
      const defaultFilter = categories[0] || '';
      const filterValues = unique(scoped(kind).flatMap(record => traitsFor(kind, record, defaultFilter))).sort((a, b) => a.localeCompare(b));
      return `<section class="course-game-setup course-game-${kind}" data-course-game-setup="${kind}">
        <div class="course-game-intro"><p class="eyebrow">${title.toUpperCase()} · COMPLETE CHARACTERISTICS</p><p>Recall every sourced characteristic, one category at a time. Select all supported answers. Complete source details include facts that have not been split into shorter traits.</p></div>
        <div class="course-game-progress" data-course-game-progress="${kind}" aria-live="polite">${progressMarkup(kind)}</div>
        <div class="course-game-settings">
          <label>Subset category<select data-course-game-filter-category="${kind}"><option value="">All ${title.toLowerCase()} entries</option>${categories.map(category => `<option value="${escText(category)}" ${category === defaultFilter ? 'selected' : ''}>${escText(category)}</option>`).join('')}</select></label>
          <label>Subset value<select data-course-game-filter-value="${kind}"><option value="">All values</option>${filterValues.map(value => `<option value="${escText(value)}">${escText(value)}</option>`).join('')}</select></label>
          <label>Round size<select data-course-game-size="${kind}"><option value="5">5 entries</option><option value="10">10 entries</option><option value="20">20 entries</option><option value="all">All in subset</option></select></label>
          <label>Review pool<select data-course-game-pool="${kind}"><option value="all">All in subset</option><option value="due">Due entries</option><option value="unseen">Unseen characteristics</option><option value="incomplete">Incomplete entries</option></select></label>
        </div>
        <details class="course-game-focus-options"><summary>Focus on selected characteristics · all selected by default</summary><fieldset class="course-game-focus"><legend>Practice characteristics</legend>${categories.map(category => `<label><input type="checkbox" data-course-game-category="${kind}" value="${escText(category)}" checked> ${escText(category)}</label>`).join('')}</fieldset></details>
        <p class="course-game-count" data-course-game-count="${kind}" aria-live="polite">${scoped(kind).length} entries in this subset.</p>
        <div class="course-game-setup-actions"><button type="button" data-course-game-start="${kind}" disabled>Start round</button><button type="button" data-course-game-resume="${kind}" hidden>Resume saved round</button></div>
        <p class="course-game-save-state" data-course-game-save-state="${kind}" role="status"></p>
        <div class="course-game-round" data-course-game-round="${kind}" hidden></div>
      </section>`;
    }
    comparisonMarkup = function (sheetId = null) {
      let html = oldComparisonMarkup(sheetId);
      if (!kinds.includes(sheetId)) return html;
      html = removeLegacyTraitControls(html, sheetId);
      if (sheetId === 'pathology') html = html.replace(/<div class="practice-modes"[^>]*>[\s\S]*?<\/div><div id="path-practice" hidden><\/div>/, markup => `<details class="course-game-source-modes"><summary>Image, reverse recall & drawing practice</summary><p>These source comparison modes keep their saved drawings and self-ratings. Use the complete-characteristics round below for category review dates.</p>${markup}</details>`);
      const controls = `<div class="practice-modes"><button type="button" data-shuffle-sheet="${sheetId}">Shuffle table</button></div>${configMarkup(sheetId)}`;
      root.queueMicrotask(() => refresh(sheetId));
      return html.replace('<label class="sheet-search">', `${controls}<label class="sheet-search">`);
    };

    function status(kind, message) { const host = root.document.querySelector(`[data-course-game-save-state="${kind}"]`); if (host) host.textContent = message; }
    function currentPool(kind, setup) {
      const category = setup.querySelector(`[data-course-game-filter-category="${kind}"]`).value;
      const value = setup.querySelector(`[data-course-game-filter-value="${kind}"]`).value;
      const mode = setup.querySelector(`[data-course-game-pool="${kind}"]`).value;
      const items = new Map((snapshots.get(kind)?.items || []).map(item => [item.id, item]));
      return subsetFor(kind, category, value).filter(record => {
        const item = items.get(record.id);
        return mode === 'all' || (mode === 'due' && item?.due_categories.length) || (mode === 'unseen' && item?.unseen_categories.length) || (mode === 'incomplete' && !item?.complete);
      });
    }
    function updateSetup(kind, changeCategory = false) {
      const setup = root.document.querySelector(`[data-course-game-setup="${kind}"]`);
      if (!setup) return;
      if (changeCategory) {
        const category = setup.querySelector(`[data-course-game-filter-category="${kind}"]`).value;
        const valueSelect = setup.querySelector(`[data-course-game-filter-value="${kind}"]`);
        const values = category ? unique(scoped(kind).flatMap(record => traitsFor(kind, record, category))).sort((a,b) => a.localeCompare(b)) : [];
        valueSelect.replaceChildren(new Option('All values', ''));
        for (const value of values) valueSelect.add(new Option(value, value));
        valueSelect.disabled = !category;
      }
      const cases = currentPool(kind, setup), saved = snapshots.get(kind)?.session;
      setup.querySelector(`[data-course-game-count="${kind}"]`).textContent = `${cases.length} entries in this subset. App quiz history is separate from Anki.`;
      setup.querySelector(`[data-course-game-start="${kind}"]`).disabled = !snapshots.has(kind) || requests.has(kind) || !cases.length || !selectedCategories(setup, kind).length;
      const resume = setup.querySelector(`[data-course-game-resume="${kind}"]`);
      resume.hidden = !saved || saved.complete;
      resume.disabled = requests.has(kind) || saved?.source_changed;
      resume.textContent = saved?.source_changed ? 'Source changed · start a new round' : 'Resume saved round';
      setup.querySelector(`[data-course-game-progress="${kind}"]`).innerHTML = progressMarkup(kind);
    }
    function accept(kind, result) {
      snapshots.set(kind, result); if (result.catalog) catalogs.set(kind, result.catalog);
      const saved = result.session, prior = sessions.get(kind), existing = prior?.id === saved?.id ? prior : null;
      if (saved) sessions.set(kind, {...saved, pool: scoped(kind), round: saved.round_ids.map(id => dataFor(kind).cases.find(record => record.id === id)).filter(Boolean), options: existing?.options || new Map(), images: existing?.images || new Map(), choice_page: existing?.choice_page || 0});
      updateSetup(kind);
    }
    async function refresh(kind) {
      if (!root.document.querySelector(`[data-course-game-setup="${kind}"]`) || requests.has(kind)) return;
      requests.set(kind, true); updateSetup(kind);
      try { accept(kind, await studyAppRequest(`/quizzes?kind=${kind}`)); status(kind, 'Saved quiz history loaded.'); }
      catch (error) { status(kind, `Quiz history is unavailable: ${error.message}`); }
      finally { requests.delete(kind); updateSetup(kind); const session = sessions.get(kind); if (session && !session.paused && !session.complete && !session.source_changed) roundMarkup(kind); }
    }
    async function act(kind, action, extra = {}) {
      if (requests.has(kind)) return false;
      const session = sessions.get(kind);
      requests.set(kind, true); updateSetup(kind); roundMarkup(kind); status(kind, 'Saving quiz…');
      try {
        const result = await studyAppRequest('/quizzes', {kind, action, session_id: session?.id, revision: session?.revision, ...extra});
        accept(kind, result); status(kind, 'Saved to this Mac.'); return true;
      } catch (error) { status(kind, `Not saved: ${error.message} Reload this round to retry.`); return false; }
      finally { requests.delete(kind); updateSetup(kind); roundMarkup(kind); }
    }
    function choicesFor(session, kind, record, category) {
      const choiceKey = `${record.id}:${category}`;
      if (!session.options.has(choiceKey)) session.options.set(choiceKey, boundedChoices(kind, category, record, session.pool, category.startsWith('Source detail · ') ? 4 : CHOICE_LIMIT, seededRandom(`${session.id}:${choiceKey}`)));
      return session.options.get(choiceKey);
    }
    function sourceFigureMarkup(kind, record, session) {
      if (kind !== 'bugs') return '';
      const figures = (record.figures || []).filter(figure => figure && typeof figure.src === 'string' &&
        (/^\/api\/course\/asset\/[A-Za-z0-9_-]+$/.test(figure.src) || /^data:image\/(?:png|jpe?g|gif|webp);base64,/i.test(figure.src)) &&
        (figure.source_url || figure.source_book || figure.source_page) &&
        (figure.license || figure.license_url || figure.source_book));
      const local = figures.length ? `<div class="course-game-figures">${figures.map(figure => {
        const sourceUrl = /^https?:\/\//i.test(figure.source_url || '') ? figure.source_url : '';
        const page = Number(figure.source_page) > 0 ? ` · source page ${Number(figure.source_page)}` : '';
        return `<figure><img loading="lazy" src="${escText(figure.src)}" alt="${escText(figure.alt || figure.caption || `${record.name} source figure`)}"><figcaption>${escText(figure.caption || 'Sourced course image')}${sourceUrl ? ` · <a href="${escText(sourceUrl)}" target="_blank" rel="noopener">Image source</a>` : ''}${page}${figure.license ? ` · ${escText(figure.license)}` : ''}</figcaption></figure>`;
      }).join('')}</div>` : '<p class="course-game-no-image">No bundled sourced image is available for this entry.</p>';
      const imageState = session.images.get(record.id) || {status: 'idle'};
      const status = imageState.status === 'loading' ? 'Looking up one licensed organism image…' :
        imageState.status === 'failed' ? 'The image lookup failed. Retry when ready.' :
        imageState.status === 'ambiguous' ? `The organism name has multiple source matches${imageState.candidates?.length ? `: ${imageState.candidates.map(item => item.title).join('; ')}` : ''}. No match was chosen.` :
        imageState.status === 'unrelated' ? 'The source returned a different term, so no image was shown.' :
        imageState.status === 'no-image' ? 'No licensed image is available for the canonical organism article.' : '';
      const canRequest = ['idle', 'failed'].includes(imageState.status);
      const actionLabel = figures.length ? 'Find an additional licensed organism image' : imageState.status === 'failed' ? 'Retry image lookup' : 'Show licensed organism image';
      return `${local}<div class="course-game-image-source" data-course-game-image="${escText(record.id)}">${status ? `<p role="status">${escText(status)}</p>` : ''}${canRequest ? `<button type="button" data-course-game-image-request="bugs" data-course-game-record="${escText(record.id)}" ${imageState.status === 'loading' ? 'disabled' : ''}>${actionLabel}</button>` : ''}<div data-course-game-image-result="${escText(record.id)}"></div></div>`;
    }

    function pathologyFigures(record) {
      const figures = DATA.pathology_images?.[String(record.row_index)] || [];
      return figures.filter(figure => /^\/api\/course\/asset\/[A-Za-z0-9_-]+$/.test(figure.src || '')).map(figure => `<figure><img loading="lazy" src="${escText(figure.src)}" alt="${escText(figure.caption || 'Pathology source figure')}"><figcaption>${escText(figure.caption || 'Sourced pathology image')}${figure.source_page ? ` · source page ${Number(figure.source_page)}` : ''}</figcaption></figure>`).join('') || '<p class="course-game-no-image">No sourced image is available for this entry.</p>';
    }
    function roundMarkup(kind) {
      const session = sessions.get(kind), target = root.document.querySelector(`[data-course-game-round="${kind}"]`);
      if (!session || !target) return;
      if (session.paused || session.source_changed) { target.hidden = true; return; }
      target.hidden = false;
      if (session.complete) { target.innerHTML = '<p class="course-game-finished" role="status">Round finished. Checked entries and category review dates are saved; skipped entries remain unassessed.</p>'; return; }
      const record = session.round[session.at], sheet = sheetFor(kind), row = sheet?.rows?.[record?.row_index];
      if (!record) return;
      const activeCategories = session.categories.filter(category => traitsFor(kind, record, category).length);
      const omitted = session.categories.filter(category => !traitsFor(kind, record, category).length);
      const categoryAt = Math.min(session.category_at || 0, Math.max(0, activeCategories.length - 1));
      const category = activeCategories[categoryAt];
      const answers = category ? traitsFor(kind, record, category) : [];
      const pages = category ? choicePages(choicesFor(session, kind, record, category)) : [];
      const choicePage = Math.min(session.choice_page || 0, Math.max(0, pages.length - 1));
      const chosen = session.selections[category] || [], grade = session.checked ? session.grade || gradeSelections(kind, record, session.categories, session.selections) : null;
      const busy = requests.has(kind);
      target.innerHTML = `<article class="course-game-prompt"><header><div><p class="eyebrow">${{bugs:'ORGANISM',drugs:'DRUG',pathology:'PATHOLOGY'}[kind]} · ${session.at + 1} / ${session.round.length}</p><h3>${escText(record.name)}</h3><p>Select every supported answer in each category. Your answers and place are saved as you go.</p></div><button type="button" data-course-game-close="${kind}" ${busy?'disabled':''}>Pause & close</button></header>
        <div class="course-game-category-nav"><button type="button" data-course-game-category-step="-1" data-course-game-kind="${kind}" ${busy||!categoryAt?'disabled':''}>← Previous category</button><span>${categoryAt + 1} / ${activeCategories.length} categories · ${Object.values(session.selections).filter(picked => picked.length).length} answered</span><button type="button" data-course-game-category-step="1" data-course-game-kind="${kind}" ${busy||categoryAt + 1 >= activeCategories.length?'disabled':''}>Next category →</button></div>
        ${category ? `<fieldset class="course-game-category"><legend>${escText(category)}</legend><p class="course-game-category-help">${category.startsWith('Source detail · ') ? 'Choose the complete statement supported by this entry. This covers the full source column.' : 'Select all supported characteristics, including overlapping facts.'}</p><div>${(pages[choicePage] || []).map((choice, index) => {
          const selected = chosen.some(value => key(value) === key(choice)), accepted = answers.some(value => key(value) === key(choice));
          const cls = session.checked ? (accepted ? 'is-valid' : selected ? 'is-invalid' : '') : selected ? 'is-selected' : '';
          return `<button type="button" class="course-game-choice ${cls}" data-course-game-choice="${kind}" data-course-game-cat="${escText(category)}" data-course-game-index="${choicePage * CHOICE_LIMIT + index}" aria-pressed="${selected}" ${session.checked||busy?'disabled':''}>${escText(choice)}${session.checked&&accepted?' ✓':session.checked&&selected?' ✕':''}</button>`;
        }).join('')}</div>${pages.length > 1 ? `<div class="course-game-choice-pages"><button data-course-game-choice-page="-1" data-course-game-kind="${kind}" ${busy||!choicePage?'disabled':''}>Previous choices</button><span>Choices ${choicePage + 1} / ${pages.length}</span><button data-course-game-choice-page="1" data-course-game-kind="${kind}" ${busy||choicePage + 1 >= pages.length?'disabled':''}>More choices</button></div>`:''}</fieldset>`:'<p>No selected category has a sourced answer for this entry.</p>'}
        ${omitted.length ? `<details class="course-game-omissions"><summary>${omitted.length} categories without a sourced answer</summary><p>${omitted.map(escText).join(' · ')}</p><p>These do not receive a score or review date.</p></details>`:''}
        <div class="practice-actions"><button type="button" data-course-game-check="${kind}" ${session.checked||busy||!activeCategories.length?'disabled':''}>Check every category</button><button type="button" data-course-game-next="${kind}" ${busy?'disabled':''}>${session.checked ? session.at + 1 === session.round.length ? 'Finish round' : 'Next entry →' : 'Skip entry →'}</button></div>
        ${grade ? `<div class="course-game-result ${grade.perfect?'is-perfect':''}" role="status"><strong>${grade.correct} / ${grade.total} categories complete</strong><p>${grade.perfect?'Every sourced characteristic was selected.':'Review missing and unsupported characteristics below.'} App review dates are saved separately from Anki.</p>${grade.details.filter(detail => !detail.correct).map(detail => `<p><strong>${escText(detail.category)}</strong>${detail.missing.length ? ` · missing: ${detail.missing.map(escText).join('; ')}`:''}${detail.wrong.length ? ` · unsupported: ${detail.wrong.map(escText).join('; ')}`:''}</p>`).join('')}</div><details class="course-game-answer"><summary>Review complete source entry & images</summary>${row ? `<dl>${sheet.columns.map((column,index) => `<div><dt>${escText(column)}</dt><dd>${row.cells[index]}</dd></div>`).join('')}</dl>`:''}${kind==='pathology'?pathologyFigures(record):sourceFigureMarkup(kind, record, session)}${record.clarification_html || ''}</details>`:''}
        </article>`;
      const imageState = session.images.get(record.id);
      if (imageState?.figure) { const host = target.querySelector(`[data-course-game-image-result="${escText(record.id)}"]`); if (host && !imageState.figure.isConnected) host.append(imageState.figure); }
    }
    async function start(kind) {
      const setup = root.document.querySelector(`[data-course-game-setup="${kind}"]`);
      if (!setup || requests.has(kind)) return;
      const pool = currentPool(kind, setup), categories = selectedCategories(setup, kind);
      if (!pool.length || !categories.length) return;
      const round = roundCases(pool, setup.querySelector(`[data-course-game-size="${kind}"]`).value);
      if (await act(kind, 'start', {round_ids: round.map(record => record.id), categories})) setup.querySelector(`[data-course-game-round="${kind}"]`).scrollIntoView({block:'nearest',behavior:'smooth'});
    }
    root.document.addEventListener('change', event => {
      const node = event.target.closest?.('[data-course-game-filter-category],[data-course-game-filter-value],[data-course-game-category],[data-course-game-pool]');
      if (!node) return;
      const kind = node.dataset.courseGameFilterCategory || node.dataset.courseGameFilterValue || node.dataset.courseGameCategory || node.dataset.courseGamePool;
      updateSetup(kind, !!node.dataset.courseGameFilterCategory);
    });
    root.document.addEventListener('click', async event => {
      const node = event.target.closest?.('[data-course-game-start],[data-course-game-close],[data-course-game-check],[data-course-game-next],[data-course-game-choice],[data-course-game-image-request],[data-course-game-resume],[data-course-game-category-step],[data-course-game-choice-page]');
      if (!node) return;
      const kind = node.dataset.courseGameStart || node.dataset.courseGameClose || node.dataset.courseGameCheck || node.dataset.courseGameNext || node.dataset.courseGameChoice || node.dataset.courseGameImageRequest || node.dataset.courseGameResume || node.dataset.courseGameKind;
      if (!kind || requests.has(kind)) return;
      const session = sessions.get(kind), record = session?.round[session.at];
      if (node.matches('[data-course-game-start]')) return start(kind);
      if (node.matches('[data-course-game-resume]')) return act(kind, 'resume');
      if (!session || !record) return;
      if (node.matches('[data-course-game-close]')) return act(kind, 'pause');
      if (node.matches('[data-course-game-check]')) return act(kind, 'check', {selections:session.selections,category_at:session.category_at});
      if (node.matches('[data-course-game-next]')) { session.choice_page=0; return act(kind, 'next'); }
      if (node.matches('[data-course-game-category-step]')) {
        if (session.checked) { session.choice_page=0; return act(kind, 'position', {category_at:session.category_at + Number(node.dataset.courseGameCategoryStep)}); }
        session.choice_page=0; return act(kind, 'draft', {selections:session.selections,category_at:session.category_at + Number(node.dataset.courseGameCategoryStep)});
      }
      if (node.matches('[data-course-game-choice-page]')) { session.choice_page += Number(node.dataset.courseGameChoicePage); return roundMarkup(kind); }
      if (node.matches('[data-course-game-choice]') && !session.checked) {
        const category = node.dataset.courseGameCat, value = choicesFor(session, kind, record, category)[Number(node.dataset.courseGameIndex)];
        if (value === undefined) return;
        const picked = session.selections[category] || [], selections = {...session.selections,[category]:picked.some(item => key(item)===key(value)) ? picked.filter(item => key(item)!==key(value)) : [...picked,value]};
        return act(kind, 'draft', {selections,category_at:session.category_at});
      }
      if (node.matches('[data-course-game-image-request]') && session.checked && kind === 'bugs' && record.id === node.dataset.courseGameRecord) {
        const imageState = {status:'loading'}; session.images.set(record.id,imageState); roundMarkup(kind);
        try {
          const result = await lookupOrganismFigure(root.MedicalTermCards, record.name, root.document);
          const latest = sessions.get(kind); if (latest?.id !== session.id || latest.round[latest.at]?.id !== record.id) return;
          Object.assign(imageState,result); latest.images.set(record.id,imageState); if (result.figure) result.figure.querySelector('img')?.addEventListener('error', () => { imageState.figure=null; imageState.status='failed'; if (sessions.get(kind)?.id === session.id && sessions.get(kind)?.round[sessions.get(kind).at]?.id === record.id) roundMarkup(kind); }, {once:true}); roundMarkup(kind);
        } catch (_) { imageState.status='failed'; roundMarkup(kind); }
      }
    });
    for (const kind of kinds) updateSetup(kind);
  }

  return {key, unique, traitsFor, matchesFilter, filterCases, boundedChoices, gradeSelections, roundCases, removeLegacyTraitControls, isCanonicalOrganismMatch, lookupOrganismFigure, install, seededRandom, choicePages, CHOICE_LIMIT, ROUND_SIZES};
});
