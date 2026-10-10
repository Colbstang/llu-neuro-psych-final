/* Focused, bounded drug and organism characteristic rounds for the course app. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.StepCoursePractice = api;
  if (root && root.document && typeof DATA !== 'undefined') api.install(root);
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const DEFAULT_CATEGORIES = 2;
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
    const missed = [], wrong = [];
    let correct = 0, extra = 0, total = 0;
    for (const category of categories || []) {
      const expected = traitsFor(kind, record, category);
      if (!expected.length) continue;
      total++;
      const picked = unique(selections?.[category] || []);
      const accepted = picked.filter(value => expected.some(answer => key(answer) === key(value)));
      const invalid = picked.filter(value => !expected.some(answer => key(answer) === key(value)));
      if (accepted.length && !invalid.length) correct++;
      else {
        if (!accepted.length) missed.push(category);
        if (invalid.length) {
          extra++;
          wrong.push(...invalid.map(value => `${category}: ${value}`));
        }
      }
    }
    return {correct, total, extra, missed, wrong, perfect: total > 0 && correct === total && !extra};
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
    const sessions = new Map();
    const kinds = ['drugs', 'bugs'];
    const oldComparisonMarkup = comparisonMarkup;

    function dataFor(kind) { return DATA.review_games?.[kind]; }
    function sheetFor(kind) { return DATA.comparison_sheets?.find(sheet => sheet.id === kind); }
    function scoped(kind) {
      const config = dataFor(kind);
      return (config?.cases || []).filter(record => scopeRow(kind, record.row_index));
    }
    function subsetFor(kind, category, value) {
      return filterCases(scoped(kind), category, value);
    }
    function configMarkup(kind) {
      const config = dataFor(kind), categories = config?.categories || [];
      const title = kind === 'bugs' ? 'Organism' : 'Drug';
      const defaultFilter = categories[0] || '';
      const filterValues = unique(scoped(kind).flatMap(record => traitsFor(kind, record, defaultFilter))).sort((a, b) => a.localeCompare(b));
      const initialCount = scoped(kind).length;
      return `<section class="course-game-setup" data-course-game-setup="${kind}">
        <div class="course-game-intro"><p class="eyebrow">FOCUSED CHARACTERISTICS</p><p>Choose a subset, the characteristics to practice, and a manageable round length. Each category shows at most ${CHOICE_LIMIT} sourced choices.</p></div>
        <div class="course-game-settings">
          <label>Subset category<select data-course-game-filter-category="${kind}"><option value="">All ${title.toLowerCase()} entries</option>${categories.map(category => `<option value="${escText(category)}" ${category === defaultFilter ? 'selected' : ''}>${escText(category)}</option>`).join('')}</select></label>
          <label>Subset value<select data-course-game-filter-value="${kind}"><option value="">All values</option>${filterValues.map(value => `<option value="${escText(value)}">${escText(value)}</option>`).join('')}</select></label>
          <label>Round size<select data-course-game-size="${kind}"><option value="5" selected>5 entries</option><option value="10">10 entries</option><option value="20">20 entries</option><option value="all">All in subset</option></select></label>
        </div>
        <fieldset class="course-game-focus"><legend>Practice characteristics</legend>${categories.map((category, index) => `<label><input type="checkbox" data-course-game-category="${kind}" value="${escText(category)}" ${index < DEFAULT_CATEGORIES ? 'checked' : ''}> ${escText(category)}</label>`).join('')}</fieldset>
        <p class="course-game-count" data-course-game-count="${kind}" aria-live="polite">${initialCount} entries in this subset.</p>
        <button type="button" data-course-game-start="${kind}" ${initialCount ? '' : 'disabled'}>Start focused round</button>
        <div class="course-game-round" data-course-game-round="${kind}" hidden></div>
      </section>`;
    }
    comparisonMarkup = function (sheetId = null) {
      let html = oldComparisonMarkup(sheetId);
      if (!kinds.includes(sheetId)) return html;
      html = removeLegacyTraitControls(html, sheetId);
      const controls = `<div class="practice-modes" role="group" aria-label="${sheetId === 'bugs' ? 'Organism' : 'Drug'} practice mode"><button type="button" data-shuffle-sheet="${sheetId}">Shuffle table</button></div>${configMarkup(sheetId)}`;
      return html.replace('<label class="sheet-search">', `${controls}<label class="sheet-search">`);
    };

    function updateSetup(kind) {
      const setup = root.document.querySelector(`[data-course-game-setup="${kind}"]`);
      if (!setup) return;
      const category = setup.querySelector(`[data-course-game-filter-category="${kind}"]`).value;
      const valueSelect = setup.querySelector(`[data-course-game-filter-value="${kind}"]`);
      const priorValue = valueSelect.value;
      const values = category ? unique(scoped(kind).flatMap(record => traitsFor(kind, record, category))).sort((a, b) => a.localeCompare(b)) : [];
      valueSelect.replaceChildren(new Option('All values', ''));
      for (const value of values) valueSelect.add(new Option(value, value));
      if (values.some(value => key(value) === key(priorValue))) valueSelect.value = values.find(value => key(value) === key(priorValue));
      valueSelect.disabled = !category;
      const cases = subsetFor(kind, category, valueSelect.value);
      const count = setup.querySelector(`[data-course-game-count="${kind}"]`);
      count.textContent = `${cases.length} entries in this subset.`;
      setup.querySelector(`[data-course-game-start="${kind}"]`).disabled = !cases.length || !selectedCategories(setup, kind).length;
    }

    function selectedCategories(setup, kind) {
      const available = setup.querySelectorAll(`[data-course-game-category="${kind}"]:checked`);
      return [...available].map(input => input.value);
    }

    function choicesFor(session, kind, record, category) {
      let byCategory = session.options.get(record.id);
      if (!byCategory) {
        byCategory = new Map();
        session.options.set(record.id, byCategory);
      }
      if (!byCategory.has(category)) byCategory.set(category, boundedChoices(kind, category, record, session.pool, CHOICE_LIMIT));
      return byCategory.get(category);
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

    function roundMarkup(kind) {
      const session = sessions.get(kind), target = root.document.querySelector(`[data-course-game-round="${kind}"]`);
      if (!session || !target) return;
      const record = session.round[session.at], config = dataFor(kind), sheet = sheetFor(kind);
      const row = sheet?.rows?.[record.row_index];
      const grade = session.checked ? gradeSelections(kind, record, session.categories, session.selections) : null;
      const activeCategories = session.categories.filter(category => traitsFor(kind, record, category).length);
      target.hidden = false;
      target.innerHTML = `<article class="course-game-prompt"><header><div><p class="eyebrow">${kind === 'bugs' ? 'ORGANISM' : 'DRUG'} · ${session.at + 1} / ${session.round.length}</p><h3>${escText(record.name)}</h3><p>Select any supported characteristic in each chosen category. Shared answers are accepted.</p></div><button type="button" data-course-game-close="${kind}">Close</button></header>
        ${activeCategories.length ? activeCategories.map(category => {
          const answers = traitsFor(kind, record, category), choices = choicesFor(session, kind, record, category);
          const chosen = session.selections[category] || [];
          return `<fieldset class="course-game-category"><legend>${escText(category)}</legend><div>${choices.map((choice, index) => {
            const selected = chosen.some(value => key(value) === key(choice));
            const accepted = answers.some(value => key(value) === key(choice));
            const cls = session.checked ? (accepted ? 'is-valid' : selected ? 'is-invalid' : '') : selected ? 'is-selected' : '';
            return `<button type="button" class="course-game-choice ${cls}" data-course-game-choice="${kind}" data-course-game-cat="${escText(category)}" data-course-game-index="${index}" aria-pressed="${selected}" ${session.checked ? 'disabled' : ''}>${escText(choice)}${session.checked && accepted ? ' ✓' : session.checked && selected ? ' ✕' : ''}</button>`;
          }).join('')}</div></fieldset>`;
        }).join('') : '<p>No selected characteristic category has an answer for this entry. Choose another category or subset.</p>'}
        <div class="practice-actions"><button type="button" data-course-game-check="${kind}" ${session.checked || !activeCategories.length ? 'disabled' : ''}>Check entry</button><button type="button" data-course-game-next="${kind}">${session.at + 1 === session.round.length ? 'Finish round' : 'Next entry →'}</button><button type="button" data-course-game-restart="${kind}">Restart subset</button></div>
        ${grade ? `<p class="course-game-result ${grade.perfect ? 'is-perfect' : ''}" role="status">${grade.correct} / ${grade.total} categories correct${grade.missed.length ? ` · still to find: ${grade.missed.map(escText).join(', ')}` : ''}${grade.wrong.length ? ` · unsupported choices: ${grade.wrong.map(escText).join(', ')}` : ''}</p><details class="course-game-answer"><summary>Review source entry</summary>${row ? `<dl>${sheet.columns.map((column, index) => `<div><dt>${escText(column)}</dt><dd>${row.cells[index]}</dd></div>`).join('')}</dl>` : ''}${sourceFigureMarkup(kind, record, session)}${record.clarification_html || ''}<p>${(record.sources || []).map(source => typeof source === 'string' ? escText(source) : escText(source.label || source.title || '')).filter(Boolean).join(' · ')}</p></details>` : ''}
        </article>`;
      const imageState = session.images.get(record.id);
      if (imageState?.figure) {
        const host = target.querySelector(`[data-course-game-image-result="${escText(record.id)}"]`);
        if (host && !imageState.figure.isConnected) host.append(imageState.figure);
      }
    }

    function start(kind) {
      const setup = root.document.querySelector(`[data-course-game-setup="${kind}"]`);
      if (!setup) return;
      const category = setup.querySelector(`[data-course-game-filter-category="${kind}"]`).value;
      const value = setup.querySelector(`[data-course-game-filter-value="${kind}"]`).value;
      const pool = subsetFor(kind, category, value);
      const categories = selectedCategories(setup, kind);
      if (!pool.length || !categories.length) return;
      const size = setup.querySelector(`[data-course-game-size="${kind}"]`).value;
      const round = roundCases(pool, size);
      sessions.set(kind, {pool, round, categories, at: 0, selections: {}, checked: false, size, category, value, options: new Map(), images: new Map()});
      roundMarkup(kind);
      setup.querySelector(`[data-course-game-round="${kind}"]`).scrollIntoView({block: 'nearest', behavior: 'smooth'});
    }

    root.document.addEventListener('change', event => {
      const node = event.target;
      const category = node.closest?.('[data-course-game-filter-category]');
      if (category) updateSetup(category.dataset.courseGameFilterCategory);
      const value = node.closest?.('[data-course-game-filter-value]');
      if (value) updateSetup(value.dataset.courseGameFilterValue);
      const focus = node.closest?.('[data-course-game-category]');
      if (focus) updateSetup(focus.dataset.courseGameCategory);
    });
    root.document.addEventListener('click', async event => {
      const node = event.target.closest?.('[data-course-game-start], [data-course-game-close], [data-course-game-check], [data-course-game-next], [data-course-game-restart], [data-course-game-choice], [data-course-game-image-request]');
      if (!node) return;
      const kind = node.dataset.courseGameStart || node.dataset.courseGameClose || node.dataset.courseGameCheck || node.dataset.courseGameNext || node.dataset.courseGameRestart || node.dataset.courseGameChoice || node.dataset.courseGameImageRequest;
      if (!kind) return;
      if (node.matches('[data-course-game-start]')) start(kind);
      else if (node.matches('[data-course-game-image-request]')) {
        const session = sessions.get(kind), record = session?.round[session.at];
        if (!session || !session.checked || kind !== 'bugs' || record?.id !== node.dataset.courseGameRecord) return;
        const current = session.images.get(record.id);
        if (current?.status === 'loading' || current?.status === 'ready' || current?.status === 'ambiguous' || current?.status === 'unrelated' || current?.status === 'no-image') return;
        const imageState = {status: 'loading'};
        session.images.set(record.id, imageState);
        roundMarkup(kind);
        try {
          const result = await lookupOrganismFigure(root.MedicalTermCards, record.name, root.document);
          if (sessions.get(kind) !== session || session.round[session.at]?.id !== record.id) return;
          Object.assign(imageState, result);
          if (result.figure) {
            result.figure.querySelector('img')?.addEventListener('error', () => {
              imageState.figure = null;
              imageState.status = 'failed';
              if (sessions.get(kind) === session && session.round[session.at]?.id === record.id) roundMarkup(kind);
            }, {once: true});
          }
          roundMarkup(kind);
        } catch (_) {
          if (sessions.get(kind) !== session || session.round[session.at]?.id !== record.id) return;
          imageState.status = 'failed';
          roundMarkup(kind);
        }
      }
      else if (node.matches('[data-course-game-close]')) {
        sessions.delete(kind);
        const target = root.document.querySelector(`[data-course-game-round="${kind}"]`);
        if (target) { target.hidden = true; target.replaceChildren(); }
      } else if (node.matches('[data-course-game-choice]')) {
        const session = sessions.get(kind);
        if (!session || session.checked) return;
        const category = node.dataset.courseGameCat;
        const answer = session.round[session.at];
        const choices = choicesFor(session, kind, answer, category);
        const value = choices[Number(node.dataset.courseGameIndex)];
        if (value === undefined) return;
        const picked = session.selections[category] || [];
        session.selections[category] = picked.some(item => key(item) === key(value)) ? picked.filter(item => key(item) !== key(value)) : [...picked, value];
        roundMarkup(kind);
      } else if (node.matches('[data-course-game-check]')) {
        const session = sessions.get(kind);
        if (!session || session.checked) return;
        session.checked = true;
        const record = session.round[session.at];
        state.traitPractice ||= {};
        state.traitPractice[record.id] = {...gradeSelections(kind, record, session.categories, session.selections), reviewedAt: Date.now()};
        save();
        roundMarkup(kind);
      } else if (node.matches('[data-course-game-next]')) {
        const session = sessions.get(kind);
        if (!session) return;
        if (session.at + 1 >= session.round.length) {
          sessions.delete(kind);
          const target = root.document.querySelector(`[data-course-game-round="${kind}"]`);
          if (target) target.innerHTML = '<p class="course-game-finished" role="status">Round complete. Start another round when you are ready.</p>';
          return;
        }
        session.at++;
        session.selections = {};
        session.checked = false;
        roundMarkup(kind);
      } else if (node.matches('[data-course-game-restart]')) start(kind);
    });
    for (const kind of kinds) updateSetup(kind);
  }

  return {key, unique, traitsFor, matchesFilter, filterCases, boundedChoices, gradeSelections, roundCases, removeLegacyTraitControls, isCanonicalOrganismMatch, lookupOrganismFigure, install, CHOICE_LIMIT, ROUND_SIZES};
});
