const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'..','semantic_search.js'),'utf8');
const helpers=source.slice(source.indexOf('function selectedReferenceFamilies('),source.indexOf('\nstate.referenceSearchFamilies='));
function context(){const ctx=vm.createContext({state:{referenceSearchFamilies:['first_aid','pathoma','mehlman']},referenceFamilyDefaults:['first_aid','pathoma','mehlman','in_house'],window:{}});vm.runInContext(helpers,ctx);return ctx;}
test('unchecked in-house removes Ty and lecture fields even from a selected book note',()=>{
  const ctx=context(),allows=ctx.window.referenceMediaAllowed;
  assert.equal(allows({note:{kind:'Ty'},field:'First Aid'}),false);
  assert.equal(allows({note:{kind:'NPS',resourceTags:['#FirstAid::Microbiology']},field:'First Aid'}),false);
  assert.equal(ctx.referenceNoteAllowed({kind:'NPS',resourceTags:['#FirstAid::Microbiology']}),false);
  assert.equal(allows({note:{kind:'AnKing',resourceTags:['#FirstAid::Neurology']},field:'Lecture Notes'}),false);
  assert.equal(allows({note:{kind:'AnKing'},field:'First Aid'}),true);
  assert.equal(allows({note:{kind:'AnKing'},field:'Pathoma'}),true);
  assert.equal(allows({note:{kind:'AnKing'},field:'Sketchy'}),false);
  assert.equal(allows({note:{kind:'AnKing'},field:'Extra'}),false);
  ctx.state.referenceSearchFamilies=[];assert.equal(allows({note:{kind:'AnKing'},field:'First Aid'}),false);
});
test('media filters run before duplicate removal and the 40-image limit',()=>{
  const ctx=context(),anki=fs.readFileSync(path.join(__dirname,'..','anki_context.js'),'utf8');
  Object.assign(ctx,{ankiContext:null,ankiTopicMap:{},ankiNotes:new Map([[1,{id:1,kind:'Ty',images:[{file:'same.png',field:'Lecture Notes'}]}],[2,{id:2,kind:'AnKing',images:[{file:'same.png',field:'First Aid'}]}]]),ankiUsefulImage:()=>true,ankiImageScore:()=>1});
  vm.runInContext(anki.slice(anki.indexOf('function ankiMediaResults('),anki.indexOf('\nfunction ankiContextKey(')),ctx);
  const found=ctx.ankiMediaResults({topics:[],matches:[{id:1},{id:2}]});
  assert.equal(found.length,1);assert.equal(found[0].note.id,2);
});
test('cached results cannot display unchecked sources after filter change',()=>{
  const ctx=context();Object.assign(ctx,{sourceResultButton:hit=>hit.title,backgroundResultButton:()=>''});
  vm.runInContext(source.slice(source.indexOf('function sourceSearchResultsMarkup('),source.indexOf('\nfunction narrowLexicalContext(')),ctx);
  const result=ctx.sourceSearchResultsMarkup({bookMatches:{'First Aid':[{title:'allowed'}]},sourceMatches:[{title:'private-lecture'}],searchScope:'all'});
  assert.ok(result.includes('allowed'));assert.ok(!result.includes('private-lecture'));assert.ok(!result.includes('In-house notes / slides'));
});
test('unchecked in-house also hides saved course citation controls',()=>{
  const ctx=context();Object.assign(ctx,{activeRef:'pneumonia',ankiContext:null,sourceLink:()=> 'lecture-page',sourceCitations:()=> 'lecture-notes'});
  ctx.state.referenceSources={pneumonia:{professor_pages:[1],lecture_sources:[]}};
  const panel=fs.readFileSync(path.join(__dirname,'..','reference_panel.js'),'utf8');
  vm.runInContext(panel.slice(panel.indexOf('function referenceCourseSources('),panel.indexOf('\nfunction sourceCitations(')),ctx);
  assert.equal(ctx.referenceCourseSources(),'');
  ctx.state.referenceSearchFamilies.push('in_house');assert.match(ctx.referenceCourseSources(),/lecture-page/);
});
