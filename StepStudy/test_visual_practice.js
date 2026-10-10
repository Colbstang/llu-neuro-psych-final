'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const visual = require('./visual_practice.js');

test('rectangle geometry is finite, bounded, and rejects effectively empty masks', () => {
  assert.deepEqual(visual.normalizeRect({x: .8, y: .7, width: .5, height: .8}),
    {x: .8, y: .7, width: .2, height: .3});
  assert.equal(visual.normalizeRect({x: 0, y: 0, width: .004, height: .2}), null);
  assert.equal(visual.normalizeRect({x: 0, y: 0, width: Infinity, height: .2}), null);
});

test('image URL helper builds only local allowlisted image routes', () => {
  assert.equal(visual.safeImageUrl({kind: 'course_asset', id: 'opaque_1'}),
    'http://localhost/api/visual/image/course_asset/opaque_1');
  assert.equal(visual.safeImageUrl({kind: 'intake_image', id: '../private'}), '');
  assert.equal(visual.safeImageUrl({kind: 'upload', id: 'a'.repeat(32)}, {imageUrl: () => 'https://evil.example/api/visual/image/upload/id'}), '');
  assert.equal(visual.safeImageUrl({kind: 'upload', id: 'a'.repeat(32)}, {imageUrl: () => '//evil.example/api/visual/image/upload/id'}), '');
});

test('practice requires revealing every valid mask before a self-rating is allowed', () => {
  const state = visual.createPracticeState([{id: 'one'}, {id: 'two'}]);
  assert.equal(state.canRate(), false);
  state.reveal('one');
  state.reveal('not-a-mask');
  assert.equal(state.canRate(), false);
  state.reveal('two');
  assert.equal(state.canRate(), true);
  state.revealed.clear();
  assert.equal(state.canRate(), false);
  state.revealAll();
  assert.equal(state.canRate(), true);
});

class FakeClassList {
  constructor(node) { this.node = node; }
  values() { return new Set(String(this.node._className || '').split(/\s+/).filter(Boolean)); }
  add(name) { const values = this.values(); values.add(name); this.node._className = [...values].join(' '); }
  remove(name) { const values = this.values(); values.delete(name); this.node._className = [...values].join(' '); }
  toggle(name, force) {
    const values = this.values(), enabled = force === undefined ? !values.has(name) : Boolean(force);
    if (enabled) values.add(name); else values.delete(name);
    this.node._className = [...values].join(' '); return enabled;
  }
  contains(name) { return this.values().has(name); }
}

class FakeElement {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toLowerCase(); this.ownerDocument = ownerDocument;
    this.children = []; this.listeners = {}; this.attributes = {}; this.dataset = {}; this.style = {};
    this.classList = new FakeClassList(this); this.value = ''; this.disabled = false; this.checked = false;
    this._text = ''; this._className = '';
  }
  set className(value) { this._className = value; }
  get className() { return this._className; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  set textContent(value) { this._text = String(value ?? ''); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...nodes) { for (const value of nodes) { if (value) { const node = typeof value === 'string' ? this.ownerDocument.createTextNode(value) : value; node.parentNode = this; this.children.push(node); } } }
  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(node => node !== this); this.parentNode = null; }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  closest() { return null; }
  setPointerCapture() {}
  getBoundingClientRect() { return {left: 0, top: 0, width: 100, height: 100}; }
  querySelectorAll(selector) {
    const matches = node => selector === 'button' ? node.tagName === 'button' :
      selector === '.visual-mask' ? node.classList.contains('visual-mask') :
      selector === '[data-visual-topic]:checked' ? node.dataset.visualTopic !== undefined && node.checked : false;
    const found = [];
    const visit = node => { for (const child of node.children) { if (matches(child)) found.push(child); visit(child); } };
    visit(this); return found;
  }
  async dispatch(type, event = {}) {
    const fullEvent = {target: this, preventDefault() {}, ...event};
    for (const callback of this.listeners[type] || []) await callback(fullEvent);
  }
  async click() { if (!this.disabled) await this.dispatch('click'); }
}

