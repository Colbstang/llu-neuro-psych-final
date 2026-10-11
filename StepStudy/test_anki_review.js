const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

class Node{
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this._text='';this.isConnected=true;}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(node=>node.textContent).join(' ');}
  set innerHTML(_value){throw new Error('Raw card HTML must never be used');}
  append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node);}}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
  setAttribute(key,value){this.attributes[key]=String(value);}
  remove(){this.isConnected=false;if(this.parent)this.parent.children=this.parent.children.filter(node=>node!==this);}
  all(){return [this,...this.children.flatMap(node=>node.all())];}
  closest(){return ['input','textarea','select','button','a'].includes(this.tagName)?this:null;}
}
const clone=value=>JSON.parse(JSON.stringify(value));
async function fixture({lostResponse=false,unknown=false}={}){
  const listeners=new Map(),requests=[],confirmed=[],done=[],errors=[],host=new Node('main');
  let session=null,writes=0,uuid=0,lost=lostResponse,historyVisible=!unknown,unavailableCardId=null;
  const receipts=new Map();
  const window={crypto:{randomUUID:()=> 'synthetic-rating-'+(++uuid)}};
  const document={createElement:tag=>new Node(tag),addEventListener:(key,fn)=>listeners.set(key,fn),removeEventListener:(key,fn)=>{if(listeners.get(key)===fn)listeners.delete(key);}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/anki_review.js','utf8'),{window,document,URLSearchParams,Number,Set,Error});
  function advance(){session.position++;session.current=null;if(session.position===session.total)session.status='finished';}
  function currentCard(){return {card_id:1001+session.position,front:{text:'Masked front [...]',images:[{src:'https://invalid.test/image.png',alt:'remote'},{src:'data:image/png;base64,c3ludGhldGlj',alt:'Local synthetic diagram'}]},back:session.current?.revealed?{text:'REVEALED_SECRET',images:[]}:null};}
  async function api(path,body){
    requests.push(clone({path,body:body||null}));
    if(!body)return {ok:true,session:clone(session)};
    if(body.action==='start'){
      if(!session||session.status==='finished')session={id:'synthetic-session',topic_id:'neuro',total:2,position:0,status:'active',confirmed_count:0,skipped_count:0,unknown_count:0,current:null};
      return {ok:true,session:clone(session)};
    }
    if(body.action==='open'){
      if(1001+session.position===unavailableCardId)throw new Error('Synthetic card unavailable');
      session.current=session.current||{card_id:1001+session.position,revealed:false,request_id:null,ease:null};return {ok:true,session:clone(session),card:currentCard()};
    }
    if(body.action==='reveal'){session.current.revealed=true;return {ok:true,session:clone(session),card:currentCard()};}
    if(body.action==='skip'){assert.ok(!session.current?.request_id);session.skipped_count++;advance();return {ok:true,session:clone(session)};}
    if(body.action==='dismiss'){
      const review=receipts.get(body.request_id);assert.ok(review);
      if(!review.confirmed&&!review.dismissed){assert.equal(session.current.request_id,body.request_id);review.dismissed=true;session.unknown_count++;advance();}
      return {ok:true,session:clone(session),review:clone(review)};
    }
    if(body.action==='rate'){
      let review=receipts.get(body.request_id);
      if(!review){assert.equal(session.current.revealed,true);writes++;session.current.request_id=body.request_id;session.current.ease=body.ease;review={confirmed:false,status:'unknown',request_id:body.request_id,card_id:session.current.card_id};receipts.set(body.request_id,review);}
      if(historyVisible&&!review.confirmed){review.confirmed=true;review.status='confirmed';review.review_id=1800000000000;session.confirmed_count++;session.last_confirmed_request_id=body.request_id;advance();}
      if(lost){lost=false;throw new Error('Synthetic lost HTTP response');}
      return {ok:true,session:clone(session),review:clone(review)};
    }
    throw new Error('Unexpected action');
  }
  const options={topicId:'neuro',api,onError:value=>errors.push(value),onConfirmed:value=>confirmed.push(value),onDone:value=>done.push(value)};
  const controller=await window.StepAnkiReview.mount(host,options);
  return {host,controller,requests,confirmed,done,errors,listeners,window,api,options,get writes(){return writes;},set historyVisible(value){historyVisible=value;},set unavailableCardId(value){unavailableCardId=value;}};
}
function find(host,tag,text){const node=host.all().find(node=>node.tagName===tag&&(text===undefined||node.textContent===text));assert.ok(node,`Expected ${tag}: ${text}`);return node;}
async function click(node){assert.notEqual(node.disabled,true);await node.onclick();}
async function start(state){await click(find(state.host,'button','Start Anki batch'));}

test('answer reveal is deliberate and only explicit confirmed ratings count',async()=>{
  const state=await fixture();assert.equal(state.writes,0);assert.equal(state.confirmed.length,0);
  await start(state);assert.equal(state.writes,0);assert.equal(state.host.textContent.includes('REVEALED_SECRET'),false);
  assert.equal(state.host.all().filter(node=>node.tagName==='img').length,1);
  assert.equal(state.host.all().find(node=>node.tagName==='img').alt,'Local synthetic diagram');
  assert.equal(state.host.all().some(node=>node.textContent==='Good · 3'),false);
  await click(find(state.host,'button','Show answer · Space'));
  assert.ok(state.host.textContent.includes('REVEALED_SECRET'));assert.equal(state.writes,0);
  await click(find(state.host,'button','Good · 3'));
  assert.equal(state.writes,1);assert.equal(state.confirmed.length,1);assert.equal(state.controller.session.confirmed_count,1);
  assert.equal(state.controller.session.position,1);assert.equal(state.host.textContent.includes('REVEALED_SECRET'),false);
  assert.equal(state.requests.find(row=>row.body?.action==='rate').body.ease,3);
  state.controller.destroy();assert.equal(state.listeners.has('keydown'),false);
});

test('lost HTTP response keeps the same request and rating for safe checking',async()=>{
  const state=await fixture({lostResponse:true});await start(state);await click(find(state.host,'button','Show answer · Space'));
  await click(find(state.host,'button','Hard · 2'));
  assert.equal(state.writes,1);assert.equal(state.confirmed.length,0);assert.ok(state.host.textContent.includes('Synthetic lost HTTP response'));
  assert.equal(find(state.host,'button','Skip without rating').disabled,true);
  await click(find(state.host,'button','Check this rating again'));
  const posts=state.requests.filter(row=>row.body?.action==='rate');assert.equal(posts.length,2);assert.deepEqual(posts[0].body,posts[1].body);
  assert.equal(state.writes,1);assert.equal(state.confirmed.length,1);assert.equal(state.controller.session.confirmed_count,1);
});

test('unknown history remains uncounted until a later read-only confirmation',async()=>{
  const state=await fixture({unknown:true});await start(state);await click(find(state.host,'button','Show answer · Space'));
  await click(find(state.host,'button','Again · 1'));assert.equal(state.controller.session.confirmed_count,0);assert.equal(state.confirmed.length,0);assert.equal(state.writes,1);
  assert.ok(state.host.textContent.includes('not confirmed'));assert.equal(find(state.host,'button','Skip without rating').disabled,true);
  await click(find(state.host,'button','Check this rating again'));assert.equal(state.writes,1);assert.equal(state.confirmed.length,0);
  state.historyVisible=true;await click(find(state.host,'button','Check this rating again'));
  assert.equal(state.writes,1);assert.equal(state.confirmed.length,1);
});

test('explicitly leaving an uncertain outcome continues with no review count or repeated write',async()=>{
  const state=await fixture({unknown:true});await start(state);await click(find(state.host,'button','Show answer · Space'));
  await click(find(state.host,'button','Again · 1'));assert.equal(state.writes,1);assert.equal(state.confirmed.length,0);
  await click(find(state.host,'button','Leave unconfirmed and continue'));
  assert.equal(state.controller.session.position,1);assert.equal(state.controller.session.unknown_count,1);assert.equal(state.controller.session.confirmed_count,0);
  assert.equal(state.writes,1);assert.equal(state.confirmed.length,0);assert.ok(state.host.textContent.includes('1 unconfirmed'));assert.equal(state.host.textContent.includes('REVEALED_SECRET'),false);
  const dismissed=state.requests.find(row=>row.body?.action==='dismiss').body,attempt=state.requests.find(row=>row.body?.action==='rate').body;
  assert.equal(dismissed.request_id,attempt.request_id);
});

test('resumed unconfirmed rating can be left pending when its card cannot be reopened',async()=>{
  const state=await fixture({unknown:true});await start(state);await click(find(state.host,'button','Show answer · Space'));
  await click(find(state.host,'button','Hard · 2'));
  const requestId=state.controller.session.current.request_id;
  assert.equal(state.writes,1);state.unavailableCardId=1001;state.controller.destroy();
  const resumed=await state.window.StepAnkiReview.mount(state.host,state.options);
  assert.ok(state.host.textContent.includes('Synthetic card unavailable'));
  assert.ok(state.host.textContent.includes('previous rating is still unconfirmed'));
  assert.equal(state.host.all().some(node=>node.tagName==='button'&&node.textContent==='Skip without rating'),false);
  await click(find(state.host,'button','Retry opening card'));
  assert.equal(state.writes,1);assert.equal(resumed.session.current.request_id,requestId);
  await click(find(state.host,'button','Leave unconfirmed and continue'));
  assert.equal(resumed.session.position,1);assert.equal(resumed.session.unknown_count,1);assert.equal(resumed.session.confirmed_count,0);
  assert.equal(state.writes,1);assert.equal(state.confirmed.length,0);
  assert.equal(state.requests.filter(row=>row.body?.action==='rate').length,1);
  assert.equal(state.requests.find(row=>row.body?.action==='dismiss').body.request_id,requestId);
  assert.ok(state.host.textContent.includes('Masked front [...]'));resumed.destroy();
});

test('leaving after a lost response still counts a receipt already confirmed by Anki',async()=>{
  const state=await fixture({lostResponse:true});await start(state);await click(find(state.host,'button','Show answer · Space'));
  await click(find(state.host,'button','Good · 3'));assert.equal(state.confirmed.length,0);assert.equal(state.writes,1);
  await click(find(state.host,'button','Leave unconfirmed and continue'));
  assert.equal(state.controller.session.confirmed_count,1);assert.equal(state.controller.session.unknown_count,0);assert.equal(state.confirmed.length,1);assert.equal(state.writes,1);
});

test('skipping and remounting preserve position without inventing a review',async()=>{
  const state=await fixture();await start(state);await click(find(state.host,'button','Skip without rating'));
  assert.equal(state.controller.session.skipped_count,1);assert.equal(state.writes,0);
  state.controller.destroy();
  const resumed=await state.window.StepAnkiReview.mount(state.host,state.options);
  assert.equal(resumed.session.position,1);assert.equal(resumed.session.confirmed_count,0);assert.equal(state.writes,0);
  await click(find(state.host,'button','Skip without rating'));
  assert.equal(resumed.session.status,'finished');assert.equal(state.done.length,1);assert.equal(state.done[0].confirmed_count,0);assert.equal(state.confirmed.length,0);
  resumed.destroy();
});

test('Space and 1–4 work outside fields and never rate unrevealed cards',async()=>{
  const state=await fixture();await start(state);let prevented=0;
  const key=(key,code,target=new Node('div'))=>state.listeners.get('keydown')({key,code,target,preventDefault(){prevented++;}});
  await key('3','Digit3');assert.equal(state.writes,0);assert.equal(prevented,0);
  await key(' ','Space',new Node('textarea'));assert.equal(state.host.textContent.includes('REVEALED_SECRET'),false);
  await key(' ','Space');assert.ok(state.host.textContent.includes('REVEALED_SECRET'));assert.equal(state.writes,0);
  await key('4','Digit4');assert.equal(state.writes,1);assert.equal(state.requests.find(row=>row.body?.action==='rate').body.ease,4);assert.equal(prevented,2);
});

test('arbitrary media URLs are excluded',()=>{
  const {safeMedia}=require('./anki_review.js');
  assert.equal(safeMedia('https://example.test/remote.png'),false);assert.equal(safeMedia('javascript:alert(1)'),false);assert.equal(safeMedia('data:text/html;base64,c3ludGhldGlj'),false);assert.equal(safeMedia('data:image/png;base64,c3ludGhldGlj'),true);
});
