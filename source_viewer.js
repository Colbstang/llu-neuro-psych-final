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
 const documents=DATA.source_catalog?.documents||[];
 const byId=documents.find(d=>d.id===value);if(byId)return byId;
 const exact=documents.filter(d=>(d.aliases||[]).some(a=>sourceAddress(a)===address));
 if(exact.length===1)return exact[0];
 if(exact.length>1)return {ambiguous:true};
 const basename=address.split('/').filter(Boolean).pop()||'';
 if(!basename)return null;
 const fallback=documents.filter(d=>(d.aliases||[]).some(a=>(sourceAddress(a).split('/').filter(Boolean).pop()||'')===basename));
 return fallback.length===1?fallback[0]:fallback.length>1?{ambiguous:true}:null;
}
async function fetchSourcePage(documentId,page,query=''){
 const key=JSON.stringify([documentId,page,query.slice(0,4000)]);
 if(sourcePageCache.has(key))return sourcePageCache.get(key);
 const response=await fetch(semanticEndpoint+'/source-page',{method:'POST',headers:semanticRequestHeaders(),body:JSON.stringify({documentId,page,query:query.slice(0,4000)}),signal:AbortSignal.timeout(45000)});
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
 showSidePanel(doc.title||result.title,`<div class="source-page-toolbar"><button data-source-step="-" aria-label="Previous source page" ${page<=1?'disabled':''}>‹</button><label>PDF page <input type="number" min="1" max="${count}" value="${page}" data-source-page-input aria-label="PDF page number"> / ${count}</label><button data-source-step="+" aria-label="Next source page" ${page>=count?'disabled':''}>›</button><button data-source-return title="Return to the page that opened this document">Linked page · ${view.linkedPage}</button></div><div class="book-mark-toolbar"><button id="book-mark-toggle" aria-pressed="${bookMarkMode}">${bookMarkMode?'Cancel page marking':'Mark a page region'}</button><span>Select text · H, or use Freehand.</span></div><p class="book-focus-legend">${hits?'<b>Matching source passage highlighted</b>':'No confident matching passage on this page. Full source shown.'} <button data-book-find-passage>Find passage</button></p><div class="panel-media book-page-media">${pageMarkup}</div>`,{sourceViewer:true,bookPanel:true,keepZoom:true,keepScroll:true,anchor:view.node});
 sourceViewerActive=view;applySideZoom();setBookMarkMode(bookMarkMode);
}
renderBookPanel=function(){if(sourceViewerActive)return renderSourceViewer();return priorRenderBookPanel()};
async function openExactSource(doc,page,query,node,linkedPage=page,forcePage=false){
 const serial=++sourceViewerSerial,topic='source:'+doc.id+':'+ankiNormalize(query).slice(0,140);
 const saved=state.sourcePageView[topic],target=forcePage?page:(linkedPage===page&&saved?.linkedPage===linkedPage?saved.page:page);
 showSidePanel(doc.title,'<p role="status">Opening the exact slide / notes page…</p>',{anchor:node});
 try{
  const result=await fetchSourcePage(doc.id,target,query);if(serial!==sourceViewerSerial||$('#side-panel').hidden)return;
  installSourcePage(result,topic);sourceViewerActive={document:doc,result,query,node,topic,linkedPage};bookMarkMode=false;
  state.sourcePageView[topic]={page:target,linkedPage};renderSourceViewer();if(target===linkedPage)requestAnimationFrame(scrollToLinkedPassage);save();
 }catch(error){if(serial!==sourceViewerSerial)return;showSidePanel(doc.title,`<p>The local source viewer is offline or this PDF is unavailable.</p><p>Run <strong>Start Study Guide.command</strong> and retry this source link. Your notes and highlights are saved.</p><p class="source-view-error">${esc(error.message)}</p>`,{anchor:node})}
}
// Public entry point for semantic search results. IDs are resolved only against
// the local catalog; callers never pass a filesystem path to the source API.
window.openSourceDocumentById=function(documentId,page=1,query='',options={}){
 const doc=(DATA.source_catalog?.documents||[]).find(item=>item.id===documentId);
 if(!doc)return false;
 const linkedPage=Number(options.linkedPage||page)||1,node=options.node||referenceOrigin?.element||null;
 openExactSource(doc,Number(page)||1,String(query||''),node,linkedPage,options.forcePage===true);return true;
};
const priorOpenBookReference=openBookReference;
openBookReference=function(key,book){
 const map=DATA.book_pages.keywords[key]||{},selected=book||state.bookPageView[bookViewKey(key,'active-book')]||(map['First Aid']?.pages.length?'First Aid':'Pathoma');
 if(selected==='Both'||!map[selected]?.pages?.length||!(DATA.source_catalog?.documents||[]).some(doc=>doc.id===selected))return priorOpenBookReference(key,book);
 const page=getBookPageView(key,selected,map[selected].pages)||map[selected].pages[0];
 if(!page)return priorOpenBookReference(key,book);
 state.bookPageView[bookViewKey(key,'active-book')]=selected;
 const query=ankiContext?.searchQuery||ankiContext?.text||referenceOrigin?.text||key;
 window.openSourceDocumentById(selected,page,query,{linkedPage:page,node:referenceOrigin?.element});
};
function bookSearchMatchesMarkup(context){
 const books=context?.bookMatches;if(!books||!['all','books'].includes(context?.searchScope||'all'))return '';
 return `<details class="passage-book-matches" open><summary>Book passages for this sentence</summary>${['First Aid','Pathoma'].map(book=>`<div class="passage-book-group"><strong>${book}</strong>${(books[book]||[]).length?(books[book]||[]).map((hit,i)=>`<button data-search-book="${book}" data-search-book-hit="${i}"><b>PDF ${hit.page}${hit.printed_page?' · printed '+esc(hit.printed_page):''}</b><span>${esc(hit.excerpt||'')}</span></button>`).join(''):'<p>No close passage found in the local edition.</p>'}</div>`).join('')}</details>`;
}
const baseSemanticMarkup=ankiContextMarkup;
ankiContextMarkup=function(){return baseSemanticMarkup()+(typeof sourceSearchResultsMarkup==='function'?'':bookSearchMatchesMarkup(ankiContext))};
const baseReferenceTabs=referenceTabsMarkup;
referenceTabsMarkup=function(mode='books'){
 let html=baseReferenceTabs(mode);if(ankiContext?.bookMatches)for(const book of ['First Aid','Pathoma','Both']){
  const available=book==='Both'?Object.values(ankiContext.bookMatches).some(h=>h.length):ankiContext.bookMatches[book]?.length;
  if(available)html=html.replace(`data-panel-book="${book}"`, `data-search-book="${book}"`).replace(new RegExp(`(<button data-search-book="${book}"[^>]*?) disabled`),'$1');
 }
 return html.replace(/<\/div>$/,`<button data-search-mehlman>Mehlman</button></div>`);
};
async function openSearchedBook(book,hitIndex=null,pageOverride=null){
 const context=ankiContext;if(!context?.bookMatches)return;
 const serial=++sourceViewerSerial,topic='passage:'+context.key,books=book==='Both'?['First Aid','Pathoma']:[book],map={};
 for(const name of books){const hits=context.bookMatches[name]||[];if(!hits.length)continue;map[name]={pages:hits.map(h=>Number(h.page)),printed_pages:hits.map(h=>h.printed_page||'—'),match:'sentence',note:'Closest passage from local sentence search. Highlight shows the matching source text.'}}
 DATA.book_pages.keywords[topic]=map;activeRef=topic;referenceBook=book;ankiReferenceMode='books';bookMarkMode=false;
 const requested=books.map(name=>{const hits=context.bookMatches[name]||[];if(!hits.length)return null;const saved=getBookPageView(topic,name,map[name].pages),hit=hits[hitIndex??hits.findIndex(h=>h.page===saved)]||hits[0],page=pageOverride&&book===name?pageOverride:hit.page;state.bookPageView[bookViewKey(topic,name)]=page;return {name,page,hit}}).filter(Boolean);
 // Keep the Both selector in the mapped passage panel. Choosing an individual
 // search hit opens that book as a full document at the exact matching page.
 if(book!=='Both'&&requested.length===1){const hit=requested[0];window.openSourceDocumentById(book,hit.page,context.searchQuery||context.text,{linkedPage:hit.page,node:referenceOrigin?.element});return}
 showSidePanel('Book passages for selected text','<p role="status">Opening the matched book passage…</p>',{anchor:referenceOrigin?.element});
 try{
  const results=await Promise.all(requested.map(({name,page})=>fetchSourcePage(name,page,context.searchQuery||context.text)));
  if(serial!==sourceViewerSerial||ankiContext!==context||$('#side-panel').hidden)return;
  results.forEach(result=>installSourcePage(result,topic));priorRenderBookPanel();requestAnimationFrame(()=>{const saved=state.bookPageView[bookViewKey(topic,book+'-scroll')];if(saved!==undefined)$('#side-content').scrollTop=saved;else scrollToLinkedPassage()});save();
 }catch(error){if(serial===sourceViewerSerial)showSidePanel('Book passages',`<p>Could not open the local PDF: ${esc(error.message)}</p><button data-semantic-retry>Retry search</button>`)}
}
window.openPassageSearch=function(item){
 const node=[...document.querySelectorAll('[data-edit]')].find(el=>el.dataset.edit===item.block_id);
 openAnkiContext({kind:'selection',text:item.quote,blockId:item.block_id,title:'References for highlighted sentence',node});
};
document.addEventListener('click',e=>{
 if(e.target.closest('[data-search-mehlman]')){const context=ankiContext;if(!context)return;const hit=context.mehlmanMatches?.[0],query=context.searchQuery||context.topic||context.text;if(hit)window.openSourceDocumentById(resultDocumentId(hit),resultPage(hit),query,{node:referenceOrigin?.element});else openAnkiContext({kind:'selection',text:query,searchQuery:query,searchScope:'books',referenceSearchFamilies:['mehlman'],title:'Mehlman · '+query.slice(0,70),topics:[]});return}
 const book=e.target.closest('[data-search-book]');if(book){e.preventDefault();e.stopImmediatePropagation();openSearchedBook(book.dataset.searchBook,book.dataset.searchBookHit===undefined?null:Number(book.dataset.searchBookHit));return}
 const sourceResult=e.target.closest('[data-open-source-result]');if(sourceResult){e.preventDefault();e.stopImmediatePropagation();window.openSourceDocumentById(sourceResult.dataset.documentId,Number(sourceResult.dataset.page||1),sourceResult.dataset.query||ankiContext?.searchQuery||ankiContext?.text||'',{node:referenceOrigin?.element});return}
 const sourceStep=e.target.closest('[data-source-step],[data-source-return]');if(sourceStep&&sourceViewerActive){e.preventDefault();e.stopImmediatePropagation();const v=sourceViewerActive,page=sourceStep.hasAttribute('data-source-return')?v.linkedPage:Number(v.result.page)+(sourceStep.dataset.sourceStep==='+'?1:-1);state.sourcePageView[v.topic]={page,linkedPage:v.linkedPage};openExactSource(v.document,page,v.query,v.node,v.linkedPage,true);return}
 const mappedBookPage=e.target.closest('[data-book-page],[data-book-step]');if(mappedBookPage&&activeRef){const name=mappedBookPage.dataset.bookName||referenceBook,ref=DATA.book_pages.keywords[activeRef]?.[name]||{pages:[]},current=Number(getBookPageView(activeRef,name,ref.pages)||ref.pages[0]||1),page=mappedBookPage.dataset.bookPage?Number(mappedBookPage.dataset.bookPage):Math.max(1,Math.min(Number(DATA.source_catalog?.documents?.find(d=>d.id===name)?.page_count)||Infinity,current+(mappedBookPage.dataset.bookStep==='+'?1:-1)));e.preventDefault();e.stopImmediatePropagation();if(name==='Both')return;state.bookPageView[bookViewKey(activeRef,name)]=page;const query=ankiContext?.searchQuery||ankiContext?.text||referenceOrigin?.text||'';window.openSourceDocumentById(name,page,query,{linkedPage:page,node:referenceOrigin?.element});return}
 const link=e.target.closest('#guide a[href],#side-content a[href]');if(!link||!/\.pdf(?:#|$|\?)/i.test(link.getAttribute('href')||'')||/Open full PDF|Open original PDF|Download/i.test(link.textContent))return;
 const doc=sourceDocument(link.getAttribute('href'));
 if(doc?.ambiguous){e.preventDefault();e.stopImmediatePropagation();showSidePanel('Source link is ambiguous','<p>This filename matches more than one local PDF, so the guide cannot safely choose a source page.</p><p>Use a citation that includes the specific lecture or a cataloged source link.</p>',{anchor:link});return}
 if(!doc)return;
 const page=Number((link.getAttribute('href').match(/#page=(\d+)/)||[])[1]||1);e.preventDefault();e.stopImmediatePropagation();openExactSource(doc,page,sourceQuery(link),link);
},true);
document.addEventListener('change',e=>{
 const input=e.target.closest('[data-source-page-input]');if(!input||!sourceViewerActive)return;
 const view=sourceViewerActive,count=Number(view.result.page_count||view.document.page_count)||1,page=Math.max(1,Math.min(count,Math.round(Number(input.value)||1)));
 state.sourcePageView[view.topic]={page,linkedPage:view.linkedPage};openExactSource(view.document,page,view.query,view.node,view.linkedPage,true);
});
