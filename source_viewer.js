// Exact local source pages share the book viewer's selectable text, persisted
// highlights and freehand marks. Clients send catalog IDs, never file paths.
state.sourcePageView ||= {};
let sourceViewerActive=null,sourceViewerSerial=0;
const sourcePageCache=new Map();
const priorShowSidePanel=showSidePanel,priorRenderBookPanel=renderBookPanel;
showSidePanel=function(title,html,opts={}){
 if(!opts.sourceViewer)sourceViewerActive=null;
 if(String(title).startsWith('passage:'))title=(ankiContext?.topic||'Selected passage')+' · '+referenceBook;
 return priorShowSidePanel(title,html,opts);
};
function sourceAddress(value){
 try{return decodeURIComponent(String(value||'').split('#')[0].split('?')[0]).replace(/^file:\/\//,'').replace(/\\/g,'/')}catch{return String(value||'').split('#')[0]}
}
function sourceDocument(value){
 const address=sourceAddress(value);
 return (DATA.source_catalog?.documents||[]).find(d=>d.id===value||(d.aliases||[]).some(a=>sourceAddress(a)===address||sourceAddress(a).split('/').pop()===address.split('/').pop()));
}
async function fetchSourcePage(documentId,page,query=''){
 const key=JSON.stringify([documentId,page,query.slice(0,4000)]);
 if(sourcePageCache.has(key))return sourcePageCache.get(key);
 const response=await fetch(semanticEndpoint+'/source-page',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({documentId,page,query:query.slice(0,4000)}),signal:AbortSignal.timeout(45000)});
 const result=await response.json();if(!response.ok)throw Error(result.error||'Source viewer unavailable');
 if(!result.layer?.spans||!result.image)throw Error('The exact source page could not be rendered');
 sourcePageCache.set(key,result);return result;
}
function installSourcePage(result,topic){
 const id=result.documentId,page=Number(result.page);
 const book=DATA.book_pages.books[id]||{label:result.title,url:result.url?.split('#')[0]||'',images:{}};
 book.images||={};book.images[page]=result.image;DATA.book_pages.books[id]=book;
 DATA.book_text_layers||={books:{}};DATA.book_text_layers.books||={};DATA.book_text_layers.books[id]||={};DATA.book_text_layers.books[id][String(page)]=result.layer;
 DATA.book_link_focus||={topics:{}};DATA.book_link_focus.topics[topic]||={};DATA.book_link_focus.topics[topic][id]||={};DATA.book_link_focus.topics[topic][id][String(page)]=result.focus||{spans:[],quotes:[]};
 return book;
}
function sourceQuery(node){
 const objective=node.closest('[data-objective-card]');
 if(objective){const id=objective.dataset.objectiveCard,o=DATA.objectives.find(o=>o.id===id),answer=DATA.objective_answers[id];return [o?.text,ankiPlain(answer?.html||'')].filter(Boolean).join(' ').slice(0,3500)}
 return (referenceOrigin?.text||node.closest('[data-block]')?.querySelector('.editable')?.textContent||node.closest('[data-sheet-row]')?.textContent||node.textContent).slice(0,3500);
}
function renderSourceViewer(){
 const view=sourceViewerActive;if(!view)return;
 const {result,topic,document:doc}=view,book=DATA.book_pages.books[result.documentId],page=Number(result.page),count=Number(result.page_count||doc.page_count)||page;
 activeRef=topic;referenceBook=result.documentId;
 const hits=result.focus?.spans?.length||0;
 const pageMarkup=bookPageMarkup(result.documentId,page,book,{pages:[page],printed_pages:[result.printed_page||'—']}).replace(/<figcaption>[^<]*<\/figcaption>/,`<figcaption>${esc(doc.title||result.title)} · PDF ${page}</figcaption>`);
 showSidePanel(doc.title||result.title,`<div class="source-page-toolbar"><button data-source-step="-" aria-label="Previous source page" ${page<=1?'disabled':''}>‹</button><span>PDF ${page} / ${count}</span><button data-source-step="+" aria-label="Next source page" ${page>=count?'disabled':''}>›</button><button data-source-return>Linked page</button></div><div class="book-mark-toolbar"><button id="book-mark-toggle" aria-pressed="${bookMarkMode}">${bookMarkMode?'Cancel page marking':'Mark a page region'}</button><span>Select text · H, or use Freehand.</span></div><p class="book-focus-legend">${hits?'<b>Matching source passage highlighted</b>':'No confident matching passage on this page. Full source shown.'} <button data-book-find-passage>Find passage</button></p><div class="panel-media book-page-media">${pageMarkup}</div>`,{sourceViewer:true,bookPanel:true,keepZoom:true,anchor:view.node});
 sourceViewerActive=view;applySideZoom();setBookMarkMode(bookMarkMode);
}
renderBookPanel=function(){if(sourceViewerActive)return renderSourceViewer();return priorRenderBookPanel()};
async function openExactSource(doc,page,query,node,linkedPage=page){
 const serial=++sourceViewerSerial,topic='source:'+doc.id+':'+ankiNormalize(query).slice(0,140);
 const saved=state.sourcePageView[topic],target=linkedPage===page&&saved?.linkedPage===linkedPage?saved.page:page;
 showSidePanel(doc.title,'<p role="status">Opening the exact slide / notes page…</p>',{anchor:node});
 try{
  const result=await fetchSourcePage(doc.id,target,query);if(serial!==sourceViewerSerial||$('#side-panel').hidden)return;
  installSourcePage(result,topic);sourceViewerActive={document:doc,result,query,node,topic,linkedPage};bookMarkMode=false;
  state.sourcePageView[topic]={page:target,linkedPage};renderSourceViewer();requestAnimationFrame(scrollToLinkedPassage);save();
 }catch(error){if(serial!==sourceViewerSerial)return;showSidePanel(doc.title,`<p>The local source viewer is offline or this PDF is unavailable.</p><p>Run <strong>Start Study Guide.command</strong> and retry this source link. Your notes and highlights are saved.</p><p class="source-view-error">${esc(error.message)}</p>`,{anchor:node})}
}
function bookSearchMatchesMarkup(context){
 const books=context?.bookMatches;if(!books)return '';
 return `<details class="passage-book-matches" open><summary>Book passages for this sentence</summary>${['First Aid','Pathoma'].map(book=>`<div class="passage-book-group"><strong>${book}</strong>${(books[book]||[]).length?(books[book]||[]).map((hit,i)=>`<button data-search-book="${book}" data-search-book-hit="${i}"><b>PDF ${hit.page}${hit.printed_page?' · printed '+esc(hit.printed_page):''}</b><span>${esc(hit.excerpt||'')}</span></button>`).join(''):'<p>No close passage found in the local edition.</p>'}</div>`).join('')}</details>`;
}
const baseSemanticMarkup=ankiContextMarkup;
ankiContextMarkup=function(){return baseSemanticMarkup()+bookSearchMatchesMarkup(ankiContext)};
const baseReferenceTabs=referenceTabsMarkup;
referenceTabsMarkup=function(mode='books'){
 let html=baseReferenceTabs(mode);if(ankiContext?.bookMatches)for(const book of ['First Aid','Pathoma','Both']){
  const available=book==='Both'?Object.values(ankiContext.bookMatches).some(h=>h.length):ankiContext.bookMatches[book]?.length;
  if(available)html=html.replace(`data-panel-book="${book}"`, `data-search-book="${book}"`).replace(new RegExp(`(<button data-search-book="${book}"[^>]*?) disabled`),'$1');
 }
 return html;
};
async function openSearchedBook(book,hitIndex=null,pageOverride=null){
 const context=ankiContext;if(!context?.bookMatches)return;
 const serial=++sourceViewerSerial,topic='passage:'+context.key,books=book==='Both'?['First Aid','Pathoma']:[book],map={};
 for(const name of books){const hits=context.bookMatches[name]||[];if(!hits.length)continue;map[name]={pages:hits.map(h=>Number(h.page)),printed_pages:hits.map(h=>h.printed_page||'—'),match:'sentence',note:'Closest passage from local sentence search. Highlight shows the matching source text.'}}
 DATA.book_pages.keywords[topic]=map;activeRef=topic;referenceBook=book;ankiReferenceMode='books';bookMarkMode=false;
 const requested=books.map(name=>{const hits=context.bookMatches[name]||[];if(!hits.length)return null;const saved=getBookPageView(topic,name,map[name].pages),hit=hits[hitIndex??hits.findIndex(h=>h.page===saved)]||hits[0],page=pageOverride&&book===name?pageOverride:hit.page;state.bookPageView[bookViewKey(topic,name)]=page;return {name,page,hit}}).filter(Boolean);
 showSidePanel('Book passages for selected text','<p role="status">Opening the matched book passage…</p>',{anchor:referenceOrigin?.element});
 try{
  const results=await Promise.all(requested.map(({name,page})=>fetchSourcePage(name,page,context.text)));
  if(serial!==sourceViewerSerial||ankiContext!==context||$('#side-panel').hidden)return;
  results.forEach(result=>installSourcePage(result,topic));priorRenderBookPanel();requestAnimationFrame(()=>{const saved=state.bookPageView[bookViewKey(topic,book+'-scroll')];if(saved!==undefined)$('#side-content').scrollTop=saved;else scrollToLinkedPassage()});save();
 }catch(error){if(serial===sourceViewerSerial)showSidePanel('Book passages',`<p>Could not open the local PDF: ${esc(error.message)}</p><button data-semantic-retry>Retry search</button>`)}
}
window.openPassageSearch=function(item){
 const node=[...document.querySelectorAll('[data-edit]')].find(el=>el.dataset.edit===item.block_id);
 openAnkiContext({kind:'selection',text:item.quote,blockId:item.block_id,title:'References for highlighted sentence',node});
};
document.addEventListener('click',e=>{
 const book=e.target.closest('[data-search-book]');if(book){e.preventDefault();e.stopImmediatePropagation();openSearchedBook(book.dataset.searchBook,book.dataset.searchBookHit===undefined?null:Number(book.dataset.searchBookHit));return}
 const sourceStep=e.target.closest('[data-source-step],[data-source-return]');if(sourceStep&&sourceViewerActive){e.preventDefault();e.stopImmediatePropagation();const v=sourceViewerActive,page=sourceStep.hasAttribute('data-source-return')?v.linkedPage:Number(v.result.page)+(sourceStep.dataset.sourceStep==='+'?1:-1);delete state.sourcePageView[v.topic];openExactSource(v.document,page,v.query,v.node,v.linkedPage);return}
 if(activeRef.startsWith('passage:')&&ankiContext?.bookMatches){const tab=e.target.closest('[data-book-page],[data-book-step]');if(tab){e.preventDefault();e.stopImmediatePropagation();const name=tab.dataset.bookName||referenceBook,ref=DATA.book_pages.keywords[activeRef][name],at=ref.pages.indexOf(getBookPageView(activeRef,name,ref.pages)),page=tab.dataset.bookPage?Number(tab.dataset.bookPage):ref.pages[Math.max(0,Math.min(ref.pages.length-1,at+(tab.dataset.bookStep==='+'?1:-1)))];openSearchedBook(name,null,page);return}}
 const link=e.target.closest('#guide a[href],#side-content a[href]');if(!link||!/\.pdf(?:#|$|\?)/i.test(link.getAttribute('href')||'')||/Open full PDF|Open original PDF|Download/i.test(link.textContent))return;
 const doc=sourceDocument(link.getAttribute('href'));if(!doc)return;
 const page=Number((link.getAttribute('href').match(/#page=(\d+)/)||[])[1]||1);e.preventDefault();e.stopImmediatePropagation();openExactSource(doc,page,sourceQuery(link),link);
},true);
