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
function scopePageSelected(p){return !!p&&(p.week===8||(DATA.week8?.include_page_ids||[]).includes(p.id))}
function topicForPage(page){return (DATA.topic_groups?.groups||[]).find(g=>g.page_ids.includes(page.id))}
function scopePageEntries(){
 const order=new Map((DATA.topic_groups?.groups||[]).flatMap(g=>g.page_ids).map((id,i)=>[id,i]));
 return pages.map((p,i)=>({p,i})).filter(({p})=>!week8Scope()||scopePageSelected(p)).sort((a,b)=>(order.get(a.p.id)??a.i)-(order.get(b.p.id)??b.i));
}
function scopeTopicGroups(){
 const entries=scopePageEntries();
 return (DATA.topic_groups?.groups||[]).map(g=>({...g,entries:entries.filter(({p})=>g.page_ids.includes(p.id))})).filter(g=>g.entries.length);
}
function topicHeadingMarkup(page,index,entries){
 const topic=topicForPage(page);
 if(!topic||(index&&topicForPage(entries[index-1].p)?.id===topic.id))return '';
 return `<div class="topic-heading" id="topic-${esc(topic.id)}"><p class="eyebrow">TOPIC</p><h2>${esc(topic.title)}</h2></div>`;
}
function scopeObjectives(){return DATA.objectives.filter(o=>!week8Scope()||[...(DATA.week8.objective_ids||[]),...(DATA.week8.supporting_objective_ids||[])].includes(o.id))}
function scopeQuestion(q){
 if(!week8Scope())return true;
 if((DATA.week8.question_ids||[]).includes(q.id))return true;
 const sectionIds=new Set(scopePageEntries().flatMap(({p})=>p.blocks.map(b=>b.id)));
 const mapped=(DATA.question_auto_links||[]).find(x=>x.question_id===q.id);
 const sections=[...(mapped?.sections||[]),...(state.questions?.[q.id]?.sections||[]),...(q.suggested_sections||[]).map(s=>s.block_id)];
 return sections.some(id=>sectionIds.has(id));
}
function scopeRow(sheet,index){return !week8Scope()||(DATA.week8.scope_rows?.[sheet]||[]).includes(index)}
function scopePathIndices(){return DATA.comparison_sheets.find(s=>s.id==='pathology').rows.map((_,i)=>i).filter(i=>scopeRow('pathology',i))}
function scopeRapidCards(){return DATA.rapid_pathology.filter(c=>!week8Scope()||c.week===8)}
function scopeOverviewMarkup(){
 if(!week8Scope())return DATA.final?.enabled?`<section class="week8-overview"><p class="eyebrow">FULL COURSE · BY TOPIC</p><h1>Neuro + Psych</h1><p>Read the explanations by topic. Mark learned keeps the essential distinctions for your morning skim, with missed questions still within reach.</p><p class="week8-counts">${scopeTopicGroups().length} topics · ${pages.length} chapters · ${DATA.objectives.length} answered objectives</p>${guideLengthMarkup()}<p class="scope-switch-note">Choose Week 8 in the sidebar to focus on this week’s material.</p>${courseAuditMarkup()}</section>`:'';
 const w=DATA.week8;
 const timingNotes=Array.isArray(w.timing_notes)?w.timing_notes:w.timing_notes?[w.timing_notes]:[];
 const timing=timingNotes.map(g=>`<p>${esc(g)}</p>`).join('');
 return `<section class="week8-overview"><p class="eyebrow">WEEK 8 · BY TOPIC</p><h1>Week 8 / Quiz 7</h1><p>The same topic-based guide, filtered to this week. Mark learned keeps the key distinctions visible.</p><p class="week8-counts">${scopePageEntries().length} chapters · ${scopeObjectives().length} answered objectives · ${w.question_ids.length} relevant appendix questions</p>${guideLengthMarkup()}<details class="week8-source-note"><summary>Scope, timing & sources</summary><p>Childhood Disorders 1–3 follows Quiz 6 and is included in Quiz 7. Coma and Sleep are scheduled after Quiz 7; they remain in the full-week view and count toward its expanded budget.</p><p>Course review and the lecture notes anchor this guide. Canvas timing and Anki tags support the scope; they are not an official exam blueprint.</p>${timing}${(w.source_gaps||[]).map(g=>`<p>${esc(g)}</p>`).join('')}</details></section>`;
}
function courseAuditMarkup(){
 const audit=DATA.course_audit;if(!audit)return '';
 return `<details class="week8-source-note"><summary>Course audit & source gaps</summary><p>${esc(audit.summary||'Source audit in progress.')}</p>${(audit.source_gaps||[]).map(g=>`<p>${esc(g)}</p>`).join('')}</details>`;
}
function scopeSelectorMarkup(){return DATA.week8?.enabled?`<label class="study-scope-label">Study scope<select id="study-scope" aria-label="Study scope"><option value="all" ${!week8Scope()?'selected':''}>Full final</option><option value="week8" ${week8Scope()?'selected':''}>Week 8 · Quiz 7</option></select></label>`:''}
function ensureScopePage(){const entries=scopePageEntries();if(!entries.some(x=>x.i===state.page))state.page=entries[0]?.i||0}
ensureScopePage();
document.addEventListener('change',e=>{
 if(e.target.id!=='study-scope')return;
 state.studyScope=e.target.value;ensureScopePage();objectiveQuiz='';objectiveSearch='';objectiveSection='';pathSession=null;traitSession=null;rapidIndex=0;
 navigateView(currentView);save();
});
