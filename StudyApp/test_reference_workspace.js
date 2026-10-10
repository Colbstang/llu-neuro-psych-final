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

const listenStart=source.indexOf('function referenceListenTerm(){');
const listenEnd=source.indexOf('\nfunction referenceSetupControls',listenStart);
assert.ok(listenStart>=0&&listenEnd>listenStart,'real speech query helper is present');
let selection='',query='myasthenia gravis';
const speech=vm.createContext({
  referenceTermContext:'References',referenceLastTerm:'References',sourceViewerActive:null,ankiContext:null,
  window:{getSelection:()=>({toString:()=>selection})},
  $:selector=>selector==='#side-title'?{textContent:'References'}:
    selector==='#reference-search-host [data-reference-search-query]'?{value:query}:null,
});
vm.runInContext(source.slice(listenStart,listenEnd),speech);
assert.equal(vm.runInContext('referenceListenTerm()',speech),'myasthenia gravis','Listen uses the typed term, not the References heading');
selection='dysmetria';
assert.equal(vm.runInContext('referenceListenTerm()',speech),'dysmetria','an explicit selection wins over a broader query');
selection='';query='';
assert.equal(vm.runInContext('referenceListenTerm()',speech),'','the empty reference library does not pronounce UI chrome');
console.log('Reference speech term selection tests passed.');

// Return from a PDF/book view without starting a new search or changing its
// query, selected source families, or the guide's reading position.
const returnStart=source.indexOf('function referenceShowSearchResults(){');
const returnEnd=source.indexOf('\nfunction openReferenceWorkspace',returnStart);
const searchContext={key:'selection:association fibers',topic:'',searchQuery:'association fibers',searchScope:'sources',referenceSearchFamilies:['in_house'],retrieval:'empty',sourceMatches:[{documentId:'lecture',page:14}]};
const sideContent={scrollTop:1400};
let renders=0,searches=0;
const navigation=vm.createContext({
  sourceViewerSerial:6,sourceViewerActive:{document:{id:'lecture'},result:{page:15}},
  activeRef:'source:lecture',ankiReferenceMode:'books',bookMarkMode:true,
  ankiContext:searchContext,referenceResultsPosition:{context:searchContext,scrollTop:350},
  renderAnkiReference:options=>{assert.equal(options.keepScroll,false);renders++;sideContent.scrollTop=0;},
  fetchSemanticCards:()=>searches++,$:()=>sideContent,
});
vm.runInContext(source.slice(returnStart,returnEnd),navigation);
vm.runInContext('referenceShowSearchResults()',navigation);
assert.equal(navigation.sourceViewerSerial,7,'Back invalidates an unfinished PDF request');
assert.equal(navigation.sourceViewerActive,null);
assert.equal(navigation.ankiReferenceMode,'cards','Back leaves book mode so it renders the results');
assert.equal(navigation.activeRef,'');
assert.equal(sideContent.scrollTop,350,'results return to their own scroll position');
assert.equal(renders,1);
assert.equal(searches,0,'completed results are reused without fetching again');
assert.deepEqual(searchContext.referenceSearchFamilies,['in_house']);
assert.equal(searchContext.searchQuery,'association fibers');
assert.equal(searchContext.sourceMatches[0].page,14);
navigation.ankiContext={...searchContext,key:'other',retrieval:'loading'};
vm.runInContext('referenceShowSearchResults()',navigation);
assert.equal(sideContent.scrollTop,0,'a different search does not inherit the prior results scroll');
assert.equal(searches,1,'a cancelled pending search resumes on return');
navigation.ankiContext=null;
vm.runInContext('referenceShowSearchResults()',navigation);
assert.equal(renders,2,'no prior search is a no-op');
console.log('Reference return navigation tests passed.');
