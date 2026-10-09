// Sentence embeddings are served locally. Explicit topic links retain their
// curated image-first view; selecting a sentence starts with eight close cards.
const semanticEndpoint='http://127.0.0.1:8768';
let semanticRequestSerial=0,semanticAbort=null;
const lexicalCardResults=ankiCardResults,legacyOpenAnkiContext=openAnkiContext,legacyContextMarkup=ankiContextMarkup;
ankiCardResults=function(context=ankiContext){
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
 const messages={loading:'Finding the closest cards…',semantic:`Local sentence search · ${context.semanticCardIds.length} close cards · allowed tags only`,fallback:'Text matches · local sentence search is offline',empty:'No close sentence match in the allowed tags'};
 return `${content}<div class="semantic-search-status" role="status"><span>${esc(messages[context.retrieval]||'')}</span>${context.retrieval==='fallback'?'<small>Install a local source index and sentence-search models to enable semantic search.</small><button data-semantic-retry>Retry sentence search</button>':''}${['semantic','empty'].includes(context.retrieval)?'<button data-semantic-broaden>Broaden text matches</button>':''}</div>`;
};
function narrowLexicalContext(context){
 context.matches=ankiMatchContext(context);
 context.semanticCardIds=lexicalCardResults(context).slice(0,12).map(item=>Number(item.card.id));
 const notes=new Set(context.semanticCardIds.map(id=>Number(ankiCards.get(id)?.note)));
 context.matches=context.matches.filter(h=>notes.has(Number(h.id)));
 context.retrieval='fallback';
}
async function fetchSemanticCards(context){
 const serial=++semanticRequestSerial;semanticAbort?.abort();const controller=new AbortController();semanticAbort=controller;
 context.retrieval='loading';context.matches=[];context.semanticCardIds=[];renderAnkiReference();
 const timer=setTimeout(()=>controller.abort(),45000);
 try{
  const response=await fetch(semanticEndpoint+'/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:context.text.slice(0,4000),kind:context.filter||'all',limit:8,include_books:true}),signal:controller.signal});
  if(!response.ok)throw Error('Local search unavailable');const result=await response.json();
  if(serial!==semanticRequestSerial||ankiContext!==context)return;
  if(result.method!=='local-sentence-embeddings'||!Array.isArray(result.matches))throw Error('Unexpected local search response');
  const cards=result.matches.filter(h=>ankiCards.has(Number(h.cardId))&&ankiNotes.has(Number(h.noteId))&&Number(ankiCards.get(Number(h.cardId)).note)===Number(h.noteId));
  context.semanticCardIds=cards.map(h=>Number(h.cardId));context.matches=[];
  for(const hit of cards){if(context.matches.some(n=>n.id===Number(hit.noteId)))continue;context.matches.push({id:Number(hit.noteId),score:hit.score,reasons:[hit.reason]})}
  context.bookMatches=result.books||{'First Aid':[],'Pathoma':[]};
  context.retrieval=cards.length?'semantic':'empty';
 }catch(error){if(serial!==semanticRequestSerial||ankiContext!==context)return;narrowLexicalContext(context)}
 finally{clearTimeout(timer);if(serial===semanticRequestSerial&&ankiContext===context&&!$('#side-panel').hidden){ankiCardRevealed=false;renderAnkiReference()}}
}
openAnkiContext=function(options){
 if(options.fromNote||options.kind==='topic'||options.kind==='connections')return legacyOpenAnkiContext(options);
 ankiContext=ankiMakeContext(options);activeRef=ankiContext.topic||ankiContext.topics.find(t=>ankiBookAvailable(t,'First Aid')||ankiBookAvailable(t,'Pathoma'))||'';
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
