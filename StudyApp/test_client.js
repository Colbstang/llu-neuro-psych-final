// Regression tests for the public app's real merge, diff, and flush functions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const workspace = fs.readFileSync(path.join(__dirname, '..', 'reference_workspace.js'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, 'study_app.js'), 'utf8');
function between(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker), end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `expected source region ${startMarker}`);
  return source.slice(start, end);
}
const mergeFunctions = between(workspace, 'function referenceClone(', '\nlet referenceStateSnapshot=');
const referenceHelpers = between(workspace, 'function referenceClone(', '\nlet referenceStateSnapshot=');
const diffFunction = between(app, 'function studyAppCopy(', '\nfunction studyAppStatus(');
const flushFunction = between(app, 'async function studyAppFlush(){', '\nasync function studyAppRefresh(');

const shared = vm.createContext({ JSON, Object, Array, Map, Set, Promise, Error, String, clearTimeout() {}, setTimeout() { return 0; } });
vm.runInContext(`${mergeFunctions}\n${diffFunction}`, shared);
assert.equal(vm.runInContext("referenceEqual({a:1,b:{x:2}},{b:{x:2},a:1})", shared), true,
  'object insertion order does not create a semantic difference');
assert.equal(vm.runInContext("referenceEqual([{id:'a'},{id:'b'}],[{id:'b'},{id:'a'}])", shared), false,
  'array order remains meaningful');
assert.equal(vm.runInContext("studyAppDiff({good:{},items:[{id:'x',a:1,b:2}]},{good:{},items:[{b:2,id:'x',a:1}]})", shared), undefined,
  'server key sorting within stable-ID array items produces no patch');
assert.equal(vm.runInContext("studyAppDiff({items:[{id:'x'},{id:'y'}]},{items:[{id:'y'},{id:'x'}]})", shared), undefined,
  'stable-ID array order alone does not emit a useless empty delta');
assert.deepEqual(JSON.parse(vm.runInContext("JSON.stringify(studyAppDiff({items:[{id:'x',a:1}]},{items:[{id:'x',a:2},{id:'y'}]}))", shared)), {
  items: { __llu_array_delta__: { remove: [], upsert: [{ id: 'x', a: 2 }, { id: 'y' }] } },
}, 'meaningful stable-ID edits still produce compact array deltas');

async function runFlush(initialState, mutate, expectedPosts) {
  const counters = { posts: 0, status: '' };
  const sortKeys = value => Array.isArray(value) ? value.map(sortKeys) : value && typeof value === 'object'
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sortKeys(value[key])])) : value;
  let harness;
  harness = vm.createContext({
    JSON, Object, Array, Map, Set, Promise, Error, String, sortKeys,
    state: JSON.parse(JSON.stringify(initialState)),
    studyAppBaseline: JSON.parse(JSON.stringify(initialState)),
    studyAppReady: true, studyAppSaving: false, studyAppRevision: 7, studyAppTimer: null,
    studyAppCopy(value) { return JSON.parse(JSON.stringify(value)); },
    studyAppBrowserSave() {},
    studyAppStatus(value) { counters.status = value; },
    clearTimeout() {}, setTimeout() { return 0; },
    async studyAppRequest(_route, body) {
      counters.posts++;
      assert.ok(body.patch && Object.keys(body.patch).length, 'flush never submits an empty patch');
      return { state: sortKeys(vm.runInContext('state', harness)), revision: 8 };
    },
  });
  vm.runInContext(`${mergeFunctions}\n${diffFunction}\n${flushFunction}`, harness);
  if (mutate) vm.runInContext(mutate, harness);
  await vm.runInContext('studyAppFlush()', harness);
  assert.equal(counters.posts, expectedPosts, 'flush stops after semantic state matches the sorted server response');
  assert.equal(counters.status, 'Saved to app');
}