function fakeRoot() {
  const doc = {createElement: tag => new FakeElement(tag, doc), createTextNode: value => {
    const node = new FakeElement('#text', doc); node.textContent = value; return node;
  }};
  const root = new FakeElement('div', doc); root.ownerDocument = doc; return root;
}

function findNode(root, predicate) {
  if (predicate(root)) return root;
  for (const child of root.children) { const result = findNode(child, predicate); if (result) return result; }
  return null;
}

test('practice reveal enables the rating buttons, and hiding masks disables them again', async () => {
  const root = fakeRoot(); let saved = null;
  const record = {id: 'a'.repeat(32), name: 'Brain landmarks', source: {kind: 'upload', id: 'b'.repeat(32)}, masks: [
    {id: 'one', x: 0, y: 0, width: .2, height: .2, label: 'A', answer: 'Answer A'},
    {id: 'two', x: .3, y: .3, width: .2, height: .2, label: 'B', answer: 'Answer B'},
  ]};
  await visual.mount(root, {exerciseId: record.id, api: {
    listSources: async () => ({sources: [{source: record.source, title: 'Figure'}]}),
    listExercises: async () => ({exercises: []}),
    getExercise: async () => ({exercise: record}),
    recordReview: async (_id, rating) => { saved = rating; return {exercise: {...record, due_at: '2026-10-12T00:00:00Z'}}; },
  }});
  const ratingButtons = root.querySelectorAll('button').filter(node => ['Again', 'Hard', 'Good', 'Easy'].includes(node.textContent));
  assert.equal(ratingButtons.length, 4);
  assert.ok(ratingButtons.every(node => node.disabled));
  const masks = root.querySelectorAll('.visual-mask');
  await masks[0].click();
  assert.ok(ratingButtons.every(node => node.disabled));
  await masks[1].click();
  assert.ok(ratingButtons.every(node => !node.disabled));
  const hide = root.querySelectorAll('button').find(node => node.textContent === 'Hide all');
  await hide.click();
  assert.ok(ratingButtons.every(node => node.disabled));
  const revealAll = root.querySelectorAll('button').find(node => node.textContent === 'Reveal all');
  await revealAll.click();
  assert.ok(ratingButtons.every(node => !node.disabled));
  await ratingButtons[2].click();
  assert.equal(saved, 'good');
});

