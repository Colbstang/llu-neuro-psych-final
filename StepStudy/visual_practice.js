/* Private image-occlusion practice. All source images are resolved by the host app. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.StepVisualRecall = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const MAX_MASKS = 64;
  const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
  const RATINGS = ['again', 'hard', 'good', 'easy'];
  const RATING_LABELS = {again: 'Again', hard: 'Hard', good: 'Good', easy: 'Easy'};

  function clamp(value, low = 0, high = 1) { return Math.max(low, Math.min(high, value)); }
  function normalizeRect(rect) {
    if (!rect || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(Number(rect[key])))) return null;
    const x = clamp(Number(rect.x)), y = clamp(Number(rect.y));
    const width = Math.min(Number(rect.width), 1 - x), height = Math.min(Number(rect.height), 1 - y);
    if (width < 0.005 || height < 0.005) return null;
    return {x: Number(x.toFixed(5)), y: Number(y.toFixed(5)), width: Number(width.toFixed(5)), height: Number(height.toFixed(5))};
  }
  function sourceKey(source) { return source && typeof source.kind === 'string' && typeof source.id === 'string' ? source.kind + ':' + source.id : ''; }
  function sourceRef(source) { return source?.source && typeof source.source === 'object' ? source.source : source; }
  function safeImageUrl(source, api) {
    if (!source || !['course_asset', 'intake_image', 'upload'].includes(source.kind) || !/^[A-Za-z0-9_-]{1,128}$/.test(String(source.id))) return '';
    const raw = api?.imageUrl ? api.imageUrl(source) : '/api/visual/image/' + encodeURIComponent(source.kind) + '/' + encodeURIComponent(source.id);
    if (typeof raw !== 'string' || !raw.startsWith('/api/visual/image/') || raw.startsWith('//')) return '';
    try {
      const url = new URL(raw, typeof location !== 'undefined' ? location.href : 'http://localhost/');
      if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';
      if (typeof location !== 'undefined' && url.origin !== location.origin) return '';
      if (!url.pathname.startsWith('/api/visual/image/')) return '';
      return url.href;
    } catch (_) { return ''; }
  }
  function createPracticeState(masks) {
    const allowed = new Set((Array.isArray(masks) ? masks : []).map(mask => mask?.id).filter(id => typeof id === 'string'));
    const state = {revealed: new Set(), count: allowed.size};
    state.reveal = id => { if (allowed.has(id)) state.revealed.add(id); };
    state.revealAll = () => (masks || []).forEach(mask => state.revealed.add(mask.id));
    state.canRate = () => state.count > 0 && state.revealed.size >= state.count;
    return state;
  }
  function identifier() {
    return globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID().replaceAll('-', '') :
      Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
  }
  function text(doc, value, className) {
    const node = doc.createElement('p'); node.textContent = String(value ?? '');
    if (className) node.className = className;
    return node;
  }

  async function mount(container, options = {}) {
    if (!container?.ownerDocument) throw new TypeError('A DOM container is required');
    container.classList.add('visual-recall');
    const doc = container.ownerDocument, api = options.api || {};
    let destroyed = false, sources = [], exercises = [], exercise = null, draftMasks = [], chosenSource = null;
    let draftName = '', draftTopics = [], draftChapter = '', draftTopicsTouched = false, draftChapterTouched = false;
    const emitError = error => { options.onError?.(error); };
    const button = (label, action, className = '') => {
      const node = doc.createElement('button'); node.type = 'button'; node.textContent = label;
      if (className) node.className = className;
      node.addEventListener('click', action); return node;
    };
    const heading = (level, label) => { const node = doc.createElement('h' + level); node.textContent = label; return node; };
    const labeled = (labelText, input) => { const label = doc.createElement('label'); label.className = 'visual-field'; label.append(doc.createTextNode(labelText), input); return label; };
    const status = message => { const node = text(doc, message, 'visual-status'); node.setAttribute('role', 'status'); return node; };
    const sourceTitle = source => sources.find(row => sourceKey(row.source) === sourceKey(source))?.title ||
      (sourceKey(source) === sourceKey(sourceRef(options.initialSource)) ? options.initialSource?.title : '') || source?.title || 'Saved image';
    const chapterTitle = chapterId => {
      if (!chapterId) return '';
      const row = sources.find(item => item.chapter_id === chapterId);
      return row?.chapter_title || row?.chapter_label || 'Linked course chapter';
    };
    const loadSources = async () => {
      if (typeof api.listSources !== 'function') return [];
      const result = await api.listSources();
      return Array.isArray(result) ? result : (result?.sources || []);
    };
    const loadExercises = async () => {
      if (typeof api.listExercises !== 'function') return [];
      const result = await api.listExercises({});
      return Array.isArray(result) ? result : (result?.exercises || []);
    };
    const call = async (method, ...args) => {
      if (typeof api[method] !== 'function') throw new Error('Visual practice service is unavailable.');
      const result = await api[method](...args);
      if (result?.ok === false) throw new Error(result.error || 'Visual practice could not be saved.');
      return result;
    };
    function stageFor(source, alt = 'Study image') {
      const src = safeImageUrl(source, api);
      if (!src) throw new Error('This image source is not available in the local study workspace.');
      const stage = doc.createElement('div'); stage.className = 'visual-stage';
      const image = doc.createElement('img'); image.src = src; image.alt = alt; image.draggable = false;
      image.addEventListener('error', () => { stage.replaceChildren(text(doc, 'The local image is unavailable. Reopen the image or select another source.', 'visual-error')); }, {once: true});
      stage.append(image); return stage;
    }
    function renderGallery(message = '') {
      if (destroyed) return;
      container.replaceChildren();
      const top = doc.createElement('div'); top.className = 'visual-heading';
      top.append(heading(2, 'Image recall'), text(doc, 'Create private image exercises from course figures, saved intake images, or your own upload.', 'visual-intro'));
      top.append(button('Create exercise', () => renderEditor(), 'visual-primary'));
      container.append(top);
      if (message) container.append(status(message));
      if (!exercises.length) {
        container.append(text(doc, 'No image exercises yet. Choose a local image, draw regions, and add the text you want revealed during practice.', 'visual-empty'));
        return;
      }
      const list = doc.createElement('div'); list.className = 'visual-list';
      for (const row of exercises) {
        const item = doc.createElement('article'); item.className = 'visual-item';
        item.append(heading(3, row.name), text(doc, sourceTitle(row.source) + ' · ' + row.mask_count + ' region' + (row.mask_count === 1 ? '' : 's'), 'visual-meta'));
        if (row.chapter_id) item.append(text(doc, 'Chapter · ' + chapterTitle(row.chapter_id), 'visual-meta'));
        item.append(text(doc, row.review_count ? 'Reviewed ' + row.review_count + ' time' + (row.review_count === 1 ? '' : 's') + (row.due_at ? ' · due ' + new Date(row.due_at).toLocaleDateString() : '') : 'Not practiced yet', 'visual-meta'));
        const actions = doc.createElement('div'); actions.className = 'visual-actions';
        actions.append(button('Practice', () => openPractice(row.id), 'visual-primary'), button('Edit', () => openEditor(row.id)));
        item.append(actions); list.append(item);
      }
      container.append(list);
    }
    function renderSourcePicker() {
      const control = doc.createElement('div'); control.className = 'visual-source-control';
      const filter = doc.createElement('input'); filter.type = 'search'; filter.placeholder = 'Title or chapter';
      filter.setAttribute('aria-label', 'Find an image by title or chapter');
      const select = doc.createElement('select'); select.id = 'visual-source'; select.required = true;
      select.setAttribute('aria-label', 'Source image');
      const placeholder = doc.createElement('option'); placeholder.value = ''; placeholder.textContent = 'Choose an image source';
      const populate = () => {
        const query = filter.value.trim().toLowerCase();
        select.replaceChildren(placeholder);
        let selectedValue = '';
        sources.forEach((row, index) => {
          const source = sourceRef(row.source);
          if (!sourceKey(source) || !safeImageUrl(source, api)) return;
          const title = row.title || 'Image', chapter = row.chapter_title || row.chapter_id || '';
          const selected = sourceKey(source) === sourceKey(chosenSource);
          if (query && ![title, chapter].join(' ').toLowerCase().includes(query) && !selected) return;
          const option = doc.createElement('option'); option.value = String(index);
          option.textContent = [title, chapter].filter(Boolean).join(' · '); select.append(option);
          if (selected) selectedValue = String(index);
        });
        if (chosenSource && !selectedValue) {
          const option = doc.createElement('option'); option.value = 'initial';
          option.textContent = 'Selected · ' + sourceTitle(chosenSource); select.append(option); selectedValue = 'initial';
        }
        select.value = selectedValue;
      };
      populate();
      filter.addEventListener('input', populate);
      select.addEventListener('change', () => {
        chosenSource = select.value === 'initial' ? (sourceRef(options.initialSource) || chosenSource) : (select.value ? sourceRef(sources[Number(select.value)]?.source) : null);
        const selectedSourceInfo = sources.find(row => sourceKey(row.source) === sourceKey(chosenSource)) ||
          (sourceKey(chosenSource) === sourceKey(sourceRef(options.initialSource)) ? options.initialSource : null);
        if (!draftTopicsTouched) draftTopics = [...(selectedSourceInfo?.topic_ids || [])];
        if (!draftChapterTouched) draftChapter = selectedSourceInfo?.chapter_id || '';
        draftMasks = [];
        if (exercise) exercise = {...exercise, source: chosenSource};
        renderEditor(exercise?.id || null, true);
      });
      control.append(filter, select); return control;
    }
    function updateMaskOverlay(stage, mask, index, edit = false) {
      const node = doc.createElement(edit ? 'div' : 'button');
      if (!edit) { node.type = 'button'; node.setAttribute('aria-label', 'Reveal mask ' + (index + 1)); }
      node.className = 'visual-mask' + (edit ? ' is-editable' : '');
      node.style.left = (mask.x * 100) + '%'; node.style.top = (mask.y * 100) + '%';
      node.style.width = (mask.width * 100) + '%'; node.style.height = (mask.height * 100) + '%';
      node.dataset.maskId = mask.id; return node;
    }
    function drawLayer(stage, masks, onAdded) {
      let start = null, drawing = null;
      const position = event => {
        const rect = stage.getBoundingClientRect();
        if (!rect.width || !rect.height) return null;
        return {x: clamp((event.clientX - rect.left) / rect.width), y: clamp((event.clientY - rect.top) / rect.height)};
      };
      stage.addEventListener('pointerdown', event => {
        if (event.button !== 0 || event.target.closest('.visual-mask')) return;
        start = position(event); if (!start) return;
        drawing = doc.createElement('div'); drawing.className = 'visual-mask visual-mask-draft';
        drawing.style.left = (start.x * 100) + '%'; drawing.style.top = (start.y * 100) + '%';
        stage.append(drawing); stage.setPointerCapture?.(event.pointerId); event.preventDefault();
      });
      stage.addEventListener('pointermove', event => {
        if (!start || !drawing) return;
        const end = position(event); if (!end) return;
        const rect = normalizeRect({x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y)});
        if (rect) { drawing.style.left = (rect.x * 100) + '%'; drawing.style.top = (rect.y * 100) + '%'; drawing.style.width = (rect.width * 100) + '%'; drawing.style.height = (rect.height * 100) + '%'; }
      });
      stage.addEventListener('pointerup', event => {
        if (!start || !drawing) return;
        const end = position(event); if (!end) { drawing.remove(); start = null; drawing = null; return; }
        const rect = normalizeRect({x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y)});
        drawing.remove(); start = null; drawing = null;
        if (!rect || masks.length >= MAX_MASKS) return;
        onAdded({id: identifier(), ...rect, label: '', answer: ''});
      });
      return stage;
    }
    function renderMaskEditor(stage) {
      const list = doc.createElement('div'); list.className = 'visual-mask-list';
      const refreshOverlays = () => {
        stage.querySelectorAll('.visual-mask').forEach(node => node.remove());
        draftMasks.forEach((mask, index) => stage.append(updateMaskOverlay(stage, mask, index, true)));
      };
      refreshOverlays();
      draftMasks.forEach((mask, index) => {
        const row = doc.createElement('fieldset'); row.className = 'visual-mask-edit';
        const legend = doc.createElement('legend'); legend.textContent = 'Region ' + (index + 1); row.append(legend);
        const labelInput = doc.createElement('input'); labelInput.type = 'text'; labelInput.maxLength = 100; labelInput.value = mask.label || ''; labelInput.placeholder = 'Optional label';
        labelInput.addEventListener('input', () => { mask.label = labelInput.value; });
        row.append(labeled('Name revealed with answer', labelInput));
        const answer = doc.createElement('textarea'); answer.rows = 2; answer.maxLength = 500; answer.value = mask.answer || ''; answer.placeholder = 'Private answer shown after reveal';
        answer.addEventListener('input', () => { mask.answer = answer.value; }); row.append(labeled('Reveal text', answer));
        const coords = doc.createElement('div'); coords.className = 'visual-coordinates';
        [['x','Left'],['y','Top'],['width','Width'],['height','Height']].forEach(([key, label]) => {
          const input = doc.createElement('input'); input.type = 'number'; input.min = key === 'width' || key === 'height' ? '0.5' : '0'; input.max = '100'; input.step = '0.5'; input.value = String(Math.round(mask[key] * 100)); input.setAttribute('aria-label', label + ' percent');
          input.addEventListener('change', () => {
            const next = normalizeRect({...mask, [key]: clamp(Number(input.value) / 100)});
            if (!next) { input.value = String(Math.round(mask[key] * 100)); return; }
            Object.assign(mask, next); refreshOverlays();
          });
          coords.append(labeled(label + ' %', input));
        });
        row.append(coords, button('Remove region ' + (index + 1), () => { draftMasks = draftMasks.filter(item => item.id !== mask.id); renderEditor(exercise?.id || null, true); })); list.append(row);
      });
      return list;
    }
    async function readUpload(file) {
      if (!file || !['image/png', 'image/jpeg'].includes(file.type) || file.size <= 0 || file.size > MAX_UPLOAD_BYTES)
        throw new Error('Choose a PNG or JPEG image up to 10 MiB.');
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (let offset = 0; offset < bytes.length; offset += 0x8000)
        binary += String.fromCharCode(...bytes.subarray(offset, Math.min(bytes.length, offset + 0x8000)));
      return {mime_type: file.type, data: btoa(binary)};
    }
    async function renderEditor(id = null, preserveDraft = false) {
      if (destroyed) return;
      container.replaceChildren();
      let current = id ? exercise : null;
      if (id && !current) {
        try { const result = await call('getExercise', id); current = result?.exercise || result; }
        catch (error) { emitError(error); renderGallery(error.message); return; }
      }
      exercise = current || null;
      if (current && !preserveDraft) draftMasks = JSON.parse(JSON.stringify(current.masks || []));
      else if (!current && !preserveDraft) draftMasks = [];
      chosenSource = preserveDraft ? (chosenSource || current?.source || sourceRef(options.initialSource) || null) :
        (current?.source || sourceRef(options.initialSource) || chosenSource || null);
      const sourceInfo = sources.find(row => sourceKey(row.source) === sourceKey(chosenSource)) ||
        (sourceKey(chosenSource) === sourceKey(sourceRef(options.initialSource)) ? options.initialSource : null);
      if (!preserveDraft) {
        draftName = current?.name || '';
        draftTopics = current ? [...(current.topic_ids || [])] : [...(sourceInfo?.topic_ids || [])];
        draftChapter = current ? (current.chapter_id || '') : (sourceInfo?.chapter_id || '');
        draftTopicsTouched = Boolean(current);
        draftChapterTouched = Boolean(current);
      }
      const top = doc.createElement('header'); top.className = 'visual-heading';
      top.append(button('← Exercises', () => renderGallery()), heading(2, current ? 'Edit image exercise' : 'Create image exercise'));
      container.append(top);
      const form = doc.createElement('form'); form.className = 'visual-editor';
      form.addEventListener('submit', async event => {
        event.preventDefault();
        try {
          if (!chosenSource) throw new Error('Choose or upload a local image first.');
          if (!draftMasks.length) throw new Error('Draw or add at least one region.');
          const payload = {name: nameInput.value, source: chosenSource, topic_ids: [...draftTopics], chapter_id: chapterInput.value || null, masks: draftMasks};
          const result = current ? await call('updateExercise', current.id, payload) : await call('createExercise', payload);
          const saved = result?.exercise || result;
          options.onCreated?.(saved);
          exercises = await loadExercises();
          renderGallery(current ? 'Exercise saved.' : 'Exercise created.');
        } catch (error) { emitError(error); form.prepend(status(error.message)); }
      });
      const nameInput = doc.createElement('input'); nameInput.type = 'text'; nameInput.maxLength = 120; nameInput.required = true; nameInput.value = draftName;
      nameInput.placeholder = 'e.g. Basal ganglia landmarks'; form.append(labeled('Exercise name', nameInput));
      nameInput.addEventListener('input', () => { draftName = nameInput.value; });
      const sourceBlock = doc.createElement('div'); sourceBlock.className = 'visual-source-picker'; sourceBlock.append(labeled('Source image', renderSourcePicker()));
      const fileInput = doc.createElement('input'); fileInput.type = 'file'; fileInput.accept = 'image/png,image/jpeg'; fileInput.setAttribute('aria-label', 'Upload a private PNG or JPEG image');
      const uploadStatus = text(doc, 'Uploads stay in this private study workspace.', 'visual-meta');
      fileInput.addEventListener('change', async () => {
        const file = fileInput.files?.[0]; if (!file) return;
        try {
          const result = await call('uploadImage', await readUpload(file));
          const source = result?.source || result;
          if (!sourceKey(source)) throw new Error('The local service did not return a saved image reference.');
          chosenSource = source;
          sources = [...sources, {source, title: file.name, chapter_id: null}];
          renderEditor(current?.id || null, true);
        } catch (error) { uploadStatus.textContent = error.message; uploadStatus.className = 'visual-error'; emitError(error); }
      });
      sourceBlock.append(labeled('Or upload an image', fileInput), uploadStatus);
      if (chosenSource) {
        const sourceName = sourceTitle(chosenSource);
        sourceBlock.append(text(doc, 'Selected · ' + sourceName, 'visual-meta'));
      }
      form.append(sourceBlock);
      const chapterInput = doc.createElement('select'); chapterInput.id = 'visual-chapter';
      const noChapter = doc.createElement('option'); noChapter.value = ''; noChapter.textContent = 'No chapter'; chapterInput.append(noChapter);
      const chapterChoices = new Map();
      for (const row of sources) {
        if (row.chapter_id && !chapterChoices.has(row.chapter_id))
          chapterChoices.set(row.chapter_id, row.chapter_title || row.chapter_label || row.title || 'Course chapter');
      }
      if (draftChapter && !chapterChoices.has(draftChapter)) chapterChoices.set(draftChapter, 'Saved chapter');
      for (const [chapterId, title] of chapterChoices) {
        const option = doc.createElement('option'); option.value = chapterId; option.textContent = title; chapterInput.append(option);
      }
      chapterInput.value = draftChapter;
      form.append(labeled('Chapter (optional)', chapterInput));
      chapterInput.addEventListener('change', () => { draftChapter = chapterInput.value; draftChapterTouched = true; });
      if (Array.isArray(options.topics) && options.topics.length) {
        const topics = doc.createElement('fieldset'); topics.className = 'visual-topics';
        const legend = doc.createElement('legend'); legend.textContent = 'Topics (optional)'; topics.append(legend);
        for (const topic of options.topics) {
          if (!topic?.id) continue;
          const input = doc.createElement('input'); input.type = 'checkbox'; input.value = topic.id; input.dataset.visualTopic = '';
          input.checked = draftTopics.includes(topic.id);
          input.addEventListener('change', () => {
            draftTopics = [...container.querySelectorAll('[data-visual-topic]:checked')].map(node => node.value);
            draftTopicsTouched = true;
          });
          topics.append(labeled(topic.label || topic.id, input));
        }
        form.append(topics);
      }
      if (chosenSource) {
        try {
          form.append(button('Add region', () => {
            if (draftMasks.length >= MAX_MASKS) return;
            draftMasks.push({id: identifier(), x: .1, y: .1, width: .2, height: .2, label: '', answer: ''});
            renderEditor(current?.id || null, true);
          }));
          const stage = drawLayer(stageFor(chosenSource, sourceTitle(chosenSource)), draftMasks, mask => { draftMasks.push(mask); renderEditor(current?.id || null, true); });
          form.append(text(doc, 'Drag over the image or choose Add region, then adjust its coordinates below.', 'visual-meta'), stage);
          if (draftMasks.length) form.append(renderMaskEditor(stage));
        } catch (error) { form.append(status(error.message)); }
      } else if (!sources.length) form.append(status('Loading private images…'));
      form.append(button('Save exercise', () => {}, 'visual-primary'));
      form.lastChild.type = 'submit';
      container.append(form);
    }
    async function openEditor(id) {
      try { const result = await call('getExercise', id); exercise = result?.exercise || result; await renderEditor(id); }
      catch (error) { emitError(error); renderGallery(error.message); }
    }
    async function openPractice(id) {
      try {
        const result = await call('getExercise', id); exercise = result?.exercise || result;
        if (!exercise?.source || !Array.isArray(exercise.masks)) throw new Error('The exercise is incomplete. Edit it to select an image and regions.');
        renderPractice(exercise);
      } catch (error) { emitError(error); renderGallery(error.message); }
    }
    function renderPractice(record) {
      const state = createPracticeState(record.masks);
      let reviewRequestId = null, attemptedRating = null, reviewComplete = false;
      let ratings;
      const syncRatingButtons = () => ratings?.querySelectorAll('button').forEach(node => {
        node.disabled = reviewComplete || !state.canRate() || Boolean(attemptedRating && node.dataset.rating !== attemptedRating);
      });
      container.replaceChildren();
      const top = doc.createElement('header'); top.className = 'visual-heading';
      top.append(button('← Exercises', () => renderGallery()), heading(2, record.name));
      top.append(text(doc, sourceTitle(record.source) + (record.chapter_id ? ' · ' + chapterTitle(record.chapter_id) : ''), 'visual-meta'));
      container.append(top);
      let stage;
      try { stage = stageFor(record.source, sourceTitle(record.source)); }
      catch (error) { container.append(status(error.message)); return; }
      record.masks.forEach((mask, index) => {
        const node = updateMaskOverlay(stage, mask, index);
        const update = () => {
          const open = state.revealed.has(mask.id); node.classList.toggle('is-revealed', open);
          node.setAttribute('aria-expanded', String(open));
          node.textContent = open ? [mask.label, mask.answer].filter(Boolean).join(' · ') || 'Revealed' : 'Mask ' + (index + 1);
        };
        node.addEventListener('click', () => { state.reveal(mask.id); update(); syncRatingButtons(); });
        update(); stage.append(node);
      });
      container.append(stage);
      const actions = doc.createElement('div'); actions.className = 'visual-actions';
      const revealAll = button('Reveal all', () => { state.revealAll(); stage.querySelectorAll('.visual-mask').forEach((node, index) => {
        const mask = record.masks[index]; node.classList.add('is-revealed'); node.setAttribute('aria-expanded', 'true');
        node.textContent = [mask.label, mask.answer].filter(Boolean).join(' · ') || 'Revealed';
      }); syncRatingButtons(); });
      actions.append(revealAll, button('Hide all', () => { state.revealed.clear(); stage.querySelectorAll('.visual-mask').forEach((node, index) => {
        node.classList.remove('is-revealed'); node.setAttribute('aria-expanded', 'false'); node.textContent = 'Mask ' + (index + 1);
      }); syncRatingButtons(); })); container.append(actions);
      ratings = doc.createElement('div'); ratings.className = 'visual-ratings'; ratings.setAttribute('aria-label', 'Self rate this image exercise');
      for (const rating of RATINGS) {
        const control = button(RATING_LABELS[rating], async () => {
          if (!state.canRate()) return;
          if (attemptedRating && attemptedRating !== rating) return;
          attemptedRating = rating;
          reviewRequestId = reviewRequestId || identifier();
          ratings.querySelectorAll('button').forEach(node => node.disabled = true);
          control.textContent = 'Saving ' + RATING_LABELS[rating] + '…';
          try {
            const result = await call('recordReview', record.id, rating, reviewRequestId);
            const updated = result?.exercise || result;
            options.onReviewed?.(updated, result?.review);
            reviewComplete = true;
            control.textContent = 'Saved: ' + RATING_LABELS[rating];
            syncRatingButtons();
            container.append(status('Self-rating saved. Next due ' + (updated?.due_at ? new Date(updated.due_at).toLocaleDateString() : 'later') + '.'));
          } catch (error) {
            control.textContent = 'Retry ' + RATING_LABELS[rating];
            ratings.querySelectorAll('button').forEach(node => node.disabled = node !== control || !state.canRate());
            emitError(error);
            container.append(status(error.message));
          }
        });
        control.dataset.rating = rating;
        control.disabled = true; ratings.append(control);
      }
      container.append(ratings);
    }
    try {
      container.replaceChildren(status('Loading private image exercises…'));
      [sources, exercises] = await Promise.all([loadSources(), loadExercises()]);
      if (destroyed) return {destroy() {}};
      if (options.exerciseId) await openPractice(options.exerciseId);
      else if (options.initialSource) await renderEditor();
      else renderGallery();
    } catch (error) { emitError(error); container.replaceChildren(text(doc, error.message, 'visual-error'), button('Retry', () => mount(container, options), 'visual-primary')); }
    return {destroy() {
      destroyed = true;
      container.replaceChildren();
    }};
  }

  return {mount, clamp, normalizeRect, createPracticeState, safeImageUrl, MAX_MASKS, MAX_UPLOAD_BYTES};
});
