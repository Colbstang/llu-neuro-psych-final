const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

class Node {
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.listeners={};this._text='';}
  set textContent(text){this._text=String(text);this.children=[];}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join(' ');}
  append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
  replaceChildren(...children){this.children=[];this._text='';this.append(...children);}
  setAttribute(name,value){this.attributes[name]=String(value);}
  addEventListener(name,callback){this.listeners[name]=callback;}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);}
  all(){return [this,...this.children.flatMap(child=>child.all())];}
}
function fixture(apiOverride){
  const window={crypto:{randomUUID:()=> 'synthetic-retry-uuid'}},document={createElement:tag=>new Node(tag)},requests=[];
  vm.runInNewContext(fs.readFileSync(__dirname+'/repair_practice.js','utf8'),{window,document,URLSearchParams,Date,Math,Number});
  const question={id:8,outcome:'wrong',full_text:'SECRET_ORIGINAL_OCR',stem:'SECRET_STEM',explanation:'SECRET_EXPLANATION',topic_ids:['neuro','psychiatry'],
    repair:{confidence:'high',reason_fooled_me:'SECRET_REASON',missing_distinction:'SECRET_DISTINCTION',correction:'SECRET_CORRECTION',source_reference:'SECRET_SOURCE',follow_up_prompt:'What distinction changes the answer?',retry_count:0}};
  const history=[];
  async function api(path,body){requests.push({path,body});if(apiOverride){const result=await apiOverride(path,body,requests.length);if(result)return result;}
    if(body?.action==='save')Object.assign(question.repair,body.repair);
    if(body?.action==='retry'){history.push({answer:body.answer,self_result:body.self_result,attempted_at:1700000000});question.repair.retry_count=history.length;}
    return {ok:true,question,repair:{...question.repair,history:[...history]}};
  }
  const host=new Node('main'),changes=[],saved=[];
  const controller=window.StepRepair.mount(host,{question,api,onModeChange:state=>changes.push(state),onSaved:result=>saved.push(result)});
  return {host,controller,requests,changes,saved,question};
}
function find(host,tag,text){const node=host.all().find(node=>node.tagName===tag&&(text===undefined||node.textContent===text));assert.ok(node,`Expected ${tag} ${text||''}`);return node;}
async function click(node){assert.notEqual(node.disabled,true,'Control should be enabled');await node.onclick();}

test('repair panels load only on demand and retry hides all source before deliberate reveal',async()=>{
  const state=fixture();
  assert.equal(state.requests.length,0);
  assert.equal(state.host.textContent.includes('SECRET_'),false);
  const opening=state.controller.open('retry');
  assert.equal(state.changes[0].mode,'retry');
  assert.equal(state.changes[0].sourceRevealed,false);
  await opening;
  assert.equal(state.requests[0].path,'/api/question-repair?id=8');
  assert.equal(state.host.textContent.includes('SECRET_'),false);
  const reveal=find(state.host,'button','Reveal source & correction');
  assert.equal(reveal.disabled,true);
  const answer=find(state.host,'textarea');answer.value='My independent answer';answer.oninput();
  await click(reveal);
  assert.ok(state.host.textContent.includes('SECRET_ORIGINAL_OCR'));
  assert.ok(state.host.textContent.includes('SECRET_CORRECTION'));
  const revealedAnswer=find(state.host,'textarea');assert.equal(revealedAnswer.readOnly,true);assert.equal(revealedAnswer.value,'My independent answer');
  await click(find(state.host,'button','Recalled'));
  const posted=state.requests.find(item=>item.body?.action==='retry').body;
  assert.deepEqual(JSON.parse(JSON.stringify(posted)),{id:8,action:'retry',answer:'My independent answer',self_result:'recalled',request_id:'synthetic-retry-uuid'});
  assert.equal(state.question.outcome,'wrong');
  assert.equal(state.saved.length,1);
  assert.ok(state.host.textContent.includes('Original outcome: wrong'));
});

test('uncertain save retains the answer and reuses the same rating and request ID',async()=>{
  let attempts=0;
  const state=fixture(async(path,body)=>{if(body?.action==='retry'&&++attempts===1)throw new Error('Synthetic network interruption');});
  await state.controller.open('retry');
  const answer=find(state.host,'textarea');answer.value='Answer before reveal';answer.oninput();
  await click(find(state.host,'button','Reveal source & correction'));
  await click(find(state.host,'button','Needs practice'));
  assert.equal(state.saved.length,0);
  assert.ok(state.host.textContent.includes('Synthetic network interruption'));
  assert.equal(find(state.host,'textarea').value,'Answer before reveal');
  await click(find(state.host,'button','Retry saving Needs practice'));
  const posts=state.requests.filter(item=>item.body?.action==='retry');
  assert.equal(posts.length,2);
  assert.deepEqual(JSON.parse(JSON.stringify(posts[0].body)),JSON.parse(JSON.stringify(posts[1].body)));
  assert.equal(state.saved.length,1);
  assert.equal(state.question.outcome,'wrong');
});

test('repair notes save user wording and source reference without grading or changing links',async()=>{
  const state=fixture();await state.controller.open('repair');
  const input=state.host.all().find(node=>node.id?.endsWith('-reason_fooled_me'));input.value='My own wording';input.oninput();
  await click(find(state.host,'button','Save repair notes'));
  const posted=state.requests.find(item=>item.body?.action==='save').body;
  assert.equal(posted.repair.reason_fooled_me,'My own wording');
  assert.equal(posted.repair.source_reference,'SECRET_SOURCE');
  assert.equal('grade' in posted,false);
  assert.equal('outcome' in posted,false);
  assert.deepEqual(state.question.topic_ids,['neuro','psychiatry']);
  assert.equal(state.question.outcome,'wrong');
});

test('native details toggle during a pending load does not replace retry with source notes',async()=>{
  let release;
  const pending=new Promise(resolve=>release=resolve);
  const state=fixture(async(path,body)=>{if(!body)await pending;});
  const opening=state.controller.open('retry');
  state.controller.root.listeners.toggle();
  assert.equal(state.requests.length,1);
  release();await opening;
  assert.equal(state.host.textContent.includes('SECRET_'),false);
  assert.ok(find(state.host,'button','Reveal source & correction'));
  state.controller.root.open=false;state.controller.root.listeners.toggle();
  state.controller.root.open=true;state.controller.root.listeners.toggle();
  assert.equal(state.changes.at(-1).mode,'retry');
  assert.equal(state.changes.at(-1).sourceRevealed,false);
});

test('failed saving while switching to retry keeps editable repair notes and navigation',async()=>{
  const state=fixture(async(path,body)=>{if(body?.action==='save')throw new Error('Synthetic save failure');});
  await state.controller.open('repair');
  const input=state.host.all().find(node=>node.id?.endsWith('-reason_fooled_me'));input.value='Unsaved wording';input.oninput();
  await click(find(state.host,'button','Retry without source'));
  assert.ok(state.host.textContent.includes('Synthetic save failure'));
  assert.equal(state.host.all().find(node=>node.id?.endsWith('-reason_fooled_me')).value,'Unsaved wording');
  assert.ok(find(state.host,'button','Save repair notes'));
  assert.equal(state.requests.filter(item=>item.body?.action==='retry').length,0);
});