test('editor retains draft fields and supports keyboard region creation with inferred source topics', async () => {
  const root = fakeRoot(); let submitted;
  const sources = [
    {source: {kind: 'course_asset', id: 'figure-a'}, title: 'Figure A', chapter_id: 'chapter-a', chapter_title: 'Neuroanatomy', topic_ids: ['neuro']},
    {source: {kind: 'course_asset', id: 'figure-b'}, title: 'Figure B', chapter_id: 'chapter-b', chapter_title: 'Psychiatry', topic_ids: ['psychiatry']},
  ];
  await visual.mount(root, {topics: [{id: 'neuro', label: 'Neuro'}, {id: 'psychiatry', label: 'Psychiatry'}], api: {
    listSources: async () => ({sources}), listExercises: async () => ({exercises: []}),
    createExercise: async payload => { submitted = payload; return {exercise: {id: 'c'.repeat(32), ...payload}}; },
  }});
  await root.querySelectorAll('button').find(node => node.textContent === 'Create exercise').click();
  const name = findNode(root, node => node.tagName === 'input' && node.type === 'text');
  name.value = 'Posterior fossa'; await name.dispatch('input');
  const chapters = findNode(root, node => node.tagName === 'select' && node.id === 'visual-chapter');
  chapters.value = 'chapter-a'; await chapters.dispatch('change');
  const source = findNode(root, node => node.tagName === 'select' && node.id === 'visual-source');
  source.value = '1'; await source.dispatch('change');
  await root.querySelectorAll('button').find(node => node.textContent === 'Add region').click();
  const leftCoordinate = findNode(root, node => node.tagName === 'input' && node.getAttribute('aria-label') === 'Left percent');
  assert.ok(leftCoordinate, 'coordinate editor should be available without pointer drawing');
  leftCoordinate.value = '15'; await leftCoordinate.dispatch('change');
  assert.equal(findNode(root, node => node.tagName === 'input' && node.type === 'text').value, 'Posterior fossa');
  assert.equal(findNode(root, node => node.tagName === 'select' && node.id === 'visual-chapter').value, 'chapter-a');
  assert.equal(findNode(root, node => node.tagName === 'select' && node.id === 'visual-source').value, '1');
  const topicChecks = root.querySelectorAll('[data-visual-topic]:checked');
  assert.deepEqual(topicChecks.map(node => node.value), ['psychiatry']);
  const filter = findNode(root, node => node.tagName === 'input' && node.type === 'search');
  filter.value = 'Figure A'; await filter.dispatch('input');
  assert.equal(findNode(root, node => node.tagName === 'select' && node.id === 'visual-source').value, '1');
  const form = findNode(root, node => node.tagName === 'form');
  await form.dispatch('submit');
  assert.equal(submitted.name, 'Posterior fossa');
  assert.equal(submitted.source.id, 'figure-b');
  assert.deepEqual(submitted.topic_ids, ['psychiatry']);
  assert.equal(submitted.chapter_id, 'chapter-a');
  assert.equal(submitted.masks.length, 1);
  assert.equal(submitted.masks[0].x, .15);
});

test('uncertain review retries the same rating and request ID only', async () => {
  const root = fakeRoot(), calls = [], committed = new Set(); let commitCount = 0;
  const record = {id: 'd'.repeat(32), name: 'One region', source: {kind: 'upload', id: 'e'.repeat(32)}, masks: [
    {id: 'only-mask', x: .1, y: .1, width: .3, height: .3, label: 'Region', answer: 'Answer'},
  ]};
  await visual.mount(root, {exerciseId: record.id, api: {
    listSources: async () => ({sources: [{source: record.source, title: 'Figure', chapter_id: 'private-id'}]}),
    listExercises: async () => ({exercises: []}), getExercise: async () => ({exercise: record}),
    recordReview: async (_id, rating, requestId) => {
      calls.push({rating, requestId});
      const key = requestId + ':' + rating;
      if (!committed.has(key)) { committed.add(key); commitCount++; throw new Error('Connection lost after submit'); }
      return {ok: true, duplicate: true, exercise: {...record, due_at: '2026-10-12T00:00:00Z'}};
    },
  }});
  assert.ok(!root.textContent.includes('private-id'), 'chapter IDs stay internal');
  await root.querySelectorAll('button').find(node => node.textContent === 'Reveal all').click();
  let buttons = root.querySelectorAll('button');
  const good = buttons.find(node => node.textContent === 'Good');
  const again = buttons.find(node => node.textContent === 'Again');
  await good.click();
  assert.equal(good.textContent, 'Retry Good');
  assert.equal(good.disabled, false);
  assert.equal(again.disabled, true);
  await root.querySelectorAll('button').find(node => node.textContent === 'Hide all').click();
  await root.querySelectorAll('button').find(node => node.textContent === 'Reveal all').click();
  buttons = root.querySelectorAll('button');
  assert.equal(buttons.find(node => node.textContent === 'Retry Good').disabled, false);
  assert.equal(buttons.find(node => node.textContent === 'Again').disabled, true);
  await buttons.find(node => node.textContent === 'Retry Good').click();
  assert.equal(calls.length, 2);
  assert.equal(calls[0].rating, 'good');
  assert.equal(calls[1].rating, 'good');
  assert.equal(calls[0].requestId, calls[1].requestId);
  assert.equal(commitCount, 1);
  assert.equal(root.querySelectorAll('button').find(node => node.textContent === 'Saved: Good').disabled, true);
});
