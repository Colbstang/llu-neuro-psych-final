// Reference controls are outside the scrolling page. A separate reference tab
// communicates only with the app-created opener using a per-session token.
const referenceParams=new URLSearchParams(location.search);
const referenceOnly=referenceParams.get('reference')==='1';
let referenceSession=referenceParams.get('referenceSession')||'',referenceChild=null,referenceChildReady=false,referencePending=null,referenceLastRequest=null;
let referenceReceiving=false,referenceDetached=false,referenceLastTerm='',referenceTermContext='',referenceSpeech=null;
const referenceStateFields=new Set(['bookPageView','bookHighlights','sourcePageView','readingHighlights','readingAutoHighlight','freehandStrokes','freehandLastKey','referenceSources','referenceViews','ankiCardPosition','ankiMediaPosition','imagePositions','guideHighlightBookTopics','referencePaneRatio','bookLabels']);
const referenceMainFields=new Set(['view','page','scope','studyScope','objectiveQuiz','objectiveFilter','objectiveSearch','questionFilter']);
function referenceClone(value){return value===undefined?undefined:JSON.parse(JSON.stringify(value))}
function referenceEqual(a,b){
 if(a===b)return true;
 if(a===null||b===null||typeof a!=='object'||typeof b!=='object')return false;
 if(Array.isArray(a)||Array.isArray(b))return Array.isArray(a)&&Array.isArray(b)&&a.length===b.length&&a.every((value,index)=>referenceEqual(value,b[index]));
 const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(key=>Object.prototype.hasOwnProperty.call(b,key)&&referenceEqual(a[key],b[key]));
}
function referenceObject(value){return value!==null&&typeof value==='object'&&!Array.isArray(value)}
function referenceMergeDelta(before,after,current){
 if(referenceEqual(before,after))return referenceClone(current);
 if(referenceObject(after)&&referenceObject(before)){
  const merged=referenceObject(current)?referenceClone(current):{};
  for(const key of new Set([...Object.keys(before),...Object.keys(after)])){
   if(!(key in after)){if(key in before)delete merged[key];continue}
   if(!referenceEqual(before[key],after[key]))merged[key]=referenceMergeDelta(before[key],after[key],merged[key]);
  }return merged;
 }
 if(Array.isArray(after)&&Array.isArray(before)&&[...before,...after].every(x=>referenceObject(x)&&x.id)){
  const prior=new Map(before.map(x=>[x.id,x])),next=new Map(after.map(x=>[x.id,x]));
  const merged=new Map((Array.isArray(current)?current:[]).filter(x=>x?.id).map(x=>[x.id,referenceClone(x)]));
  for(const [id,old] of prior)if(!next.has(id))merged.delete(id);
  for(const [id,item] of next)if(!referenceEqual(prior.get(id),item))merged.set(id,referenceMergeDelta(prior.get(id),item,merged.get(id)));
  return [...merged.values()];
 }
 return referenceClone(after);
}
let referenceStateSnapshot=referenceClone(state);
const referenceOriginalSave=save;
function referenceMergeIncoming(incoming){
 if(!referenceObject(incoming))return;
 const current=state,merged=referenceMergeDelta(referenceStateSnapshot,current,incoming)||{};
 if(!referenceOnly)for(const key of referenceMainFields)if(key in current)merged[key]=current[key];
 state={...current,...merged};referenceStateSnapshot=referenceClone(incoming);
 if(typeof window.refreshReadingAnnotations==='function')window.refreshReadingAnnotations();
}
save=function(){
 try{
  let latest=referenceClone(referenceStateSnapshot);try{const stored=localStorage.getItem(KEY);if(stored)latest=JSON.parse(stored)}catch{}
  const proposed=referenceOnly?Object.fromEntries(Object.entries(state).filter(([key])=>referenceStateFields.has(key))):state;
  const baseline=referenceOnly?Object.fromEntries(Object.entries(referenceStateSnapshot).filter(([key])=>referenceStateFields.has(key))):referenceStateSnapshot;
  const merged=referenceMergeDelta(baseline,proposed,latest)||latest;
  localStorage.setItem(KEY,JSON.stringify(merged));
  const own=state;state={...state,...merged};if(!referenceOnly)for(const key of referenceMainFields)if(key in own)state[key]=own[key];
  referenceStateSnapshot=referenceClone(state);
  const status=$('#save-status');if(status)status.textContent='Saved on this browser';
  referencePeerMessage({type:'state',state:merged});
 }catch{referenceOriginalSave()}
};
window.addEventListener('storage',event=>{if(event.key===KEY&&event.newValue){try{referenceMergeIncoming(JSON.parse(event.newValue))}catch{}}});
function referencePeerMessage(message){
 const peer=referenceOnly?window.opener:referenceChild;
 if(!peer||peer.closed||!referenceSession)return;
 try{peer.postMessage({app:'llu-reference',session:referenceSession,...message},'*')}catch{}
}
function referenceSafePayload(value){
 if(typeof value==='string'){
  return value.replace(/blob:[^"'<>\s]+/g,source=>{const id=typeof embeddedMediaIDs!=='undefined'?embeddedMediaIDs.get(source):null;return id?'@asset:'+id:source});
 }
 if(Array.isArray(value))return value.map(referenceSafePayload);
 if(referenceObject(value))return Object.fromEntries(Object.entries(value).filter(([key,item])=>key!=='node'&&key!=='element'&&!(item instanceof Node)).map(([key,item])=>[key,referenceSafePayload(item)]));
 return value;
}
function referenceResolvePayload(value){
 if(typeof value==='string'&&value.includes('@asset:'))return resolveMedia(value);
 if(Array.isArray(value))return value.map(referenceResolvePayload);
 if(referenceObject(value))return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,referenceResolvePayload(item)]));
 return value;
}
function referenceRoute(kind,payload){
 const request={type:'request',kind,payload:referenceSafePayload(payload),courseSources:referenceSafePayload(state.referenceSources||{})};
 if(referenceOnly){if(!referenceReceiving&&kind!=='panel'){referenceLastRequest=request;referencePeerMessage({type:'view',request})}return false}
 if(referenceReceiving||!referenceDetached)return false;
 referenceLastRequest=request;
 if(!referenceChild||referenceChild.closed){referenceDetached=false;document.body.classList.remove('reference-detached');return false}
 if(referenceChildReady)referencePeerMessage(request);else referencePending=request;
 return true;
}
function referenceHandleRequest(message){
 referenceLastRequest=message;
 referenceReceiving=true;
 try{
  if(message.courseSources)state.referenceSources={...state.referenceSources,...message.courseSources};
  const payload=referenceResolvePayload(message.payload||{});
  if(message.kind==='anki')openAnkiContext(payload);
  else if(message.kind==='topic'){referenceOrigin={topic:payload.topic,text:payload.originText||'',element:null};openStudyReference(payload.topic,payload.book)}
  else if(message.kind==='source')openExactSource(payload.doc,payload.page,payload.query,null,payload.linkedPage,payload.forcePage);
  else if(message.kind==='images')openImageGallery(payload);
  else if(message.kind==='rapid')openRapid();
  else if(message.kind==='search')openAnkiContext({kind:'selection',text:payload.query,searchQuery:payload.query,searchScope:payload.scope,title:'Search · '+payload.query.slice(0,80),topics:[]});
  else if(message.kind==='panel')showSidePanel(payload.title,payload.html,payload.options||{});
  else openReferenceWorkspace();
 }finally{referenceReceiving=false}
}
window.addEventListener('message',event=>{
 const message=event.data,peer=referenceOnly?window.opener:referenceChild;
 if(!peer||event.source!==peer||message?.app!=='llu-reference'||message.session!==referenceSession)return;
 if(message.type==='ready'&&!referenceOnly){referenceChildReady=true;referencePeerMessage({type:'state',state});if(referencePending){referencePeerMessage(referencePending);referencePending=null}return}
 if(message.type==='state'){referenceMergeIncoming(message.state);return}
 if(message.type==='view'&&!referenceOnly){referenceLastRequest=message.request;return}
 if(message.type==='request'&&referenceOnly){referenceHandleRequest(message);return}
 if(message.type==='dock'&&!referenceOnly){if(message.request)referenceLastRequest=message.request;dockReferenceWorkspace()}
});
const referenceLocalOpenAnki=openAnkiContext;
openAnkiContext=function(options){if(referenceRoute('anki',options))return;return referenceLocalOpenAnki(options)};window.openAnkiContext=openAnkiContext;
const referenceLocalOpenTopic=openStudyReference;
openStudyReference=function(topic,book){if(referenceRoute('topic',{topic,book,originText:referenceOrigin?.text||''}))return;return referenceLocalOpenTopic(topic,book)};
const referenceLocalOpenSource=openExactSource;
openExactSource=function(doc,page,query,node,linkedPage=page,forcePage=false){if(referenceRoute('source',{doc,page,query,linkedPage,forcePage}))return;return referenceLocalOpenSource(doc,page,query,node,linkedPage,forcePage)};
const referenceLocalOpenImages=openImageGallery;
openImageGallery=function(options){if(referenceRoute('images',options))return;return referenceLocalOpenImages(options)};
const referenceLocalOpenRapid=openRapid;
openRapid=function(){if(referenceRoute('rapid',{}))return;return referenceLocalOpenRapid()};$('#rapid-button').onclick=openRapid;
const referenceLocalFetch=fetchSemanticCards;
fetchSemanticCards=function(context){if(referenceRoute('search',{query:context.searchQuery||context.text,scope:context.searchScope||'all'}))return;return referenceLocalFetch(context)};window.fetchSemanticCards=fetchSemanticCards;
const referenceLocalShow=showSidePanel;
showSidePanel=function(title,html,options={}){
 if(referenceRoute('panel',{title,html,options:{bookPanel:options.bookPanel,keepScroll:options.keepScroll,keepZoom:options.keepZoom}}))return;
 const alreadyOpen=!$('#side-panel').hidden,mainY=window.scrollY;
 const sameRapid=alreadyOpen&&$('#rapid-card')&&String(html).includes('id="rapid-card"');
 const result=referenceLocalShow(title,html,{...options,keepScroll:options.keepScroll??!!sameRapid});
 if(referenceTermContext!==String(title)){referenceLastTerm='';referenceTermContext=String(title)}
 window.refreshReferenceSearchControls?.(window.referencePanelSearchContext?.());
 referenceApplySize();
 if(alreadyOpen&&!referenceOnly)requestAnimationFrame(()=>{if(Math.abs(window.scrollY-mainY)>1)window.scrollTo({top:mainY,behavior:'instant'})});
 return result;
};
function referenceRailWidth(){return window.innerWidth<=700?72:96}
function referenceApplySize(){
 const header=document.querySelector('body>header');if(header&&!referenceOnly)document.documentElement.style.setProperty('--study-header-height',header.getBoundingClientRect().height+'px');
 if(referenceOnly)return;
 const available=Math.max(240,window.innerWidth-referenceRailWidth()),ratio=Math.max(.3,Math.min(.75,Number(state.referencePaneRatio)||.5));
 document.documentElement.style.setProperty('--reference-width',Math.round(available*ratio)+'px');
 const divider=$('#reference-divider');if(divider)divider.setAttribute('aria-valuenow',String(Math.round(ratio*100)));
}
function referenceSetRatio(ratio){state.referencePaneRatio=Math.max(.3,Math.min(.75,ratio));referenceApplySize()}
function openReferenceWorkspace(){
 if(referenceRoute('empty',{}))return;
 const documents=(DATA.source_catalog?.documents||[]).filter(doc=>['First Aid','Pathoma'].includes(doc.id)||doc.kind==='supplement'),hasCards=(ankiLibrary.notes||[]).length>0;
 const message=documents.length||hasCards?'Search your cards, books, lecture slides and notes. Choose a match to open its exact page.':'This public edition has no linked Anki cards or source PDFs. Add a private Anki export or source pack to enable local matches.';
 showSidePanel('References',`<section class="reference-empty"><h3>Look up a term or sentence</h3><p>${message}</p><div class="reference-library">${documents.map(doc=>`<button data-reference-library="${esc(doc.id)}">${esc(doc.title)} · ${doc.page_count} pages</button>`).join('')}</div></section>`);
}
function detachReferenceWorkspace(){
 if(referenceOnly)return;
 if(referenceChild&&!referenceChild.closed){referenceChild.focus();return}
 referenceSession=crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+Math.random().toString(36).slice(2);
 const url=new URL(location.href);url.searchParams.set('reference','1');url.searchParams.set('referenceSession',referenceSession);url.hash='';
 referenceChild=window.open(url.href,'llu-reference-'+referenceSession);
 if(!referenceChild){$('#reference-listen-status').textContent='Allow this app to open its reference tab, or keep using the split view.';return}
 referenceChildReady=false;referenceDetached=true;document.body.classList.add('reference-detached');
 referencePending=referenceLastRequest||(sourceViewerActive?{type:'request',kind:'source',payload:{doc:sourceViewerActive.document,page:Number(sourceViewerActive.result.page),linkedPage:sourceViewerActive.linkedPage,query:sourceViewerActive.query,forcePage:true}}:ankiContext?{type:'request',kind:'anki',payload:{kind:ankiContext.kind,text:ankiContext.text,topic:ankiContext.topic,topics:ankiContext.topics,title:ankiContext.title,view:ankiReferenceMode,searchQuery:ankiContext.searchQuery,searchScope:ankiContext.searchScope}}:{type:'request',kind:'empty',payload:{}});
 referencePending=referenceSafePayload(referencePending);
 $('#side-panel').hidden=true;document.body.classList.remove('with-panel');
 const button=$('#references-button');button.textContent='References ↗';button.title='Send lookups to your reference tab';
}
function dockReferenceWorkspace(){
 if(referenceOnly){referencePeerMessage({type:'dock',request:referenceLastRequest});return}
 referenceDetached=false;referenceChildReady=false;document.body.classList.remove('reference-detached');
 if(referenceChild&&!referenceChild.closed)referenceChild.close();referenceChild=null;
 $('#references-button').textContent='References';
 if(referenceLastRequest)referenceHandleRequest(referenceLastRequest);else openReferenceWorkspace();
}
function referenceSpeakText(value){
 const term=String(value||'').replace(/\s+/g,' ').trim();
 if(!term||term.length>180||term.split(' ').length>24){$('#reference-listen-status').textContent='Select a medical term or short phrase to hear it.';return}
 const synthesis=window.speechSynthesis;
 const nativeSpeech=window.webkit?.messageHandlers?.nativeSpeak;
 if(nativeSpeech){
  if(referenceSpeech){nativeSpeech.postMessage({stop:true});referenceSpeech=null;referenceUpdateSpeechControls(false);$('#reference-listen-status').textContent='Stopped.';return}
  referenceSpeech={native:true};referenceLastTerm=term;referenceUpdateSpeechControls(true);$('#reference-listen-status').textContent='Listening: '+term;nativeSpeech.postMessage({text:term});return;
 }
 if(!synthesis||typeof SpeechSynthesisUtterance==='undefined'){$('#reference-listen-status').textContent='Speech is unavailable in this browser.';return}
 if(referenceSpeech){synthesis.cancel();referenceSpeech=null;referenceUpdateSpeechControls(false);$('#reference-listen-status').textContent='Stopped.';return}
 const voices=synthesis.getVoices(),voice=voices.find(v=>v.localService&&/^en[-_]/i.test(v.lang))||voices.find(v=>v.localService&&v.lang==='en');
 if(!voice){$('#reference-listen-status').textContent='No installed English voice is available yet. Try Listen again once voices load.';return}
 referenceLastTerm=term;
 const spoken=term.replace(/\bIFN\s*[-–]?\s*(?:beta|β)(?!\w)/gi,'interferon beta');
 const utterance=new SpeechSynthesisUtterance(spoken);utterance.voice=voice;utterance.lang=voice.lang;utterance.rate=.86;
 referenceSpeech=utterance;referenceUpdateSpeechControls(true);
 $('#reference-listen-status').textContent='Listening: '+term;
 utterance.onend=()=>{referenceSpeech=null;referenceUpdateSpeechControls(false);$('#reference-listen-status').textContent='Pronunciation · '+term};
 utterance.onerror=()=>{referenceSpeech=null;referenceUpdateSpeechControls(false);$('#reference-listen-status').textContent='Could not play this term. Try Listen again.'};
 synthesis.cancel();synthesis.speak(utterance);
}
window.referenceNativeSpeechEnded=function(){referenceSpeech=null;referenceUpdateSpeechControls(false);$('#reference-listen-status').textContent='Pronunciation · '+referenceLastTerm};
function referenceUpdateSpeechControls(speaking){document.querySelectorAll('[data-reference-listen],[data-selection-listen]').forEach(button=>{button.textContent=speaking?'■ Stop':'🔊 Listen';button.setAttribute('aria-pressed',String(speaking))})}
function referenceListenTerm(){const title=$('#side-title')?.textContent||'';return (window.getSelection()?.toString().trim()||(referenceTermContext===title?referenceLastTerm:'')||($('#side-content .background-reference')?title:'')||sourceViewerActive?.query||ankiContext?.topic||ankiContext?.searchQuery||ankiContext?.text||title).replace(/^source:/,'')}
function referenceSetupControls(){
 // Asking for voices during startup lets the browser load its installed list
 // before the first Listen click, without speaking anything automatically.
 window.speechSynthesis?.getVoices();
 const panel=$('#side-panel'),head=panel.querySelector('.side-panel-head');
 const actions=document.createElement('div');actions.className='reference-workspace-actions';actions.innerHTML='<button data-reference-listen aria-pressed="false" title="Hear the selected term or reference heading">🔊 Listen</button><button data-reference-detach title="Send lookups to a separate tab">Open reference tab ↗</button><button data-reference-dock>Dock beside guide</button>';head.insertBefore(actions,$('#close-side'));
 const status=document.createElement('p');status.id='reference-listen-status';status.className='reference-listen-status';status.setAttribute('role','status');head.after(status);
 const host=document.createElement('div');host.id='reference-search-host';status.after(host);
 const divider=document.createElement('div');divider.id='reference-divider';divider.className='reference-divider';divider.setAttribute('role','separator');divider.setAttribute('aria-orientation','vertical');divider.setAttribute('aria-label','Resize study and reference panes');divider.setAttribute('aria-valuemin','30');divider.setAttribute('aria-valuemax','75');divider.tabIndex=0;document.body.append(divider);
 let resizing=false;
 divider.addEventListener('pointerdown',event=>{event.preventDefault();resizing=true;divider.setPointerCapture(event.pointerId);document.body.classList.add('reference-resizing')});
 divider.addEventListener('pointermove',event=>{if(resizing)referenceSetRatio((window.innerWidth-event.clientX)/(window.innerWidth-referenceRailWidth()))});
 const finishResize=()=>{if(!resizing)return;resizing=false;document.body.classList.remove('reference-resizing');save()};divider.addEventListener('pointerup',finishResize);divider.addEventListener('pointercancel',finishResize);
 divider.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home'].includes(event.key))return;event.preventDefault();referenceSetRatio(event.key==='Home'?.5:(Number(state.referencePaneRatio)||.5)+(event.key==='ArrowLeft'?.025:-.025));save()});
 const button=document.createElement('button');button.id='references-button';button.textContent='References';button.onclick=openReferenceWorkspace;document.querySelector('.toolbar').insertBefore(button,$('#rapid-button'));
 window.addEventListener('resize',referenceApplySize);referenceApplySize();
 document.addEventListener('selectionchange',()=>{const text=window.getSelection()?.toString().replace(/\s+/g,' ').trim();if(text&&text.length<=180&&text.split(' ').length<=24){referenceLastTerm=text;referenceTermContext=$('#side-title')?.textContent||''}});
 const addListen=()=>{document.querySelectorAll('.selection-tools').forEach(tools=>{if(tools.querySelector('[data-selection-listen]'))return;const button=document.createElement('button');button.type='button';button.dataset.selectionListen='';button.textContent='🔊 Listen';button.title='Hear this term';tools.append(button)})};
 new MutationObserver(addListen).observe(document.body,{childList:true,subtree:true});addListen();
 document.addEventListener('click',event=>{
  if(event.target.closest('[data-reference-listen],[data-selection-listen]')){event.preventDefault();referenceSpeakText(referenceListenTerm());return}
  if(event.target.closest('[data-reference-detach]')){detachReferenceWorkspace();return}
  if(event.target.closest('[data-reference-dock]')){dockReferenceWorkspace();return}
  const library=event.target.closest('[data-reference-library]');if(library){window.openSourceDocumentById(library.dataset.referenceLibrary,1,'');return}
  const background=event.target.closest('[data-open-background-result]');if(background){const entry=ankiContext?.backgroundMatches?.[Number(background.dataset.openBackgroundResult)];if(!entry)return;const url=entry.url&&/^https?:\/\//.test(entry.url)?entry.url:'';referenceLastTerm=entry.title;showSidePanel(entry.title,`<article class="background-reference"><div class="eyebrow">BACKGROUND REFERENCE</div><h3>${esc(entry.title)}</h3><div class="reference-definition">${esc(entry.summary||entry.excerpt||'')}</div><p class="source-links">${url?`<a href="${esc(url)}" target="_blank" rel="noopener">${esc(entry.source||'Source')}</a>`:esc(entry.source||'')}${entry.source_updated?' · Updated '+esc(entry.source_updated):''}${entry.fetched_at?' · Local copy '+esc(entry.fetched_at):''}${entry.license?'<br>'+esc(entry.license):''}</p><button data-background-return>← Search results</button></article>`);return}
  if(event.target.closest('[data-background-return]'))renderAnkiReference({keepScroll:true});
 });
 if(referenceOnly){document.body.classList.add('reference-only');openReferenceWorkspace();referencePeerMessage({type:'ready'})}
}
referenceSetupControls();
