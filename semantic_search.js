// Sentence embeddings are served locally. Explicit topic links retain their
// curated image-first view; selecting a sentence starts with eight close cards.
const semanticEndpoint=window.STUDY_APP_CONFIG?.apiBase||'http://127.0.0.1:8768';
function semanticRequestHeaders(){return {'Content-Type':'application/json',...(window.STUDY_APP_CONFIG?{'X-LLU-App-Token':window.STUDY_APP_CONFIG.token}:{})}}
const referenceFamilyOptions=[['first_aid','First Aid'],['pathoma','Pathoma'],['mehlman','Mehlman'],['in_house','In-house notes / slides']];
const referenceFamilyDefaults=referenceFamilyOptions.map(([key])=>key);
function selectedReferenceFamilies(context){const selected=context?.referenceSearchFamilies??state.referenceSearchFamilies;return Array.isArray(selected)?referenceFamilyDefaults.filter(key=>selected.includes(key)):referenceFamilyDefaults.slice()}
state.referenceSearchFamilies=selectedReferenceFamilies();
window.referenceSearchFamiliesMarkup=function(context){const selected=new Set(selectedReferenceFamilies(context));return `<fieldset class="reference-family-selector" data-reference-family-selector><legend>Reference families</legend>${referenceFamilyOptions.map(([key,label])=>`<label><input type="checkbox" data-reference-family value="${key}" ${selected.has(key)?'checked':''}><span>${label}</span></label>`).join('')}</fieldset>`};
const referenceFamilyContainers=new WeakSet();
window.setupReferenceSearchFamilies=function(container,contextGetter=()=>window.referencePanelSearchContext?.()){if(!container||referenceFamilyContainers.has(container))return;referenceFamilyContainers.add(container);container.addEventListener('change',event=>{if(!event.target.matches('[data-reference-family]'))return;const families=[...container.querySelectorAll('[data-reference-family]:checked')].map(input=>input.value);state.referenceSearchFamilies=referenceFamilyDefaults.filter(key=>families.includes(key));save();const context=contextGetter?.();if(context){context.referenceSearchFamilies=state.referenceSearchFamilies.slice();if(context.searchQuery||context.text)fetchSemanticCards(context)}})};
let semanticRequestSerial=0,semanticAbort=null;
const semanticResultCache=new Map();
function semanticCacheKey(request){return JSON.stringify([request.query,request.kind,request.search_scope,[...request.reference_families].sort()])}
const lexicalCardResults=ankiCardResults,legacyOpenAnkiContext=openAnkiContext,legacyContextMarkup=ankiContextMarkup;
const legacyAnkiCardsMarkup=ankiCardsMarkup;
ankiCardsMarkup=function(){
 if(ankiContext?.searchScope&&ankiContext.searchScope!=='all'&&ankiContext.searchScope!=='cards')return '';
 return legacyAnkiCardsMarkup();
};
ankiCardResults=function(context=ankiContext){
 if(context?.searchScope&&context.searchScope!=='all'&&context.searchScope!=='cards')return [];
 if(!context||!Array.isArray(context.semanticCardIds))return lexicalCardResults(context);
 const query=ankiNormalize(context.filterText||''),hits=new Map(context.matches.map(h=>[Number(h.id),h]));
 return context.semanticCardIds.map(id=>{
  const card=ankiCards.get(Number(id)),note=card&&ankiNotes.get(Number(card.note));if(!note)return null;
  if(context.filter!=='all'&&note.kind!==context.filter)return null;
  if(query&&!ankiNormalize(note.plain+' '+ankiPlain(note.extraHtml)).includes(query))return null;
  const hit=hits.get(Number(note.id));return {card,note,reasons:hit?.reasons||[],score:hit?.score||0};
 }).filter(Boolean);
};
ankiContextMarkup=function(){
 const content=legacyContextMarkup(),context=ankiContext;if(!context?.retrieval)return content;
 const status=semanticSearchStatus(context),showBroaden=['all','cards'].includes(context.searchScope||'all');
 return `${content}<div class="semantic-search-status" role="status"><span>${esc(status)}</span>${context.retrieval==='fallback'?'<small>Run Start Study Guide.command to enable local search.</small><button data-semantic-retry>Retry search</button>':''}${showBroaden&&['semantic','empty'].includes(context.retrieval)?'<button data-semantic-broaden>Broaden text matches</button>':''}</div>${sourceSearchResultsMarkup(context)}`;
};
function semanticSearchStatus(context){
 const scope=context.searchScope||'all';
 if(context.retrieval==='loading')return `Searching ${scope==='all'?'all sources':scope==='sources'?'slides / notes':scope}…`;
 const counts={cards:ankiCardResults(context).length,books:Object.values(context.bookMatches||{}).reduce((sum,hits)=>sum+(Array.isArray(hits)?hits.length:0),0)+(context.mehlmanMatches||[]).length,sources:(context.sourceMatches||context.sources||[]).length,background:(context.backgroundMatches||context.background||[]).length};
 const labels={cards:'Cards',books:'Books','sources':'Slides / notes',background:'Background'};
 const keys=scope==='all'?['cards','books','sources','background']:[scope];
 const found=keys.filter(key=>counts[key]>0).map(key=>`${labels[key]}: ${counts[key]}`);
 if(found.length)return found.join(' · ');
 if(context.retrieval==='fallback')return scope==='all'?'No local search results':'No matches in '+(labels[scope]||scope);
 return `No matches in ${scope==='all'?'all sources':labels[scope]||scope}`;
}
function referenceSearchControls(context){
 context||={text:'',searchScope:'all'};
 const query=context.searchQuery??context.text??'',selected=context.searchScope||'all';
 return `<form class="reference-search-controls" data-reference-search-form><label>Search<input type="search" data-reference-search-query value="${esc(query)}" placeholder="Search cards, books, slides, or notes" aria-label="Search study sources"></label><label>In<select data-reference-search-source aria-label="Choose sources to search"><option value="all" ${selected==='all'?'selected':''}>All sources</option><option value="cards" ${selected==='cards'?'selected':''}>Cards</option><option value="books" ${selected==='books'?'selected':''}>Books</option><option value="sources" ${selected==='sources'?'selected':''}>Slides / notes</option><option value="background" ${selected==='background'?'selected':''}>Background</option></select></label><button type="submit">Search</button></form>`;
}
window.referenceSearchControls=referenceSearchControls;
window.refreshReferenceSearchControls=function(context=ankiContext){const host=document.getElementById('reference-search-host');if(host){const active=context||{text:'',searchScope:'all'};host.innerHTML=referenceSearchControls(active)+window.referenceSearchFamiliesMarkup(active);window.setupReferenceSearchFamilies(host,()=>window.referencePanelSearchContext?.()||ankiContext)}};
window.referencePanelSearchContext=function(){return ankiContext||window.referenceSearchFallbackContext||null;};
function resultDocumentId(hit){return hit?.documentId||hit?.document_id||hit?.sourceId||hit?.source_id||hit?.id||''}
function resultPage(hit){return Number(hit?.physicalPage||hit?.page||hit?.pageNumber||hit?.page_number||1)||1}
function referenceResultExcerpt(value,query){
 const text=String(value||'').replace(/\s+/g,' ').trim();if(text.length<=230)return text;
 const lower=text.toLowerCase(),phrase=String(query||'').toLowerCase();let at=lower.indexOf(phrase);
 if(at<0)for(const term of phrase.match(/[\p{L}\p{N}]+/gu)||[]){if(term.length>3&&(at=lower.indexOf(term))>=0)break}
 const start=Math.max(0,at-60),snippet=text.slice(start,start+230);return (start?'…':'')+snippet+(start+230<text.length?'…':'');
}
function sourceResultButton(hit,query,label=''){
 const id=resultDocumentId(hit),page=resultPage(hit),title=hit.title||hit.label||hit.name||'Source document',excerpt=referenceResultExcerpt(hit.excerpt||hit.text||hit.snippet||'',query);
 const localId=(DATA.source_catalog?.documents||[]).some(doc=>doc.id===id);
 const attribution=[label||hit.source||hit.attribution,hit.source_updated?`Updated ${hit.source_updated}`:''].filter(Boolean).join(' · ');
 if(id&&localId)return `<button class="reference-source-result" data-open-source-result data-document-id="${esc(id)}" data-page="${page}" data-query="${esc(query)}"><b>${esc(title)} · PDF ${page}${attribution?` · ${esc(attribution)}`:''}</b>${excerpt?`<span>${esc(excerpt)}</span>`:''}</button>`;
 const url=hit.url||hit.href;if(url&&/^https?:\/\//i.test(url))return `<a class="reference-source-result" href="${esc(url)}" target="_blank" rel="noopener"><b>${esc(title)}${label?` · ${esc(label)}`:''}</b>${excerpt?`<span>${esc(excerpt)}</span>`:''}</a>`;
 return '';
}
function backgroundResultButton(hit,index){
 const title=hit.title||hit.label||'Background source',attribution=hit.source||hit.attribution||'',excerpt=hit.excerpt||hit.summary||'';
 return `<button class="reference-source-result reference-background-result" type="button" data-open-background-result="${index}" data-index="${index}"><b>${esc(title)}${attribution?` · ${esc(attribution)}`:''}</b>${excerpt?`<span>${esc(excerpt)}</span>`:''}</button>`;
}
function sourceSearchResultsMarkup(context){
 const books=context.bookMatches||{},sources=context.sourceMatches||context.sources||[],background=context.backgroundMatches||context.background||[],mehlman=context.mehlmanMatches||books.Mehlman||[];
 const scope=context.searchScope||'all',showAll=scope==='all';
 const query=context.searchQuery||context.text||'';
 const sourceRows=(Array.isArray(sources)?sources:[]).slice(0,5).map(hit=>sourceResultButton(hit,query,'In-house notes / slides')).join('');
 const bookRows=['First Aid','Pathoma'].map(name=>[name,(books[name]||[])]).concat([['Mehlman',mehlman]]).map(([name,hits])=>hits.length?`<div class="reference-passage-group"><strong>${name}</strong>${hits.slice(0,3).map(hit=>sourceResultButton(hit,query,name)).join('')}</div>`:'').join('');
 const backgroundRows=(Array.isArray(background)?background:[]).slice(0,4).map((hit,index)=>backgroundResultButton(hit,index)).join('');
 const bookCount=scope==='all'||scope==='books'?Object.values(books).reduce((sum,hits)=>sum+(Array.isArray(hits)?hits.length:0),0)+(Array.isArray(mehlman)?mehlman.length:0):0;
 const sourceCount=scope==='all'||scope==='sources'?(Array.isArray(sources)?sources.length:0):0,backgroundCount=scope==='all'||scope==='background'?(Array.isArray(background)?background.length:0):0;
 const noFamilies=selectedReferenceFamilies(context).length===0;
 if(!bookCount&&!sourceCount&&!backgroundCount&&!noFamilies)return '';
 return `<section class="reference-search-results" aria-label="Search results">${noFamilies?'<p role="status">No reference sources selected.</p>':''}${bookCount?`<details open><summary>Standards <span>${bookCount}</span></summary><div>${bookRows||'<p>No local standards passages matched.</p>'}</div></details>`:''}${sourceCount?`<details open><summary>In-house notes / slides <span>${sourceCount}</span></summary><div>${sourceRows||'<p>No local in-house passages matched.</p>'}</div></details>`:''}${backgroundCount?`<details ${showAll?'':'open'}><summary>Background <span>${backgroundCount}</span></summary><div>${backgroundRows||'<p>No background source passages matched.</p>'}</div></details>`:''}</section>`;
}
function narrowLexicalContext(context){
 const oldText=context.text;context.text=context.searchQuery||oldText;context.matches=ankiMatchContext(context);context.text=oldText;
 context.semanticCardIds=lexicalCardResults(context).slice(0,12).map(item=>Number(item.card.id));
 const notes=new Set(context.semanticCardIds.map(id=>Number(ankiCards.get(id)?.note)));
 context.matches=context.matches.filter(h=>notes.has(Number(h.id)));
 context.retrieval='fallback';
}
async function fetchSemanticCards(context){
 const serial=++semanticRequestSerial;semanticAbort?.abort();const controller=new AbortController();semanticAbort=controller;
 context.retrieval='loading';context.matches=[];context.semanticCardIds=[];context.bookMatches={'First Aid':[],'Pathoma':[]};context.mehlmanMatches=[];context.sourceMatches=[];context.backgroundMatches=[];renderAnkiReference();window.refreshReferenceSearchControls(context);
 const timer=setTimeout(()=>controller.abort(),45000);
 try{
  const query=String(context.searchQuery??context.text??'').slice(0,4000),scope=context.searchScope||'all',referenceFamilies=selectedReferenceFamilies(context);
  const request={query,kind:context.filter||'all',search_scope:scope,reference_families:referenceFamilies,limit:8,include_books:scope==='all'||scope==='books',include_sources:scope==='all'||scope==='sources',include_background:scope==='all'||scope==='background'},key=semanticCacheKey(request),cached=semanticResultCache.get(key);
  let result;
  if(cached&&Date.now()-cached.at<300000)result=cached.result;
  else{const response=await fetch(semanticEndpoint+'/search',{method:'POST',headers:semanticRequestHeaders(),body:JSON.stringify(request),signal:controller.signal});if(!response.ok)throw Error('Local search unavailable');result=await response.json();}
  if(serial!==semanticRequestSerial||ankiContext!==context)return;
  if(result.method!=='local-sentence-embeddings'||!Array.isArray(result.matches))throw Error('Unexpected local search response');
  semanticResultCache.set(key,{at:Date.now(),result});while(semanticResultCache.size>30)semanticResultCache.delete(semanticResultCache.keys().next().value);
  const cards=result.matches.filter(h=>ankiCards.has(Number(h.cardId))&&ankiNotes.has(Number(h.noteId))&&Number(ankiCards.get(Number(h.cardId)).note)===Number(h.noteId));
  context.semanticCardIds=cards.map(h=>Number(h.cardId));context.matches=[];
  for(const hit of cards){if(context.matches.some(n=>n.id===Number(hit.noteId)))continue;context.matches.push({id:Number(hit.noteId),score:hit.score,reasons:[hit.reason]})}
  context.bookMatches=result.books||{'First Aid':[],'Pathoma':[]};context.mehlmanMatches=result.mehlman||[];
  context.sourceMatches=result.sources||result.sourceMatches||result.notes||result.slides||[];
  context.backgroundMatches=result.background||result.backgroundMatches||result.background_sources||[];
  context.retrieval=cards.length?'semantic':'empty';
 }catch(error){if(serial!==semanticRequestSerial||ankiContext!==context)return;narrowLexicalContext(context)}
 finally{clearTimeout(timer);if(serial===semanticRequestSerial&&ankiContext===context&&!$('#side-panel').hidden){ankiCardRevealed=false;renderAnkiReference();window.refreshReferenceSearchControls(context)}}
}
window.fetchSemanticCards=fetchSemanticCards;
openAnkiContext=function(options){
 if(options.fromNote||options.kind==='topic'||options.kind==='connections')return legacyOpenAnkiContext(options);
 ankiContext=ankiMakeContext(options);ankiContext.searchScope=options.searchScope||'all';ankiContext.referenceSearchFamilies=selectedReferenceFamilies(options);if(options.searchQuery!==undefined)ankiContext.searchQuery=options.searchQuery;activeRef=ankiContext.topic||ankiContext.topics.find(t=>ankiBookAvailable(t,'First Aid')||ankiBookAvailable(t,'Pathoma'))||'';
 if(!ankiContext.topic&&activeRef)ankiContext.topic=activeRef;if(options.node&&activeRef)setReferenceContext(activeRef,options.node);
 ankiCardRevealed=false;ankiCurrentCard=options.cardId||Number(state.ankiCardPosition[ankiContext.key])||0;ankiReferenceMode=options.view||'cards';
 fetchSemanticCards(ankiContext);
};
window.openAnkiContext=openAnkiContext;
document.addEventListener('click',e=>{
 if(e.target.closest('[data-semantic-retry]')&&ankiContext)fetchSemanticCards(ankiContext);
 if(e.target.closest('[data-semantic-broaden]')&&ankiContext){delete ankiContext.semanticCardIds;delete ankiContext.retrieval;ankiContext.matches=ankiMatchContext(ankiContext);renderAnkiReference()}
 if(e.target.closest('[data-anki-filter]')&&ankiContext?.retrieval)fetchSemanticCards(ankiContext);
});
document.addEventListener('submit',e=>{
 const form=e.target.closest('[data-reference-search-form]');if(!form)return;
 e.preventDefault();const query=form.querySelector('[data-reference-search-query]')?.value.trim()||'';
 const scope=form.querySelector('[data-reference-search-source]')?.value||'all';
 ankiContext=ankiMakeContext({kind:'selection',text:query,title:query?`Search · ${query.slice(0,72)}`:'Search study sources',topics:[],blockId:''});
 ankiContext.searchQuery=query;ankiContext.searchScope=scope;ankiContext.referenceSearchFamilies=selectedReferenceFamilies();activeRef='';ankiReferenceMode='cards';ankiCardRevealed=false;ankiCurrentCard=0;fetchSemanticCards(ankiContext);
});
