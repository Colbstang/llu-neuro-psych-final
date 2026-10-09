const test = require('node:test');
const assert = require('node:assert/strict');
const {evidence, readingLength, latestAttempts, ankiDueSummary, ankiReviewLabel} = require('./step.js');
const {validSelection} = require('../TermCards/selection_lookup.js');

test('unassessed recalls and unconnected Anki remain unknown', () => {
  const data = {state:{attempts:[{topic_id:'renal',score:1,grade:{assessed:false}}]},anki:{subjects:{}},questions:{topics:{}}};
  assert.equal(evidence('renal',data).recall,null);
  assert.equal(evidence('renal',data).anki,null);
  assert.equal(evidence('renal',data).course,null);
});
test('course ratings are separate from assessed recall and imported questions', () => {
  const data={state:{attempts:[]},module:{available:true,topics:{neuro:{lo_good:5,lo_bad:2,wrong_count:1}}}};
  const info=evidence('neuro',data);assert.equal(info.course.lo_bad,2);assert.equal(info.recall,null);assert.equal(info.unresolved,0);
  data.module.available=false;assert.equal(evidence('neuro',data).course,null);
});
test('a failed later assessment retains the last actual assessed recall', () => {
  const data={state:{attempts:[{topic_id:'renal',score:.4,grade:{assessed:true}},{topic_id:'renal',score:null,grade:{assessed:false}}]}};
  assert.equal(evidence('renal',data).recall,.4);
});
test('the latest actual recall and question misses remain separate evidence', () => {
  const data = {state:{attempts:[{topic_id:'renal',score:1,grade:{assessed:true}},{topic_id:'renal',score:.4,grade:{assessed:true}}]},anki:{subjects:{renal:{review_count:10,again_count:4,due_count:7}}},questions:{topics:{renal:{unresolved_count:3}}}};
  const info=evidence('renal',data); assert.equal(info.recall,.4);assert.equal(info.unresolved,3);assert.equal(info.anki.due_count,7);
  assert.equal(latestAttempts(data.state.attempts).renal.score,.4);
});
test('collapsed reading still counts skim text and expanded image allowance', () => {
  const records=[{id:'a',definition:'The key pattern remains visible.',summary:'One two three four five six seven eight nine ten.',image:{url:'valid'}},{id:'b',definition:'Two words',summary:'Two words'}];
  const open=readingLength(records),closed=readingLength(records,{a:true});
  assert.equal(open.fullWords,12);assert.equal(closed.visibleWords,7);assert.equal(closed.fullPages,open.fullPages);assert.ok(closed.visiblePages<open.visiblePages);assert.ok(closed.visiblePages>0);
});
test('on-demand lookup accepts unknown selected terms without a scanner entry', () => {
  assert.equal(validSelection(' previously unseen medical term '),true);
  assert.equal(validSelection(''),false);assert.equal(validSelection('x'.repeat(161)),false);assert.equal(validSelection(null),false);
});
test('unknown due counts remain unknown and sampled ratings remain labeled', () => {
  assert.deepEqual(ankiDueSummary([{due_count:null}]),{count:0,known:0,partial:true});
  assert.deepEqual(ankiDueSummary([{due_count:3,scope_partial:true},{due_count:null}]),{count:3,known:1,partial:true});
  assert.equal(ankiReviewLabel({review_count:null,review_history_known:false}),'History unknown');
  assert.equal(ankiReviewLabel({review_count_sampled:0,again_count_sampled:0,hard_count_sampled:0,review_scope:'sample',review_sample_size:100}),'0 / 0 · 100-card sample');
});
