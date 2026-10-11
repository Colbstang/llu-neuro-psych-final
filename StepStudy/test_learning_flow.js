const test=require('node:test'),assert=require('node:assert/strict');
const {countSession,canGrade,selectGazeItem,createDraftController}=require('./learning_flow.js');
test('session completion distinguishes skipped work from completed practice',()=>{assert.deepEqual(countSession({tasks:[{status:'complete'},{status:'pending'},{status:'skipped'}]}),{total:3,complete:1,skipped:1});});
test('an answer cannot be graded as independent recall after source reveal',()=>{assert.equal(canGrade(false,true),true);assert.equal(canGrade(true,true),false);assert.equal(canGrade(false,false),false);});
test('gaze objective selection restores only an objective still in the catalog',()=>{
  const items=[{item_id:'first'},{item_id:'second'}];
  assert.equal(selectGazeItem(items,'second'),'second');
  assert.equal(selectGazeItem(items,'removed'),'first');
  assert.equal(selectGazeItem([],'removed'),'');
});
test('flushing a draft cancels the pending debounce and saves its latest text',async()=>{
  const saved=[],draft=createDraftController(async value=>saved.push(value));
  draft.change('Earlier text');await draft.flush('Latest prediction');await draft.stop();
  assert.deepEqual(saved,['Latest prediction']);
});
test('draft writes serialize so an older request cannot finish after the latest answer',async()=>{
  let release;const saved=[],draft=createDraftController(async value=>{
    saved.push(value);if(value==='old')await new Promise(resolve=>release=resolve);
  });
  const first=draft.flush('old');await Promise.resolve();await Promise.resolve();
  const latest=draft.flush('new');assert.deepEqual(saved,['old']);release();
  await Promise.all([first,latest]);assert.deepEqual(saved,['old','new']);
});
test('a failed draft save remains retryable',async()=>{
  let calls=0;const draft=createDraftController(async()=>{if(++calls===1)throw Error('Offline');});
  await assert.rejects(draft.flush('Answer'),/Offline/);await draft.flush('Answer');assert.equal(calls,2);
});