function makeStartupClient(fetchRoute) {
  const browser = { calls: [], dashboards: [], listeners: [] };
  const status = { textContent: '' }, importControl = {}, button = { textContent: '' }, ankiStatus = { innerHTML: '' };
  const elements = new Map([
    ['#save-status', status], ['#import', importControl], ['#export', {}],
    ['#study-app-button', button], ['[data-app-anki-status]', ankiStatus],
  ]);
  let context;
  const sandbox = {
    window: { STUDY_APP_CONFIG: { apiBase: '/api', token: 'fake-token' }, addEventListener() {} },
    document: {
      body: { classList: { add() {}, remove() {} } },
      createElement: tag => ({ tag, id: '', textContent: '' }),
      querySelector: selector => selector === '.toolbar' ? { insertBefore() {} } : elements.get(selector) || null,
      addEventListener(event, callback) { browser.listeners.push({ event, callback }); },
    },
    location: { hash: '#dashboard', search: '' },
    localStorage: { setItem() {}, getItem() { return null; } },
    state: { view: 'dashboard', good: {}, notes: {} }, currentView: 'dashboard', KEY: 'public-test',
    save() {}, render() {
      if (context.currentView === 'dashboard' && typeof context.renderStudyDashboard === 'function') {
        context.renderStudyDashboard();
      }
    },
    renderStudyDashboard() {
      const coverage = vm.runInContext('studyAppCoverage', context), card = coverage.cards?.['101'];
      browser.dashboards.push(card ? `${card.recent_again_count} Again` : 'not synced');
    },
    renderAnkiReference() {}, ankiStepCard() {}, ensureScopePage() {},
    referenceStateSnapshot: {}, referenceEqual: (a, b) => JSON.stringify(a) === JSON.stringify(b),
    referenceObject: value => value !== null && typeof value === 'object' && !Array.isArray(value),
    referenceClone: value => value === undefined ? undefined : JSON.parse(JSON.stringify(value)),
    esc: value => String(value),
    $: selector => elements.get(selector) || null,
    fetch: async (url, options = {}) => {
      const entry = { url, options, body: options.body ? JSON.parse(options.body) : undefined };
      browser.calls.push(entry);
      const value = await fetchRoute(entry);
      return { ok: true, json: async () => value };
    },
    AbortController, setTimeout: () => 0, clearTimeout() {}, setInterval() {},
    JSON, Object, Array, Map, Set, Promise, String, Number, Error, Date, Math, console,
  };
  context = vm.createContext(sandbox);
  vm.runInContext(referenceHelpers, context, { filename: 'reference_workspace.js' });
  vm.runInContext(app, context, { filename: 'StudyApp/study_app.js' });
  return { context, browser, button };
}

async function testCachedCoverageRefreshesDashboardWithoutSync() {
  const cached = {
    summary: { synced_at: 123, recent_window_days: 30, review_count: 9 },
    cards: { '101': { review_count: 4, recent_review_count: 3, recent_again_count: 2, recent_hard_count: 1 } },
  };
  const client = makeStartupClient(async entry => {
    if (entry.url === '/api/progress') return { exists: true, revision: 2, state: { view: 'dashboard', good: {}, notes: {} } };
    if (entry.url === '/api/anki/status') return { available: true, anki_connect_version: 6, coverage: cached };
    throw new Error(`Unexpected client route ${entry.url}`);
  });

  await client.context.startStudyApp();

  assert.deepEqual(client.browser.dashboards, ['not synced', '2 Again'],
    'startup refreshes the dashboard after loading its cached Anki coverage');
  assert.equal(client.browser.calls.filter(entry => entry.url === '/api/anki/status').length, 1);
  assert.equal(client.browser.calls.filter(entry => entry.url === '/api/anki/sync').length, 0,
    'displaying cached coverage does not trigger a new review-history sync');
}

(async () => {
  const initial = { good: {}, notes: {}, annotations: [{ id: 'h1', text: 'x', color: 'yellow' }] };
  await runFlush(initial, "state={annotations:[{color:'yellow',id:'h1',text:'x'}],notes:{},good:{}}", 0);
  await runFlush(initial, "state.good['section-1']=true", 1);
  await testCachedCoverageRefreshesDashboardWithoutSync();
  console.log('Public study app source merge/flush and cached-coverage tests passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
