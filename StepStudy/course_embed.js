/* Neuro/Psych is a content surface within Step Study's shared navigation.
   Only our trusted renderer runs here; the private bundle supplies JSON. */
const stepCourseConfig = window.STEP_COURSE_CONFIG;
const stepCourseViews = new Set(['guide','objectives','questions','pathology','drugs','bugs']);
let stepCourseStarted = false, stepCourseScanner = null, stepCourseLexicon = [];
let stepCourseDefinitionSerial = 0, stepCourseHoverSerial = 0, stepCourseHoverTimer;
const stepCourseDefinitions = new Map();
const stepCourseOldQuestionImage = questionImageUrl;
questionImageUrl = function (question) {
  const source=String(question.screenshot_url||question.screenshot||'');
  return source.startsWith('/api/course/asset/')?source:stepCourseOldQuestionImage(question);
};
function stepCoursePost(type, extra = {}) {
  if (window.parent !== window && !referenceOnly)
    window.parent.postMessage({app:'step-course',type,...extra}, location.origin);
}
function stepCourseInfo() {
  const c = guideLengthCounts();
  return {view:currentView,scope:week8Scope()?'week8':'all',chapters:scopePageEntries().length,
    objectives:scopeObjectives().length,questions:DATA.questions.filter(scopeQuestion).length,
    currentPages:c.current/c.unit,expandedPages:c.full/c.unit,revision:studyAppRevision};
}
const stepCourseOldNav = renderNav;
const stepCourseOldOverview = scopeOverviewMarkup;
scopeOverviewMarkup = function () {return stepCourseOldOverview().replace('Choose Week 8 in the sidebar','Choose Week 8 above');};
renderNav = function () {
  stepCourseOldNav();
  const groups = scopeTopicGroups();
  const choose = `<label class="course-chapter-label">Topic / chapter<select id="course-chapter" aria-label="Course topic or chapter"><option value="">Jump to topic…</option>${groups.map(g=>`<optgroup label="${esc(g.title)}">${g.entries.map(({p,i})=>`<option value="${i}">${esc(p.short_title||p.title)}</option>`).join('')}</optgroup>`).join('')}</select></label>`;
  $('#nav').innerHTML = scopeSelectorMarkup() + (currentView==='guide'?choose:'');
  const budget = $('#word-budget');
  const c = guideLengthCounts();
  budget.textContent = `${(c.current/c.unit).toFixed(1)} page eq. now · ${(c.full/c.unit).toFixed(1)} expanded`;
  budget.title = 'Word-based reading length, accounting for learned sections. LOs and comparison sheets are separate.';
  stepCoursePost('view', stepCourseInfo());
};
document.addEventListener('change', event => {
  if (event.target.id !== 'course-chapter' || event.target.value === '') return;
  state.page = Number(event.target.value);save();
  document.getElementById('page-'+state.page)?.scrollIntoView({block:'start',behavior:'instant'});
});
// The frame has no second left rail; the reference divider uses its full width.
referenceRailWidth = function () { return 0; };
const stepCourseOldLength = refreshGuideLength;
refreshGuideLength = function () {
  stepCourseOldLength();
  const c = guideLengthCounts(), budget = $('#word-budget');
  if (budget) budget.textContent = `${(c.current/c.unit).toFixed(1)} page eq. now · ${(c.full/c.unit).toFixed(1)} expanded`;
  if (stepCourseStarted) stepCoursePost('length',stepCourseInfo());
};
// Board-review citations must resolve through the same exact-document catalog.
sourceLink = function (page,label='Cole review') {
  const doc = (DATA.source_catalog?.documents||[]).find(item=>/Neuro Board Review Sept 2026/i.test(item.title||'') || (item.aliases||[]).some(alias=>/Neuro.*Board.*Review.*Sept.*2026/i.test(alias)));
  return doc?`<button class="course-citation" data-course-source="${esc(doc.id)}" data-course-page="${Number(page)||1}">${esc(label)} · PDF ${Number(page)||1}</button>`:`<span class="source-unavailable">${esc(label)} · PDF ${Number(page)||1}</span>`;
};
document.addEventListener('click', event => {
  const cite=event.target.closest('[data-course-source]');
  if(cite)window.openSourceDocumentById(cite.dataset.courseSource,Number(cite.dataset.coursePage),sourceQuery(cite),{node:cite});
});
const stepCourseOldStatus = studyAppStatus;
studyAppStatus = function (message) {
  stepCourseOldStatus(message);
  if(stepCourseStarted && message==='Saved to app') stepCoursePost('saved',stepCourseInfo());
};
function stepCourseElement(tag,text,cls) {
  const element=document.createElement(tag);if(text!==undefined)element.textContent=text;if(cls)element.className=cls;return element;
}
function stepCourseLink(label,url,image=false) {
  const safe=window.MedicalTermCards.safeLink(url,image);if(!safe)return stepCourseElement('span',label);
  const link=stepCourseElement('a',label);link.href=safe;link.target='_blank';link.rel='noopener noreferrer';return link;
}
async function stepCourseLookup(query,isId=false) {
  const key=(isId?'id:':'q:')+query.toLowerCase();if(stepCourseDefinitions.has(key))return stepCourseDefinitions.get(key);
  const response=await fetch('/api/term?'+new URLSearchParams({[isId?'id':'q']:query}),{signal:AbortSignal.timeout(45000)});
  const result=await response.json();
  if(result.ok){stepCourseDefinitions.set(key,result);stepCourseDefinitions.set('id:'+result.record.id,result);}
  return result;
}
function stepCourseDefinitionMarkup(record) {
  const article=stepCourseElement('article',undefined,'course-definition');
  article.append(stepCourseElement('small',record.source?.name||'MDWiki','source-badge'),stepCourseElement('h3',record.title),stepCourseElement('p',record.definition||record.summary));
  const rest=(record.summary||'').startsWith(record.definition||'')?(record.summary||'').slice((record.definition||'').length).trim():record.summary;
  if(rest){const details=stepCourseElement('details');details.append(stepCourseElement('summary','More background'),stepCourseElement('p',rest));article.append(details);}
  const source=record.source||{}, attribution=stepCourseElement('p',undefined,'course-attribution');
  attribution.append(stepCourseLink('Full article',source.url),document.createTextNode(' · contributors · '),stepCourseLink(source.license||'License',source.license_url));
  if(source.history_url)attribution.append(document.createTextNode(' · '),stepCourseLink('History',source.history_url));
  if(source.original_url)attribution.append(document.createTextNode(' · '),stepCourseLink('Original source',source.original_url));
  if(source.original_history_url)attribution.append(document.createTextNode(' · '),stepCourseLink('Original contributors',source.original_history_url));
  article.append(attribution);return article.outerHTML;
}
async function stepCourseDefine(query,isId=false) {
  const serial=++stepCourseDefinitionSerial;stepCourseHoverSerial++;const hover=$('#course-term-hover');if(hover)hover.hidden=true;
  showSidePanel('Medical definition','<p role="status">Looking up the selected term…</p>');
  try {
    const result=await stepCourseLookup(query,isId);if(serial!==stepCourseDefinitionSerial)return;
    if(!result.ok){
      const root=stepCourseElement('div');root.append(stepCourseElement('p',result.error==='source_unavailable'?'The source is unavailable. Cached terms still work.':'Choose a more specific term.'));
      for(const candidate of result.candidates||[]){const b=stepCourseElement('button',candidate.title);b.dataset.courseDefine=candidate.title;root.append(b);}
      showSidePanel(query,root.outerHTML);return;
    }
    const record=result.record;referenceLastTerm=record.title;showSidePanel(record.title,stepCourseDefinitionMarkup(record));
    if(!stepCourseLexicon.some(row=>row.id===record.id)){
      stepCourseLexicon.push({id:record.id,title:record.title,aliases:record.aliases||[]});
      if(stepCourseScanner){stepCourseScanner.compiled=window.MedicalTermCards.compileLexicon(stepCourseLexicon);stepCourseScanner.scan();}
    }
  } catch(error) { if(serial===stepCourseDefinitionSerial)showSidePanel(query,`<p>${esc(error.message)}</p>`); }
}
function stepCourseHover(id,anchor,immediate=false) {
  clearTimeout(stepCourseHoverTimer);const serial=++stepCourseHoverSerial;
  stepCourseHoverTimer=setTimeout(async()=>{
    try {
      const result=await stepCourseLookup(id,true);if(serial!==stepCourseHoverSerial||!anchor.isConnected||!result.ok)return;
      const record=result.record,hover=$('#course-term-hover');hover.replaceChildren(stepCourseElement('strong',record.title),stepCourseElement('p',record.definition),stepCourseElement('small',(record.source?.name||'MDWiki')+' · '+(record.source?.license||'')));
      const b=stepCourseElement('button','Keep beside reading →');b.onclick=()=>stepCourseDefine(id,true);hover.append(b);hover.hidden=false;
      const rect=anchor.getBoundingClientRect();hover.style.left=Math.max(8,Math.min(rect.left,innerWidth-320))+'px';hover.style.top=Math.max(8,Math.min(rect.bottom+8,innerHeight-hover.offsetHeight-8))+'px';
    }catch(_){/* A definition outage must not interrupt course reading. */}
  },immediate?0:350);
}
document.addEventListener('click',event=>{
  const node=event.target.closest('[data-course-define]');if(node)stepCourseDefine(node.dataset.courseDefine);
});
window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==window.parent||event.data?.app!=='step-course')return;
  const message=event.data;
  if(message.type==='navigate'&&stepCourseViews.has(message.view)){
    const unchanged=message.view===currentView && (!message.scope||message.scope===state.studyScope) && (!message.target||message.target===state.studyTarget) && !message.options;
    if(unchanged){stepCoursePost('view',stepCourseInfo());return;}
    if(message.scope==='week8'||message.scope==='all')state.studyScope=message.scope;
    if(['both','step','in-house'].includes(message.target))state.studyTarget=message.target;
    if(message.view==='objectives'&&message.options?.reviewBad){objectiveView='bad';objectiveQuiz='';objectiveSearch='';}
    ensureScopePage();navigateView(message.view,message.options||{});
  } else if(message.type==='definitions') {
    stepCourseScanner?.setEnabled(message.enabled===true);
  } else if(message.type==='reference')openReferenceWorkspace();
});
window.StepCourseReady = async function () {
  stepCourseStarted=true;
  if(stepCourseConfig.publicOnly){const notice=stepCourseElement('aside','Public course text. Import your private bundle to load answered LOs, personal questions, lecture images, books, and scoped Anki cards.','course-public-notice');document.querySelector('.shell').prepend(notice);}
  if(!referenceOnly){
    if(stepCourseConfig.scope)state.studyScope=stepCourseConfig.scope;
    state.studyTarget=stepCourseConfig.target;ensureScopePage();
    navigateView(stepCourseConfig.view);
  }
  const hover=stepCourseElement('div',undefined,'course-term-hover');hover.id='course-term-hover';hover.hidden=true;document.body.append(hover);
  hover.onmouseleave=()=>{stepCourseHoverSerial++;hover.hidden=true;};
  try {
    const result=await (await fetch('/api/terms')).json();stepCourseLexicon=result.records||[];
    stepCourseScanner=window.MedicalTermCards.attach($('#guide'),stepCourseLexicon,{onOpen:id=>stepCourseDefine(id,true),onHover:stepCourseHover});
    stepCoursePost('ready',stepCourseInfo());
  }catch(_){stepCoursePost('ready',stepCourseInfo());}
  window.MedicalSelectionLookup.attach($('#guide'),query=>stepCourseDefine(query));
};
