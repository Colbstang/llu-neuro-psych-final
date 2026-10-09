/* A topic-level starting point. Counts describe observed signals, not mastery. */
state.studyTarget ||= 'both';
let studyDashboardMap=null,studyDashboardFilter='all';
function studyDashboardLOReview(id){const value=state.objectives?.[id]?.review;return value==='reviewed'?'good':value==='later'?'bad':value}
function studyDashboardGraph(){
 if(studyDashboardMap)return studyDashboardMap;
 const noteCards=new Map();
 for(const card of ankiLibrary.cards){const key=Number(card.note);if(!noteCards.has(key))noteCards.set(key,[]);noteCards.get(key).push(String(card.id))}
 studyDashboardMap=new Map((DATA.topic_groups?.groups||[]).map(group=>{
  const chapters=pages.filter(page=>group.page_ids.includes(page.id)),blocks=chapters.flatMap(page=>page.blocks),named=new Set();
  for(const page of chapters)for(const term of [...(page.keywords||[]),...ankiDetectTopics(page.title+' '+page.blocks.map(block=>block.title+' '+ankiPlain(block.html)).join(' '))]){
   const canonical=ankiCanonicalAliases[term]||term;if(ankiTopicMap[canonical])named.add(canonical);else if(ankiTopicMap[term])named.add(term);
  }
  const cardIds=new Set();for(const topic of named)for(const note of ankiTopicMap[topic]?.noteIds||[])for(const id of noteCards.get(Number(note))||[])cardIds.add(id);
  return [group.id,{...group,chapters,blocks,cardIds:[...cardIds],named:[...named]}];
 }));return studyDashboardMap;
}
function studyDashboardRows(){
 const allowed=new Set(scopePageEntries().map(({p})=>p.id)),objectives=scopeObjectives();
 return [...studyDashboardGraph().values()].map(group=>{
  const chapters=group.chapters.filter(page=>allowed.has(page.id));if(!chapters.length)return null;
  const blocks=chapters.flatMap(page=>page.blocks),ids=new Set(blocks.map(block=>block.id));
  const los=objectives.filter(lo=>(lo.sections||[]).some(id=>ids.has(id)));
  const questions=DATA.questions.filter(q=>scopeQuestion(q)&&(state.questions?.[q.id]?.sections||[]).some(id=>ids.has(id)));
  const wrong=questions.filter(q=>/wrong/i.test(q.source_status||'')&&state.questions?.[q.id]?.review!=='reviewed');
  const bad=los.filter(lo=>studyDashboardLOReview(lo.id)==='bad'),unrated=los.filter(lo=>!['good','bad'].includes(studyDashboardLOReview(lo.id)));
  const cardIds=week8Scope()?group.cardIds.filter(id=>{
   const card=ankiCards.get(Number(id)),note=ankiNotes.get(Number(card?.note));
   return (note?.quizTags||[]).some(tag=>/Quiz[_ ]?0?7(?:$|::)/i.test(tag))||chapters.some(page=>(page.keywords||[]).some(term=>(ankiTopicMap[ankiCanonicalAliases[term]||term]?.noteIds||[]).includes(Number(card?.note))));
  }):group.cardIds;
  const records=cardIds.map(id=>studyAppCoverage.cards?.[id]).filter(Boolean),known=records.filter(card=>Number.isInteger(card.recent_review_count));
  const recentReviews=known.reduce((n,card)=>n+card.recent_review_count,0),again=known.reduce((n,card)=>n+card.recent_again_count,0),hard=known.reduce((n,card)=>n+card.recent_hard_count,0);
  const reviewed=records.filter(card=>card.review_count>0).length,learned=blocks.filter(block=>state.good[block.id]).length;
  const count=guideReadingCounts(blocks),priority=wrong.length+(state.studyTarget==='step'?0:bad.length)+(state.studyTarget==='in_house'?0:again);
  return {...group,chapters,blocks,los,wrong,bad,unrated,cardIds,known:known.length,recentReviews,again,hard,reviewed,learned,count,priority};
 }).filter(Boolean);
}
function studyDashboardSignal(value,label,kind){return `<span class="study-signal ${value?'has-signal '+kind:''}"><b>${value}</b> ${label}</span>`}
function renderStudyDashboard(){
 const all=studyDashboardRows(),rows=all.filter(row=>studyDashboardFilter!=='needs_work'||row.priority>0).sort((a,b)=>b.priority-a.priority),counts=guideLengthCounts();
 const uniqueWrong=new Set(all.flatMap(row=>row.wrong.map(q=>q.id))),bad=new Set(all.flatMap(row=>row.bad.map(lo=>lo.id))),synced=studyAppCoverage.summary?.synced_at;
 const target=state.studyTarget,showSchool=target!=='step',showStep=target!=='in_house';
 $('#guide').innerHTML=`<section class="study-dashboard">
  <div class="study-dashboard-heading"><div><p class="eyebrow">${week8Scope()?'WEEK 8 / QUIZ 7':'NEURO + PSYCH'}</p><h1>Study by topic</h1><p>Start with the distinctions you missed. Read, practice, then keep a short skim.</p></div><button data-dashboard-continue>Continue reading →</button></div>
  <div class="study-target-control" role="group" aria-label="Study target"><span>Target</span>${[['in_house','In-house'],['step','Step'],['both','Both']].map(([key,label])=>`<button data-study-target="${key}" aria-pressed="${target===key}">${label}</button>`).join('')}</div>
  <div class="study-dashboard-overview"><div><small>UNRESOLVED MISSED QUESTIONS</small><strong>${uniqueWrong.size}</strong><span>from your saved bank</span></div>${showSchool?`<div><small>OBJECTIVES MARKED BAD</small><strong>${bad.size}</strong><span>your own ratings</span></div>`:''}<div><small>READ WITH CURRENT COLLAPSES</small><strong>${(counts.current/counts.unit).toFixed(1)} <i>page eq.</i></strong><span>${(counts.full/counts.unit).toFixed(1)} fully expanded · ${(counts.skim/counts.unit).toFixed(1)} all learned</span></div></div>
  <div class="study-dashboard-toolbar"><div role="group" aria-label="Topic filter">${[['all','All topics'],['needs_work','Needs work']].map(([key,label])=>`<button data-dashboard-filter="${key}" aria-pressed="${studyDashboardFilter===key}">${label}</button>`).join('')}</div>${studyAppConfig?`<button data-app-anki-sync ${studyDashboardSyncPending?'disabled':''}>${studyDashboardSyncPending?'Syncing…':'Sync Anki'}</button>`:''}</div>
  <p class="study-dashboard-sync" role="status">${studyDashboardSyncMessage||(!synced?'Anki review signals appear after a sync in the app.':`Anki synced ${esc(new Date(Number(synced)*1000).toLocaleString())} · last ${studyAppCoverage.summary.recent_window_days||30} days`)}${!studyAppConfig?' Open LLU Study to sync reviews.':''}</p>
  <div class="study-topic-list">${rows.map(row=>`<article class="study-topic-row"><div class="study-topic-description"><button class="study-topic-title" data-dashboard-topic="${esc(row.id)}">${esc(row.short_title||row.title)} →</button><small>${row.chapters.length} chapter${row.chapters.length===1?'':'s'} · ${row.learned}/${row.blocks.length} sections learned · ${(row.count.current/counts.unit).toFixed(1)} page eq.</small></div><div class="study-topic-signals">${studyDashboardSignal(row.wrong.length,'missed','question')}${showSchool?studyDashboardSignal(row.bad.length,'LOs bad','lo'):''}${showStep?`<span class="study-signal ${row.again?'has-signal anki':''}" title="Again ratings in normal Anki learning, review, or relearning during the last 30 days"><b>${row.known?row.again:'—'}</b> Again${row.known?`<small>${row.recentReviews} recent reviews${row.hard?' · '+row.hard+' Hard':''}</small>`:'<small>not synced</small>'}</span>`:''}</div><div class="study-topic-actions"><button data-dashboard-topic="${esc(row.id)}">Read</button>${showSchool?`<button data-dashboard-los="${esc(row.id)}">LOs <small>${row.unrated.length} unrated</small></button>`:''}${row.wrong.length?`<button data-dashboard-missed="${esc(row.id)}">Missed facts</button>`:''}</div></article>`).join('')||'<p class="study-dashboard-empty">No recorded trouble signals in this view. Unrated objectives and unreviewed cards still need assessment.</p>'}</div>
  <details class="study-metric-explanation"><summary>What these signals mean</summary><p>Missed = a saved wrong question you have not marked reviewed. LOs bad = your objective ratings. Again and Hard = actual Anki ratings over the last 30 days; no review data shows a dash. Cards are linked through named topics, so a card or question may inform more than one topic. These are review priorities, not a mastery score.</p><p>${showSchool?'In-house uses course objectives and your missed facts. ':''}${showStep?'Step prioritizes missed facts and Anki recall. This set covers Neuro/Psych; other Step 1 and Step 2 subjects still need to be added. ':''}Page equivalents use ${counts.unit} words and reflect sections you have collapsed.</p></details>
 </section>`;
}
let studyDashboardSyncPending=false,studyDashboardSyncMessage='';
function studyDashboardJump(topic){const group=studyDashboardRows().find(row=>row.id===topic);if(!group)return;state.page=pages.findIndex(page=>page.id===group.chapters[0].id);state.navGroups||={};state.navGroups[group.id]=true;navigateView('guide');document.getElementById('topic-'+topic)?.scrollIntoView({block:'start'})}
document.addEventListener('click',event=>{
 const target=event.target.closest('[data-study-target]');if(target){state.studyTarget=target.dataset.studyTarget;save();renderStudyDashboard();return}
 const filter=event.target.closest('[data-dashboard-filter]');if(filter){studyDashboardFilter=filter.dataset.dashboardFilter;renderStudyDashboard();return}
 const topic=event.target.closest('[data-dashboard-topic]');if(topic){studyDashboardJump(topic.dataset.dashboardTopic);return}
 if(event.target.closest('[data-dashboard-continue]')){navigateView('guide');document.getElementById('page-'+state.page)?.scrollIntoView({block:'start'});return}
 const lo=event.target.closest('[data-dashboard-los]');if(lo){const group=studyDashboardRows().find(row=>row.id===lo.dataset.dashboardLos);objectiveSection='';objectiveSearch='';objectiveQuiz='';objectiveView='all';navigateView('objectives',{topic:group.id});return}
 const missed=event.target.closest('[data-dashboard-missed]');if(missed){const group=studyDashboardRows().find(row=>row.id===missed.dataset.dashboardMissed);navigateView('questions',{topic:group.id,missed:true});return}
});
const studyDashboardOriginalSync=studyAppSync;
studyAppSync=async function(){studyDashboardSyncPending=true;studyDashboardSyncMessage='Reading Anki review history…';if(currentView==='dashboard')renderStudyDashboard();try{const ok=await studyDashboardOriginalSync();studyDashboardSyncMessage=ok?'':'Anki sync failed. Open Anki with AnkiConnect and try again.'}finally{studyDashboardSyncPending=false;if(currentView==='dashboard')renderStudyDashboard()}};
