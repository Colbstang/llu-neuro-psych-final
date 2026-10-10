const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../source_viewer.js'),'utf8');
test('same PDF request is shared, successful pages cache, failures can retry',async()=>{
 const start=source.indexOf('async function fetchSourcePage('),end=source.indexOf('\nfunction installSourcePage(',start);
 let calls=0,fail=false;
 const sandbox={sourcePageCache:new Map(),sourcePagePending:new Map(),semanticEndpoint:'/api/course',semanticRequestHeaders:()=>({}),AbortSignal,JSON,Error,fetch:async()=>{calls++;await Promise.resolve();return {ok:!fail,json:async()=>fail?{error:'offline'}:{image:'image',layer:{spans:[]}}};}};
 const context=vm.createContext(sandbox);vm.runInContext(source.slice(start,end),context);
 await Promise.all([context.fetchSourcePage('doc',2,'phrase'),context.fetchSourcePage('doc',2,'phrase')]);assert.equal(calls,1);
 await context.fetchSourcePage('doc',2,'phrase');assert.equal(calls,1);
 fail=true;await assert.rejects(context.fetchSourcePage('doc',3,'phrase'));fail=false;
 await context.fetchSourcePage('doc',3,'phrase');assert.equal(calls,3);
 for(let page=4;page<20;page++)await context.fetchSourcePage('doc',page,'phrase');
 assert.equal(sandbox.sourcePageCache.size,12);assert.equal(sandbox.sourcePagePending.size,0);
});
test('Mehlman is disabled when the current context has no passage',()=>{
 const start=source.indexOf('referenceTabsMarkup=function('),end=source.indexOf('\nasync function openSearchedBook',start);
 const context=vm.createContext({baseReferenceTabs:()=>'<div></div>',ankiContext:{bookMatches:{'First Aid':[],'Pathoma':[]},mehlmanMatches:[],retrieval:'empty'}});
 vm.runInContext(source.slice(start,end),context);
 assert.match(context.referenceTabsMarkup(),/data-search-mehlman disabled/);
 context.ankiContext.mehlmanMatches=[{page:1}];assert.doesNotMatch(context.referenceTabsMarkup(),/data-search-mehlman disabled/);
});
