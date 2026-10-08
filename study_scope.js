// A week scope filters views while keeping original section and row IDs stable.
state.studyScope ||= DATA.final?.enabled ? 'all' : DATA.week8?.enabled ? 'week8' : 'all';
// Restore the whole-final scope once after the expanded edition. Retain every
// personal rating, highlight, note, and later user-selected scope.
if(DATA.final?.enabled&&!state.fullFinalScope){state.studyScope='all';state.fullFinalScope=true}
// Keep ratings and notes from an earlier truncated objective transcription.
state.objectives ||= {};
for(const [alias,canonical] of Object.entries(DATA.objective_aliases||{})){
 const old=state.objectives[alias];
 if(old&&!state.objectives[canonical])state.objectives[canonical]={...old};
 else if(old&&state.objectives[canonical]){
  const current=state.objectives[canonical];if(old.notes&&!current.notes)current.notes=old.notes;
  else if(old.notes&&current.notes&&!current.notes.includes(old.notes))current.notes+='\n\nAdditional source note:\n'+old.notes;
  if(old.review&&old.review!=='unrated'&&(!current.review||current.review==='unrated'))current.review=old.review;
 }
}
function week8Scope(){return state.studyScope==='week8'&&!!DATA.week8?.enabled}
function scopePageEntries(){return pages.map((p,i)=>({p,i})).filter(({p})=>!week8Scope()||p.week===8)}
function scopeObjectives(){return DATA.objectives.filter(o=>!week8Scope()||[...(DATA.week8.objective_ids||[]),...(DATA.week8.supporting_objective_ids||[])].includes(o.id))}
function scopeQuestion(q){return !week8Scope()||(DATA.week8.question_ids||[]).includes(q.id)}
function scopeRow(sheet,index){return !week8Scope()||(DATA.week8.scope_rows?.[sheet]||[]).includes(index)}
function scopePathIndices(){return DATA.comparison_sheets.find(s=>s.id==='pathology').rows.map((_,i)=>i).filter(i=>scopeRow('pathology',i))}
function scopeRapidCards(){return DATA.rapid_pathology.filter(c=>!week8Scope()||c.week===8)}
function scopeOverviewMarkup(){
 if(!week8Scope())return DATA.final?.enabled?`<section class="week8-overview"><p class="eyebrow">FINAL STUDY GUIDE</p><h1>Neuro + Psych</h1><p>Read the full explanations now. Mark learned keeps only the key distinctions for your morning-of skim.</p><p class="week8-counts">${pages.length} chapters · ${DATA.objectives.length} learning objectives · ${DATA.questions.length} local question records</p><p><strong>Friday priority: Quiz 7 / Week 8.</strong> Use the scope selector to focus on that week.</p></section>`:'';
 const w=DATA.week8;
 return `<section class="week8-overview"><p class="eyebrow">FRIDAY QUIZ · WEEK 8</p><h1>Quiz 7 study guide</h1><p>Read the explanations, test the learning objectives, then revisit the missed-question facts. Mark learned keeps the key distinctions visible.</p><p class="week8-counts">${scopePageEntries().length} chapters · ${scopeObjectives().length} learning objectives · ${(w.question_ids||[]).length} local question records</p><details class="week8-source-note"><summary>Coverage & sources</summary><p>Course review material and Week 8 lecture notes anchor this guide. It covers the whole week, including coma and sleep; exact Quiz 7 scope can be checked against private course materials when imported.</p>${(w.source_gaps||[]).map(g=>`<p>${esc(g)}</p>`).join('')}</details></section>`;
}
function scopeSelectorMarkup(){return DATA.week8?.enabled?`<label class="study-scope-label">Study scope<select id="study-scope" aria-label="Study scope"><option value="all" ${!week8Scope()?'selected':''}>Full final</option><option value="week8" ${week8Scope()?'selected':''}>Quiz 7 · W8</option></select></label>`:''}
function ensureScopePage(){const entries=scopePageEntries();if(!entries.some(x=>x.i===state.page))state.page=entries[0]?.i||0}
ensureScopePage();
document.addEventListener('change',e=>{
 if(e.target.id!=='study-scope')return;
 state.studyScope=e.target.value;ensureScopePage();objectiveQuiz='';objectiveSearch='';objectiveSection='';pathSession=null;traitSession=null;rapidIndex=0;
 navigateView(currentView);save();
});
