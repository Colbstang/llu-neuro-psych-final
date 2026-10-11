const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'..','anki_context.js'),'utf8');
function section(start,end){return source.slice(source.indexOf(start),source.indexOf(end));}
function fixture(){
  const note={id:11,kind:'AnKing',cardIds:[101],html:'Synthetic source note',extraHtml:'Synthetic source extra',images:[{file:'synthetic.png',field:'Extra',src:'/synthetic.png'}],tables:[]};
  const other={id:22,kind:'Ty',cardIds:[202],html:'Synthetic unrelated result',images:[],tables:[]};
  const card={id:101,note:11,ord:0},unrelated={id:202,note:22,ord:0};
  const previous={key:'row:synthetic',kind:'row',title:'Synthetic row',topic:'Synthetic topic',topics:['Synthetic topic'],filter:'all',filterText:'',semanticCardIds:[202],matches:[{id:22,score:90,reasons:['Synthetic semantic result']}],referenceSearchFamilies:['first_aid','pathoma']};
  const media={...note.images[0],note,type:'image',caption:'Synthetic media'};
  const context=vm.createContext({ankiContext:previous,ankiReferenceMode:'images',ankiCurrentCard:0,ankiCardRevealed:false,
    ankiCards:new Map([[101,card],[202,unrelated]]),ankiNotes:new Map([[11,note],[22,other]]),state:{ankiMediaPosition:{},ankiCardPosition:{}},renders:0,
    window:{referenceMediaAllowed:()=>true},ankiMediaResults:()=>[media],selectedReferenceFamilies:context=>context.referenceSearchFamilies,
    ankiMakeContext:options=>({...options,key:'note:'+options.fromNote,filter:'all',filterText:'',matches:previous.matches}),
    ankiPlain:text=>String(text||''),ankiRenderClozes:text=>String(text||''),esc:text=>String(text||''),ankiCardLabel:item=>'Synthetic card '+item.card.id,
    ankiCardResults:()=>[{card:unrelated,note:other,reasons:['Wrong semantic result'],score:90}],ankiNeighbors:()=>[],ankiSpecificTags:()=>[],referenceCourseSources:()=>'',
    renderAnkiReference(){context.renders++;context.lastMarkup=context.ankiCardsMarkup();}});
  vm.runInContext(section('function ankiSourceCardResults(','function referenceTabsMarkup('),context);
  vm.runInContext(section('function ankiMediaMarkup(','// Parse balanced clozes'),context);
  vm.runInContext(section('function ankiCardsMarkup(','function renderAnkiReference('),context);
  return {context,note,card,media,previous};
}

test('See source card opens its exact note even when semantic candidates exclude it',()=>{
  const {context,previous}=fixture();
  assert.equal(context.ankiCardResults()[0].card.id,202);
  assert.equal(context.ankiOpenMediaSource(101,11,'Extra','synthetic.png'),true);
  assert.equal(context.ankiCurrentCard,101);assert.equal(context.ankiContext.back,previous);
  assert.equal(context.ankiContext.backReferenceMode,'images');assert.equal(context.ankiCardRevealed,false);
  assert.deepEqual(Array.from(context.ankiContext.referenceSearchFamilies),['first_aid','pathoma']);
  assert.ok(context.lastMarkup.includes('Card 101 · Note 11'));assert.ok(!context.lastMarkup.includes('Card 202 · Note 22'));
  assert.ok(!context.lastMarkup.includes('Related card scope'));
});

test('source button carries exact note and media metadata and disables invalid edges',()=>{
  const {context,card}=fixture();
  let html=context.ankiMediaMarkup();
  assert.ok(html.includes('data-anki-open-card="101"'));assert.ok(html.includes('data-anki-source-note="11"'));assert.ok(html.includes('data-anki-source-field="Extra"'));assert.ok(html.includes('data-anki-source-file="synthetic.png"'));
  card.note=22;html=context.ankiMediaMarkup();assert.ok(html.includes('data-anki-open-card=""'));assert.match(html,/disabled>See source card/);
});

test('wrong note/card/media combinations fail without substituting a ranked result',()=>{
  const {context,previous}=fixture();
  for(const args of [[202,11,'Extra','synthetic.png'],[101,22,'Extra','synthetic.png'],[101,11,'Extra','different.png'],[101,11,'Lecture Notes','synthetic.png']]){
    assert.equal(context.ankiOpenMediaSource(...args),false);assert.equal(context.ankiContext,previous);assert.equal(context.renders,0);
  }
});

test('reference families remain enforced for source cards and fail closed after deselection',()=>{
  const {context,previous}=fixture();
  context.window.referenceMediaAllowed=()=>false;
  assert.equal(context.ankiOpenMediaSource(101,11,'Extra','synthetic.png'),false);assert.equal(context.ankiContext,previous);
  context.window.referenceMediaAllowed=()=>true;assert.equal(context.ankiOpenMediaSource(101,11,'Extra','synthetic.png'),true);
  context.window.referenceMediaAllowed=()=>false;
  assert.equal(context.ankiSourceCardResults().length,0);
  const html=context.ankiCardsMarkup();assert.ok(html.includes('exact source card is unavailable'));assert.ok(!html.includes('Card 202 · Note 22'));
});

test('table provenance opens the correct source note too',()=>{
  const {context,note,media}=fixture();
  note.tables=[{field:'Extra',html:'Synthetic source table'}];media.type='table';delete media.file;
  assert.equal(context.ankiOpenMediaSource(101,11,'Extra',''),true);assert.equal(context.ankiSourceCardResults()[0].note.id,11);
});

test('ordinary related-card references retain their ranked results',()=>{
  const {context}=fixture();assert.equal(context.ankiSourceCardResults(),null);
  const html=context.ankiCardsMarkup();assert.ok(html.includes('Card 202 · Note 22'));assert.ok(html.includes('Related card scope'));
});
