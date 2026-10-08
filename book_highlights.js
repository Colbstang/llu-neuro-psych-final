function bookHighlightKey(book,page){return `${book}::${page}`}
function bookPageMarkup(bookName,page,book,ref){
  const marks=state.bookHighlights[bookHighlightKey(bookName,page)]||[];
  const layer=DATA.book_text_layers?.books?.[bookName]?.[String(page)];
  const editId=`book-text-${bookName}-${page}`;
  if(!book.images[page]||!layer?.spans){
    const plain=layer?.plain_text||'The original PDF page is not locally available.';
    const html=plain.split(/\n\s*\n/).map(t=>`<p>${esc(t).replace(/\n/g,'<br>')}</p>`).join('');
    return `<figure class="book-page-figure"><p class="book-availability-note">${!book.images[page]?'Exact saved page text is shown while the original PDF is offloaded to iCloud.':'Text view of this reference page.'} <a href="${book.url}#page=${page}" target="_blank" rel="noopener">Open original PDF page ${page}</a></p><div class="editable book-fallback-text" data-edit="${esc(editId)}" contenteditable="false">${decorateReading(editId,html)}</div><figcaption>${esc(bookName)} · PDF ${page}</figcaption></figure>`;
  }
  const focus=DATA.book_link_focus?.topics?.[activeRef]?.[bookName]?.[String(page)],focusIds=new Set(focus?.spans||[]);
  const textHTML=layer?(layer.spans||[]).map((s,i)=>`<span class="${focusIds.has(i)?'book-linked-passage':''}" style="left:${s.x*100}%;top:${s.y*100}%;width:${s.w*100}%;height:${s.h*100}%;--pdf-font:${s.size};--pdf-width:${layer.width}">${esc(s.text)}</span>`).join(''):'';
  const i=ref.pages.indexOf(page),printed=ref.printed_pages[i]||'—';
  return `<figure class="book-page-figure" data-book-page-figure data-book="${bookName}" data-page="${page}"><div class="book-page-canvas"><img class="book-page-image" src="${book.images[page]}" alt="${esc(book.label)} physical PDF page ${page}" draggable="false" width="${layer.width}" height="${layer.height}"><div class="editable book-text-layer" data-edit="${esc(editId)}" contenteditable="false" aria-label="Selectable text from ${esc(book.label)} page ${page}">${decorateReading(editId,textHTML)}</div><div class="book-highlight-layer" aria-label="Saved page highlights">${marks.map(m=>`<button class="book-highlight-rect" style="left:${m.x*100}%;top:${m.y*100}%;width:${m.w*100}%;height:${m.h*100}%" data-remove-book-highlight="${m.id}" title="Remove saved highlight" aria-label="Remove saved highlight"></button>`).join('')}<div class="book-highlight-draft" hidden></div></div></div><figcaption>${esc(bookName)} · Printed p. ${printed} · PDF ${page}</figcaption><div class="book-highlight-actions">${marks.length?`<button data-clear-book-highlights="${page}" data-clear-book="${bookName}">Clear highlights on this page</button>`:''}</div></figure>`
}
function applySideZoom(){$('#side-content')?.querySelectorAll('.book-page-canvas').forEach(canvas=>canvas.style.width=(sideZoom*100)+'%')}
function saveBookMarks(book,page,marks){const key=bookHighlightKey(book,page);if(marks.length)state.bookHighlights[key]=marks;else delete state.bookHighlights[key];save()}
let bookMarkMode=false,bookDrag=null;
function setBookMarkMode(on){bookMarkMode=on;const b=$('#book-mark-toggle');if(b){b.setAttribute('aria-pressed',String(on));b.textContent=on?'Cancel page marking':'Mark a page region'}$('#side-content')?.querySelectorAll('.book-page-canvas').forEach(canvas=>canvas.classList.toggle('marking',on))}
function bookPageRef(book,page){const ref=DATA.book_pages.keywords[activeRef]?.[book]||{pages:[]};return ref}
document.addEventListener('click',e=>{
  const pageTab=e.target.closest('[data-book-page]');
  if(pageTab){const bk=pageTab.dataset.bookName||referenceBook;state.bookPageView[bookViewKey(activeRef,bk)]=Number(pageTab.dataset.bookPage);save();renderBookPanel();return}
  const step=e.target.closest('[data-book-step]');
  if(step){const bk=step.dataset.bookName||referenceBook,ref=bookPageRef(bk);const at=ref.pages.indexOf(getBookPageView(activeRef,bk,ref.pages));const next=Math.max(0,Math.min(ref.pages.length-1,at+(step.dataset.bookStep==='+'?1:-1)));state.bookPageView[bookViewKey(activeRef,bk)]=ref.pages[next];save();renderBookPanel();return}
  if(e.target.closest('#book-mark-toggle')){setBookMarkMode(!bookMarkMode);return}
  const remove=e.target.closest('[data-remove-book-highlight]');
  if(remove){const fig=remove.closest('[data-book-page-figure]'),key=bookHighlightKey(fig.dataset.book,Number(fig.dataset.page));saveBookMarks(fig.dataset.book,Number(fig.dataset.page),(state.bookHighlights[key]||[]).filter(m=>m.id!==remove.dataset.removeBookHighlight));renderBookPanel();return}
  const clear=e.target.closest('[data-clear-book-highlights]');
  if(clear){saveBookMarks(clear.dataset.clearBook||referenceBook,Number(clear.dataset.clearBookHighlights),[]);renderBookPanel();return}
});
document.addEventListener('pointerdown',e=>{
  const layer=e.target.closest('.book-highlight-layer');if(!layer||!bookMarkMode)return;
  if(e.target.closest('.book-highlight-rect'))return;
  const box=layer.getBoundingClientRect(),x=Math.max(0,Math.min(1,(e.clientX-box.left)/box.width)),y=Math.max(0,Math.min(1,(e.clientY-box.top)/box.height));
  bookDrag={layer,book:layer.closest('[data-book-page-figure]').dataset.book,page:Number(layer.closest('[data-book-page-figure]').dataset.page),box,x,y,draft:layer.querySelector('.book-highlight-draft'),pointerId:e.pointerId};
  bookDrag.draft.hidden=false;bookDrag.draft.style.cssText=`left:${x*100}%;top:${y*100}%;width:0;height:0`;
  layer.setPointerCapture?.(e.pointerId);e.preventDefault();
});
document.addEventListener('pointermove',e=>{
  if(!bookDrag)return;const {box,x,y,draft}=bookDrag;const ex=Math.max(0,Math.min(1,(e.clientX-box.left)/box.width)),ey=Math.max(0,Math.min(1,(e.clientY-box.top)/box.height));
  draft.style.left=(Math.min(x,ex)*100)+'%';draft.style.top=(Math.min(y,ey)*100)+'%';draft.style.width=(Math.abs(ex-x)*100)+'%';draft.style.height=(Math.abs(ey-y)*100)+'%';
});
document.addEventListener('pointerup',e=>{
  if(!bookDrag)return;const d=bookDrag;bookDrag=null;const ex=Math.max(0,Math.min(1,(e.clientX-d.box.left)/d.box.width)),ey=Math.max(0,Math.min(1,(e.clientY-d.box.top)/d.box.height));d.draft.hidden=true;
  const x=Math.min(d.x,ex),y=Math.min(d.y,ey),w=Math.abs(ex-d.x),h=Math.abs(ey-d.y);
  if(w<.004||h<.002){if(!(state.bookHighlights[bookHighlightKey(d.book,d.page)]||[]).length)setBookMarkMode(false);return}
  const key=bookHighlightKey(d.book,d.page),marks=[...(state.bookHighlights[key]||[]),{id:`bm-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,7)}`,x,y,w,h}];saveBookMarks(d.book,d.page,marks);renderBookPanel();setBookMarkMode(true);
});
