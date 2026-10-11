const test=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('./navigation.js');
test('browser Back restores subject, reference location, and scroll without adding an entry',async()=>{
  const entries=[],restored=[];let at=-1;
  const history={get state(){return entries[at]?.state;},replaceState(state,_,url){if(at<0)at=0;entries[at]={state,url};},pushState(state,_,url){entries.splice(at+1);entries.push({state,url});at++;}};
  const nav=create(history,{},async state=>{restored.push(state);nav.write({...state,scrollY:0});});
  nav.write({view:'read',topicId:'neuro',scrollY:160,course:{id:'1',key:'search'}});
  nav.write({view:'read',topicId:'neuro',scrollY:160,course:{id:'2',key:'pdf-page-14'}});
  nav.write({view:'cards',topicId:'renal',scrollY:0});
  at--;await nav.restore({state:history.state});
  assert.equal(entries.length,3);assert.equal(restored[0].course.id,'2');assert.equal(restored[0].scrollY,160);
  at--;await nav.restore({state:history.state});assert.equal(restored[1].course.key,'search');
  at++;await nav.restore({state:history.state});assert.equal(restored[2].course.id,'2');
  nav.write({view:'read',topicId:'neuro',scrollY:720,course:{id:'2',key:'pdf-page-14'}},true);
  assert.equal(entries.length,3);assert.equal(history.state.stepNavigation.scrollY,720);
});
test('same view in a different subject creates a recoverable history entry',()=>{
  let saved=null;const writes=[];const history={get state(){return saved;},replaceState(s){saved=s;writes.push('replace');},pushState(s){saved=s;writes.push('push');}};
  const nav=create(history,{},()=>{});nav.write({view:'cards',topicId:'neuro'});nav.write({view:'cards',topicId:'renal'});
  assert.deepEqual(writes,['replace','push']);
});