function browserRuntime(){
  const vm=require('node:vm'),fs=require('node:fs');
  class Element{
    constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.value='';this.className='';this._text='';this.classList={add:value=>this.className+=' '+value};}
    get isConnected(){return this.root===true||!!this.parent?.isConnected;}
    set textContent(value){this._text=value;this.children=[];}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
    append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
    replaceChildren(...children){for(const child of this.children)child.parent=null;this.children=[];this._text='';this.append(...children);}
    setAttribute(name,value){this.attributes[name]=value;}
    getContext(){return {beginPath(){},moveTo(){},lineTo(){},stroke(){},clearRect(){}};}
    closest(){let parent=this;while(parent&&!parent.className.includes('learning-flow'))parent=parent.parent;return parent;}
    querySelector(){return this.find(child=>child.attributes.role==='status');}
    find(predicate){for(const child of this.children){if(predicate(child))return child;const nested=child.find?.(predicate);if(nested)return nested;}return null;}
  }
  const window={},context={window,document:{createElement:tag=>new Element(tag),createTextNode:text=>{const node=new Element('#text');node.textContent=text;return node;}},URLSearchParams,setTimeout,clearTimeout,Promise};
  vm.runInNewContext(fs.readFileSync(require.resolve('./learning_flow.js'),'utf8'),context);
  const host=new Element('main');host.root=true;
  return {host,learning:window.StepLearning};
}
test('returning to a revealed objective restores frozen text and self-rating controls',async()=>{
  const {host,learning}=browserRuntime(),requests=[];
  const api=async(path,body)=>{
    requests.push([path,body]);if(path.startsWith('/api/concept?'))return {recall:{session_id:'resume',prompt:'Synthetic objective',draft:'Original answer',revealed:true,reference_context:'Supplied explanation'}};
    return path==='/api/concept-answer'?{attempt:{}}:{ok:true};
  };
  await learning.mountConcept(host,{api,itemId:'lo:one',aiAvailable:true,draft:'Edited task draft'});
  const answer=host.find(child=>child.tagName==='textarea');assert.equal(answer.value,'Original answer');assert.equal(answer.disabled,true);
  assert.equal(host.find(child=>child.textContent==='Grade answer').disabled,true);
  assert.ok(host.textContent.includes('Supplied explanation'));
  await host.find(child=>child.tagName==='button'&&child.textContent==='Recalled with effort').onclick();
  const saved=requests.find(([path])=>path==='/api/concept-answer')[1];
  assert.equal(saved.answer,'Original answer');assert.equal(saved.action,'self_rating');assert.equal(saved.self_rating,'hard');
});
test('Reveal locks the answer before a delayed response and displays the server-frozen answer',async()=>{
  const {host,learning}=browserRuntime();let release,start;const started=new Promise(resolve=>start=resolve),requests=[];
  const api=async(path,body)=>{
    requests.push([path,body]);
    if(path.startsWith('/api/concept?'))return {recall:{session_id:'one',prompt:'Synthetic objective',draft:'Original answer'}};
    if(path==='/api/concept-reveal'){start();return new Promise(resolve=>release=resolve);}
    return path==='/api/concept-answer'?{attempt:{}}:{ok:true};
  };
  await learning.mountConcept(host,{api,itemId:'lo:one',aiAvailable:true});
  const answer=host.find(child=>child.tagName==='textarea'),reveal=host.find(child=>child.tagName==='button'&&child.textContent==='Reveal course explanation');
  const grade=host.find(child=>child.tagName==='button'&&child.textContent==='Grade answer'),save=host.find(child=>child.tagName==='button'&&child.textContent==='Save without grading');
  const pending=reveal.onclick();assert.equal(answer.disabled,true,'lock before either draft or source request');await started;
  assert.equal(grade.disabled,true);assert.equal(save.disabled,true);
  assert.equal(requests.find(([path])=>path==='/api/concept-reveal')[1].answer,'Original answer');
  // A canonical response also repairs a stale second view of the same objective.
  answer.value='Stale local text';release({reference_context:'Source explanation',answer:'Original answer'});await pending;
  assert.equal(answer.value,'Original answer');assert.equal(answer.disabled,true);
  assert.equal(grade.disabled,true);assert.equal(save.disabled,false);
  await host.find(child=>child.tagName==='button'&&child.textContent==='Recalled').onclick();
  assert.equal(requests.find(([path])=>path==='/api/concept-answer')[1].answer,'Original answer');
});
test('a failed Reveal unlocks the retained draft for another try',async()=>{
  const {host,learning}=browserRuntime();
  const api=async path=>{
    if(path.startsWith('/api/concept?'))return {recall:{session_id:'one',prompt:'Synthetic objective',draft:'Retained answer'}};
    if(path==='/api/concept-reveal')throw Error('Synthetic response failure');return {ok:true};
  };
  await learning.mountConcept(host,{api,itemId:'lo:one',aiAvailable:true});
  const answer=host.find(child=>child.tagName==='textarea');await host.find(child=>child.tagName==='button'&&child.textContent==='Reveal course explanation').onclick();
  assert.equal(answer.value,'Retained answer');assert.equal(answer.disabled,false);assert.ok(host.textContent.includes('Synthetic response failure'));
});
test('gaze selection flushes the current prediction before loading the next objective',async()=>{
  const {host,learning}=browserRuntime(),requests=[],selected=[];
  const api=async(path,body)=>{
    requests.push([path,body]);if(path==='/api/gaze')return {items:[{item_id:'a',title:'A',topic_ids:['neuro']},{item_id:'b',title:'B',topic_ids:['neuro']}]};
    if(path==='/api/labs')return {labs:[]};
    if(path.startsWith('/api/concept?')){const id=new URL('http://local'+path).searchParams.get('item_id');return {recall:{session_id:'session-'+id,prompt:'Objective '+id,draft:'Saved '+id,sources:[]}};}
    return {ok:true};
  };
  await learning.mountGaze(host,{api,topicId:'psychiatry',selectedItem:'b',onChoose:id=>selected.push(id)});
  const choose=host.find(child=>child.tagName==='select'),answer=host.find(child=>child.tagName==='textarea');
  assert.equal(choose.value,'b');assert.equal(answer.value,'Saved b');answer.value='New prediction for B';answer.oninput();
  choose.value='a';await choose.onchange();
  assert.deepEqual(selected,['b','a']);
  const saveIndex=requests.findIndex(([path])=>path==='/api/concept-draft'),loadIndex=requests.findIndex(([path])=>path.includes('item_id=a'));
  assert.ok(saveIndex<loadIndex);assert.equal(requests[saveIndex][1].session_id,'session-b');assert.equal(requests[saveIndex][1].answer,'New prediction for B');
  assert.equal(host.find(child=>child.tagName==='textarea').value,'Saved a');
  assert.equal(new URL('http://local'+requests[loadIndex][0]).searchParams.get('topic_id'),'neuro');
});
