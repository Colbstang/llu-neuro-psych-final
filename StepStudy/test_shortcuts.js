const test=require('node:test');
const assert=require('node:assert/strict');
const {action}=require('./shortcuts.js');
function event(key,extras={}){return {key,code:'Key'+key.toUpperCase(),target:{closest:()=>null},...extras};}
test('resource shortcuts use physical number keys on an Option keyboard',()=>{
  assert.deepEqual(action(event('£',{altKey:true,code:'Digit3'})),{kind:'resource',family:'mehlman'});
  assert.equal(action(event('3',{code:'Digit3'})),null);
});
test('study shortcuts preserve typing, repeated keys, dialogs, and existing actions',()=>{
  assert.equal(action(event('s',{target:{closest:()=>({})}}),'a'),null);
  assert.equal(action(event('s',{defaultPrevented:true}),'a'),null);
  assert.equal(action(event('s',{repeat:true}),'a'),null);
  assert.equal(action(event('s',{metaKey:true}),'a'),null);
  assert.deepEqual(action(event('s'),' association fibers '),{kind:'search',text:'association fibers'});
  assert.equal(action(event('h'),'a'),null);
  assert.equal(action(event('d'),'a'),null);
});
