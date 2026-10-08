let currentView=location.hash.slice(1)||state.view||'guide',objectiveMount=null,viewOptions={};
if(!['guide','objectives','questions','pathology','drugs','bugs'].includes(currentView))currentView='guide';
const sheetViews={'drugs':'drugs','bugs':'bugs','pathology':'pathology'};
function resolveSheet(view){return DATA.comparison_sheets.find(s=>s.id===sheetViews[view])||DATA.comparison_sheets.find(s=>view==='drugs'?/drug/i.test(s.title):view==='bugs'?/infection/i.test(s.title):/pathology/i.test(s.title))}
function navigateView(view,options={}){
 if(objectiveMount){objectiveMount.destroy();objectiveMount=null}
 currentView=['guide','objectives','questions','pathology','drugs','bugs'].includes(view)?view:'guide';viewOptions=options;state.view=currentView;editing=false;
 if(location.hash!==('#'+currentView))history.pushState(null,'','#'+currentView);render();save();if(!options.keepScroll)window.scrollTo(0,0);
}
function render(){
 renderNav();$('#edit-toggle').hidden=currentView!=='guide';
 if(currentView==='guide')renderGuide();
 else if(currentView==='objectives'){if(objectiveMount)objectiveMount.destroy();objectiveMount=mountObjectivesPage($('#guide'),viewOptions)}
 else if(currentView==='questions'){$('#guide').innerHTML=$('#questions-page-template').innerHTML;initializeQuestions(viewOptions.section||'')}
 else {const sheet=resolveSheet(currentView);$('#guide').innerHTML=comparisonMarkup(sheet.id)}
 updateQuestionLinks();
}
renderNav=function(){
 const short=['Neuroaxis','Deficit pattern','Tempo','Myositis','IBM / DMD'];
 const views=[['guide','Read'],['objectives','LOs'],['questions','Questions'],['pathology','Pathology'],['drugs','Drugs'],['bugs','Bugs']];
 const item=({p,i})=>`<button class="nav-item ${i===state.page?'active':''}" data-page="${i}" title="${esc(p.title)}">${esc(p.short_title||short[i]||p.title)}</button>`;
 const entries=scopePageEntries();state.navGroups||={};
 const groups=[['priority','Quiz 7 · Fri',entries.filter(x=>x.p.week===8)],['early','Neuro basics',entries.filter(x=>x.p.week!==8&&!x.p.id.startsWith('final-late-')&&!x.p.id.startsWith('final-psych-'))],['late','Neuro systems',entries.filter(x=>x.p.id.startsWith('final-late-'))],['psych','Psych / pharm',entries.filter(x=>x.p.id.startsWith('final-psych-'))]];
 const chapters=week8Scope()?entries.map(item).join(''):groups.filter(g=>g[2].length).map(([id,title,items])=>`<details class="nav-group" data-nav-group="${id}" ${state.navGroups[id]??items.some(x=>x.i===state.page)?'open':''}><summary>${title}<small>${items.length}</small></summary>${items.map(item).join('')}</details>`).join('');
 $('#nav').innerHTML=scopeSelectorMarkup()+views.map(([id,label])=>`<button class="nav-view ${currentView===id?'active':''}" data-view="${id}">${label}</button>`).join('')+(currentView==='guide'?'<div class="nav-divider"></div>'+chapters:'');
 const visiblePages=scopePageEntries().map(x=>x.p),total=visiblePages.flatMap(p=>p.blocks).length,done=visiblePages.flatMap(p=>p.blocks).filter(b=>state.good[b.id]).length;
 $('#progress-text').textContent=`${done}/${total} learned`;$('#progress-bar').style.width=`${done/total*100}%`;
 const counts=guideReadingCounts(visiblePages.flatMap(p=>p.blocks)),unit=DATA.word_budget.words_per_page_equivalent||700;
 $('#word-budget').innerHTML=`Reading ≈ ${Math.ceil(counts.current/unit)} page eq.<span class="skim-budget">All learned ≈ ${Math.ceil(counts.skim/unit)} eq.</span>`;$('#word-budget').title=`${counts.full.toLocaleString()} detailed words; ${counts.current.toLocaleString()} currently expanded/skim words. About ${unit} words per page equivalent. Detailed guide has no page cap; morning skim target is 80 or fewer. Separate objectives and comparison sheets excluded.`;document.querySelector('.brand small').textContent=week8Scope()?'Study guide · Quiz 7 / Week 8':'Study guide · Full final';
};
$('#questions-button').onclick=()=>navigateView('questions');$('#objectives-button').onclick=()=>navigateView('objectives');
document.addEventListener('click',e=>{
 const image=e.target.closest('[data-source-image]');if(image)showSidePanel('Question image',`<p>${esc(image.dataset.imageCaption)}</p><div class="panel-zoom"><button data-panel-zoom="-">−</button><span>Image zoom</span><button data-panel-zoom="+">+</button></div><div class="panel-media"><img src="${image.dataset.sourceImage}" alt="${esc(image.dataset.imageCaption)}"></div>`);
 const view=e.target.closest('[data-view]');if(view)navigateView(view.dataset.view);
 const objective=e.target.closest('[data-objective-section]');if(objective)navigateView('objectives',{section:objective.dataset.objectiveSection});
 const jump=e.target.closest('[data-jump-section]');if(jump){const section=questionSections.find(s=>s.id===jump.dataset.jumpSection);if(section){state.page=section.pageIndex;state.studyScope=pages[section.pageIndex].week===8?'week8':'all';navigateView('guide');document.querySelector(`[data-block="${section.id}"]`).scrollIntoView({block:'start'})}}
});
// Apply source-backed classifications once. Personal notes and review status survive.
for(const q of DATA.questions){
 const mapped=(DATA.question_auto_links||[]).find(x=>x.question_id===q.id),r=qWrite(q.id);
 if(mapped){r.sections=[...new Set([...r.sections,...mapped.sections])];r.tags=[...new Set([...r.tags,...mapped.tags])];q.auto_rationale=mapped.rationale}
 else if(!r.tags.length){r.tags=[q.title.replace(/^(AMBOSS|UWorld)\s*[-–]\s*/,'').replace(/^Test \d+, Question \d+:\s*/,'')]}
}

window.addEventListener('popstate',()=>{const view=location.hash.slice(1)||'guide';if(view!==currentView)navigateView(view)});
document.addEventListener('toggle',e=>{if(e.target.matches('[data-nav-group]')&&e.target.isConnected){state.navGroups[e.target.dataset.navGroup]=e.target.open;save()}},true);
