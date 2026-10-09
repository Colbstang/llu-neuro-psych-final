let currentView=location.hash.slice(1)||state.view||'guide',objectiveMount=null,viewOptions={};
if(!['dashboard','guide','objectives','questions','pathology','drugs','bugs'].includes(currentView))currentView='guide';
const sheetViews={'drugs':'drugs','bugs':'bugs','pathology':'pathology'};
function resolveSheet(view){return DATA.comparison_sheets.find(s=>s.id===sheetViews[view])||DATA.comparison_sheets.find(s=>view==='drugs'?/drug/i.test(s.title):view==='bugs'?/infection/i.test(s.title):/pathology/i.test(s.title))}
function navigateView(view,options={}){
 if(objectiveMount){objectiveMount.destroy();objectiveMount=null}
 currentView=['dashboard','guide','objectives','questions','pathology','drugs','bugs'].includes(view)?view:'guide';viewOptions=options;state.view=currentView;editing=false;
 if(location.hash!==('#'+currentView))history.pushState(null,'','#'+currentView);render();save();if(!options.keepScroll)window.scrollTo(0,0);
}
function render(){
 renderNav();$('#edit-toggle').hidden=currentView!=='guide';
 if(currentView==='dashboard')renderStudyDashboard();
 else if(currentView==='guide')renderGuide();
 else if(currentView==='objectives'){if(objectiveMount)objectiveMount.destroy();objectiveMount=mountObjectivesPage($('#guide'),viewOptions)}
 else if(currentView==='questions'){$('#guide').innerHTML=$('#questions-page-template').innerHTML;initializeQuestions(viewOptions.section||'',viewOptions)}
 else {const sheet=resolveSheet(currentView);$('#guide').innerHTML=comparisonMarkup(sheet.id)}
 updateQuestionLinks();
}
renderNav=function(){
 const short=['Neuroaxis','Deficit pattern','Tempo','Myositis','IBM / DMD'];
 const views=[['dashboard','Study'],['guide','Read'],['objectives','LOs'],['questions','Questions'],['pathology','Pathology'],['drugs','Drugs'],['bugs','Bugs']];
 const item=({p,i})=>`<button class="nav-item ${i===state.page?'active':''}" data-page="${i}" title="${esc(p.title)}">${esc(p.short_title||short[i]||p.title)}</button>`;
 const entries=scopePageEntries();state.navGroups||={};
 const groups=scopeTopicGroups();
 const chapters=groups.map(g=>`<details class="nav-group" data-nav-group="${g.id}" ${state.navGroups[g.id]??g.entries.some(x=>x.i===state.page)?'open':''}><summary title="${esc(g.title)}">${esc(g.short_title||g.title)}<small>${g.entries.length}</small></summary>${g.entries.map(item).join('')}</details>`).join('');
 $('#nav').innerHTML=scopeSelectorMarkup()+views.map(([id,label])=>`<button class="nav-view ${currentView===id?'active':''}" data-view="${id}">${label}</button>`).join('')+(currentView==='guide'?'<div class="nav-divider"></div>'+chapters:'');
 const visiblePages=scopePageEntries().map(x=>x.p),total=visiblePages.flatMap(p=>p.blocks).length,done=visiblePages.flatMap(p=>p.blocks).filter(b=>state.good[b.id]).length;
 $('#progress-text').textContent=`${done}/${total} learned`;$('#progress-bar').style.width=`${done/total*100}%`;
 refreshGuideLength();
 document.querySelector('.brand small').textContent=week8Scope()?'Study guide · Week 8 / Quiz 7':'Study guide · Full course by topic';
};
$('#questions-button').onclick=()=>navigateView('questions');$('#objectives-button').onclick=()=>navigateView('objectives');
document.addEventListener('click',e=>{
 const image=e.target.closest('[data-source-image]');if(image)showSidePanel('Question image',`<p>${esc(image.dataset.imageCaption)}</p><div class="panel-zoom"><button data-panel-zoom="-">−</button><span>Image zoom</span><button data-panel-zoom="+">+</button></div><div class="panel-media"><img src="${image.dataset.sourceImage}" alt="${esc(image.dataset.imageCaption)}"></div>`);
 const view=e.target.closest('[data-view]');if(view)navigateView(view.dataset.view);
 const objective=e.target.closest('[data-objective-section]');if(objective)navigateView('objectives',{section:objective.dataset.objectiveSection});
 const jump=e.target.closest('[data-jump-section]');if(jump){const section=questionSections.find(s=>s.id===jump.dataset.jumpSection);if(section){state.page=section.pageIndex;state.studyScope=scopePageSelected(pages[section.pageIndex])?'week8':'all';navigateView('guide');document.querySelector(`[data-block="${section.id}"]`).scrollIntoView({block:'start'})}}
});
// Apply source-backed classifications once. Personal notes and review status survive.
for(const q of DATA.questions){
 const mapped=(DATA.question_auto_links||[]).find(x=>x.question_id===q.id),r=qWrite(q.id);
 const corrected=(DATA.question_link_corrections||[]).filter(x=>x.question_id===q.id).map(x=>x.old_section);
 if(corrected.length)r.sections=r.sections.filter(section=>!corrected.includes(section));
 if(mapped){r.sections=[...new Set([...r.sections,...mapped.sections])];r.tags=[...new Set([...r.tags,...mapped.tags])];q.auto_rationale=mapped.rationale}
 else if(!r.tags.length){r.tags=[q.title.replace(/^(AMBOSS|UWorld)\s*[-–]\s*/,'').replace(/^Test \d+, Question \d+:\s*/,'')]}
}

window.addEventListener('popstate',()=>{const view=location.hash.slice(1)||'guide';if(view!==currentView)navigateView(view)});
document.addEventListener('toggle',e=>{if(e.target.matches('[data-nav-group]')&&e.target.isConnected){state.navGroups[e.target.dataset.navGroup]=e.target.open;save()}},true);
