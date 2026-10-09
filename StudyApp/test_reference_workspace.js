// Exercise the real public workspace's three-way merge without a browser DOM.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'reference_workspace.js'), 'utf8');
const start = source.indexOf('function referenceClone(');
const end = source.indexOf('\nsave=function(){', start);
assert.ok(start >= 0 && end > start, 'public workspace merge functions are present');
const sandbox = {
  state: { view: 'guide', page: 1, good: { a: false }, notes: { section: 'old' },
    highlights: [{ id: 'h1', text: 'first' }] },
  referenceOnly: false,
  referenceMainFields: new Set(['view', 'page', 'scope']),
  save() {},
  window: {},
  JSON,
  Set,
  Map,
  Array,
  Object,
};
const context = vm.createContext(sandbox);
vm.runInContext(source.slice(start, end), context, { filename: 'reference_workspace.js' });
vm.runInContext("state.good.a=true;state.highlights.push({id:'h2',text:'local'})", context);
vm.runInContext("referenceMergeIncoming({view:'questions',page:4,good:{a:false,b:true},notes:{section:'server'},highlights:[{id:'h1',text:'first'},{id:'h3',text:'server'}]})", context);
assert.deepEqual(JSON.parse(JSON.stringify(context.state)), {
  view: 'guide', page: 1,
  good: { a: true, b: true },
  notes: { section: 'server' },
  highlights: [{ id: 'h1', text: 'first' }, { id: 'h3', text: 'server' }, { id: 'h2', text: 'local' }],
}, 'incoming independent state merges while concurrent local edits and navigation stay intact');
assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(referenceStateSnapshot)', context)), {
  view: 'questions', page: 4, good: { a: false, b: true }, notes: { section: 'server' },
  highlights: [{ id: 'h1', text: 'first' }, { id: 'h3', text: 'server' }],
}, 'workspace advances its remote baseline after merging');
console.log('Public reference workspace merge tests passed.');
