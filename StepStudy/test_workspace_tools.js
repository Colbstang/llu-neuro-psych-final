const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function tools(fetch) {
  const observers=[];
  class Observer {
    constructor(callback){this.callback=callback;observers.push(this);}
    observe(host){this.host=host;}
    disconnect(){this.disconnected=true;}
  }
  const window={STUDY_APP_CONFIG:{token:'synthetic-course-token'}};
  const context={window,document:{querySelector:()=>null},fetch,AbortSignal,Date,URLSearchParams,IntersectionObserver:Observer};
  vm.runInNewContext(fs.readFileSync(__dirname+'/workspace_tools.js','utf8'),context);
  return {tools:window.StepWorkspaceTools,observers};
}

test('capture is explicit, uses course token, preserves multiple tags, and ignores duplicate trigger',async()=>{
  const requests=[];let resolve;
  const state=tools((path,options)=>{requests.push({path,options});return new Promise(done=>resolve=done);});
  const captured=[];
  state.tools.configure({getContext:()=>({subject_id:'neuro',chapter_id:'stable-chapter'}),onCaptured:result=>captured.push(result)});
  assert.equal(requests.length,0);
  const pending=state.tools.captureRegion();
  await state.tools.captureRegion();
  assert.equal(requests.length,1);
  assert.equal(requests[0].path,'/api/capture-region');
  assert.equal(requests[0].options.headers['X-Step-Token'],'synthetic-course-token');
  assert.deepEqual(JSON.parse(requests[0].options.body),{context:{subject_id:'neuro',chapter_id:'stable-chapter'}});
  resolve({ok:true,json:async()=>({ok:true,cancelled:false,question:{id:7,topic_ids:['neuro','psychiatry']}})});
  await pending;
  assert.equal(captured.length,1);
  assert.deepEqual(captured[0].question.topic_ids,['neuro','psychiatry']);
});

test('cancelled capture leaves inbox callback untouched and failed capture can retry',async()=>{
  let attempt=0,captured=0,errors=[];
  const state=tools(async()=>{attempt++;return attempt===2?{ok:false,json:async()=>({ok:false,error:'Permission unavailable'})}:{ok:true,json:async()=>({ok:true,cancelled:true})};});
  state.tools.configure({onCaptured:()=>captured++,onError:error=>errors.push(error)});
  await state.tools.captureRegion();await state.tools.captureRegion();await state.tools.captureRegion();
  assert.equal(captured,0);
  assert.equal(attempt,3);
  assert.deepEqual(errors,['Permission unavailable']);
});

test('chapter badges wait for visibility and share one lightweight count request',async()=>{
  const requests=[];
  const state=tools(async path=>{requests.push(path);return {ok:true,json:async()=>({ok:true,counts:{}})};});
  for(let i=0;i<53;i++)state.tools.mountChapterQuestions({isConnected:true,querySelector:()=>null},'chapter-'+i);
  assert.equal(requests.length,0);
  for(const observer of state.observers)observer.callback([{isIntersecting:true}]);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(requests,['/api/intake/chapter-links']);
  assert.ok(state.observers.every(observer=>observer.disconnected));
});
