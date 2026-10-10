const test=require('node:test'),assert=require('node:assert/strict');
const {countSession,canGrade}=require('./learning_flow.js');
test('session completion distinguishes skipped work from completed practice',()=>{assert.deepEqual(countSession({tasks:[{status:'complete'},{status:'pending'},{status:'skipped'}]}),{total:3,complete:1,skipped:1});});
test('an answer cannot be graded as independent recall after source reveal',()=>{assert.equal(canGrade(false,true),true);assert.equal(canGrade(true,true),false);assert.equal(canGrade(false,false),false);});
