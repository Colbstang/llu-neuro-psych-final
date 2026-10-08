// Recall games use the same course comparison rows as the reference tables.
state.traitPractice ||= {};
state.pathDrawings ||= {};
let traitSession=null,pathSession=null;
const originalComparisonMarkup=comparisonMarkup;
const shuffled=items=>{const out=[...items];for(let i=out.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[out[i],out[j]]=[out[j],out[i]]}return out};
function practiceSheet(id){return DATA.comparison_sheets.find(s=>s.id===id)}
comparisonMarkup=function(sheetId=null){
 const html=originalComparisonMarkup(sheetId);
 if(!sheetId)return html;
 const controls=sheetId==='pathology'
  ? `<div class="practice-modes" role="group" aria-label="Pathology practice mode"><button data-path-mode="table">Table</button><button data-path-mode="reverse">Reverse recall</button><button data-path-mode="mechanism">Image + mechanism</button><button data-path-mode="draw">Quick draw</button><button data-shuffle-sheet="pathology">Shuffle table</button></div><div id="path-practice" hidden></div>`
  : `<div class="practice-modes"><button data-trait-start="${sheetId}">Play ${sheetId==='bugs'?'organism':'drug'} characteristics</button><button data-shuffle-sheet="${sheetId}">Shuffle table</button></div><div id="trait-practice" hidden></div>`;
 return html.replace('<label class="sheet-search">',controls+'<label class="sheet-search">');
};
function traitKey(value){return String(value).normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,'')}
function uniqueTraits(values){const seen=new Set();return values.filter(v=>{const key=traitKey(v);if(seen.has(key))return false;seen.add(key);return true})}
function traitIncludes(values,value){return values.some(v=>traitKey(v)===traitKey(value))}
function applicableTraitValues(kind,category,values){return kind==='bugs'&&category==='Gram / stain'?values.filter(v=>!['notabacterialgramclassification','notapplicablevirus','notapplicableparasite'].includes(traitKey(v))):values}
function traitCandidates(kind,selections={},before=Infinity){
 const config=DATA.review_games[kind];
 return config.cases.filter(c=>scopeRow(kind,c.row_index)&&config.categories.slice(0,before).every(category=>{
  const picked=selections[category]||[];return !picked.length||picked.some(value=>traitIncludes(c.traits[category]||[],value));
 }));
}
function traitOptions(kind,category,selections={},before=Infinity){return uniqueTraits(traitCandidates(kind,selections,before).flatMap(c=>applicableTraitValues(kind,category,c.traits[category]||[]))).sort((a,b)=>a.localeCompare(b))}
function reconcileTraitSelections(session){
 const config=DATA.review_games[session.kind],valid={};
 config.categories.forEach((category,index)=>{
  const options=traitOptions(session.kind,category,valid,index),picked=session.selections[category]||[];
  const kept=picked.map(value=>options.find(option=>traitKey(option)===traitKey(value))).filter(Boolean);
  if(kept.length)valid[category]=uniqueTraits(kept);
 });
 session.selections=valid;
}
function startTraits(kind){
 const cases=DATA.review_games[kind].cases;
 traitSession={kind,order:shuffled(cases.map((c,i)=>({c,i})).filter(({c})=>scopeRow(kind,c.row_index)).map(({i})=>i)),at:0,selections:{},checked:false};
 renderTraits();
 $('#trait-practice').scrollIntoView({block:'start',behavior:'smooth'});
}
function traitCase(){return DATA.review_games[traitSession.kind].cases[traitSession.order[traitSession.at]]}
function traitGrade(c){
 let correct=0,total=0,extra=0;const missed=[],wrong=[];
 for(const category of DATA.review_games[traitSession.kind].categories){const expected=uniqueTraits(applicableTraitValues(traitSession.kind,category,c.traits[category]||[])),picked=uniqueTraits(traitSession.selections[category]||[]);total+=expected.length;correct+=expected.filter(v=>traitIncludes(picked,v)).length;for(const v of expected)if(!traitIncludes(picked,v))missed.push(`${category}: ${v}`);for(const v of picked)if(!traitIncludes(expected,v)){extra++;wrong.push(`${category}: ${v}`)}}
 return {correct,total,extra,missed,wrong,perfect:correct===total&&!extra};
}
function rowFacts(sheet,row){return `<dl class="practice-facts">${sheet.columns.map((h,i)=>`<div><dt>${esc(h)}</dt><dd class="editable" data-edit="practice-${sheet.id}-${sheet.rows.indexOf(row)}-${i}" contenteditable="false">${keywordify(row.cells[i])}</dd></div>`).join('')}</dl><p class="source-links">${(row.source_pages||[]).map(p=>sourceLink(p)).join(' · ')} ${sourceCitations(row.lecture_sources)} ${(row.external_sources||[]).map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a>`).join(' · ')}</p>`}
function renderTraits(){
 const target=$('#trait-practice');if(!target||!traitSession)return;
 const s=traitSession,c=traitCase(),config=DATA.review_games[s.kind],sheet=practiceSheet(s.kind),row=sheet.rows[c.row_index],grade=s.checked?traitGrade(c):null;
 target.hidden=false;
 const visible=config.categories.map((category,ci)=>({category,ci,options:traitOptions(s.kind,category,s.selections,ci)})).filter(x=>x.options.length);
 const active=Object.entries(s.selections).map(([category,values])=>`${category}: ${values.join(' / ')}`);
 target.innerHTML=`<section class="trait-round"><header class="practice-header"><div><p class="eyebrow">${s.kind==='bugs'?'ORGANISM':'DRUG'} CHARACTERISTICS · ${s.at+1} / ${s.order.length}</p><h2>${esc(c.name)}</h2><p>Choose one or more characteristics in a category; any selected choice counts. Choices across categories narrow the options below.</p></div><button data-trait-close>Close game</button></header>${active.length?`<div class="trait-scope"><span>Current filters: ${active.map(esc).join(' · ')}</span><button type="button" data-trait-clear-all ${s.checked?'disabled':''}>Clear choices</button></div>`:''}${visible.length?visible.map(({category,ci,options})=>{const expected=c.traits[category]||[],pickedValues=s.selections[category]||[];return `<fieldset class="trait-category"><legend>${esc(category)}${pickedValues.length?` <button type="button" class="trait-clear" data-trait-clear-category="${ci}" ${s.checked?'disabled':''}>Clear ${esc(category)} choice</button>`:''}</legend><div class="trait-options">${options.map((option,oi)=>{const picked=pickedValues.some(value=>traitKey(value)===traitKey(option)),right=traitIncludes(expected,option);return `<button type="button" data-trait-category="${ci}" data-trait-option="${oi}" aria-pressed="${picked}" ${s.checked?'disabled':''} class="trait-option ${s.checked?(right?'trait-right':picked?'trait-wrong':''):picked?'trait-picked':''}">${esc(option)}${s.checked&&right?'<span aria-label="Correct characteristic"> ✓</span>':s.checked&&picked?'<span aria-label="Incorrect selection"> ✕</span>':''}</button>`}).join('')}</div></fieldset>`}).join(''):'<p class="trait-empty">No later category has choices for this filter combination. Clear one or more earlier choices to broaden the options.</p>'}<div class="practice-actions"><button data-trait-check>${s.checked?'Try this round again':'Check all categories'}</button><button data-trait-next>${s.checked?'Next →':'Skip →'}</button><button data-trait-shuffle>Shuffle ${s.kind==='bugs'?'organisms':'drugs'}</button></div>${s.checked?`<div class="trait-result ${grade.perfect?'perfect':''}" role="status"><strong>${grade.perfect?'All characteristics correct':`${grade.correct} / ${grade.total} correct characteristics · ${grade.extra} extra selections`}</strong><p>${grade.missed.length?`${grade.missed.length} missed characteristics are marked green above.`:'All expected characteristics selected.'} ${grade.wrong.length?'Extra selections are marked red.':''}</p></div><div class="practice-complete"><h3>Complete study entry</h3>${rowFacts(sheet,row)}${c.clarification_html||''}<p class="source-links">${sourceCitations(c.sources)} ${(c.external_sources||[]).map(x=>`<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.label)}</a>`).join(' · ')}</p></div>`:''}</section>`;
}
function pathName(row){const el=document.createElement('div');el.innerHTML=row.cells[0];return el.textContent.trim()}
function setPathMode(mode){
 const sheet=practiceSheet('pathology');pathSession ||= {order:shuffled(scopePathIndices()),at:0,revealed:false};
 pathSession.mode=mode;pathSession.revealed=false;state.pathPracticeMode=mode;save();renderPathPractice();
}
function pathImages(index,hiddenName=false){
 const all=DATA.pathology_images?.[String(index)]||[],images=all;
 if(!images.length)return '<p class="path-source-note">Source figure unavailable.</p>';
 return images.map((f,i)=>{const url=(f.source_url||(f.source_book?DATA.book_pages.books[f.source_book]?.url:''))?.split('#')[0];return `<figure class="path-image"><button data-path-image="${index}-${i}" data-image-src="${esc(f.src)}" data-image-caption="${esc(f.caption)}" aria-label="Open pathology source image"><img loading="lazy" src="${esc(f.src)}" alt="${hiddenName?'Pathology recall image':esc(f.caption)}"></button><figcaption>${hiddenName?(f.recall_prompt===false?'Labeled reference figure · review the mechanism':'Source image · identify the process'):esc(f.caption)+(url?` <a href="${esc(url)}#page=${f.source_page}" target="_blank" rel="noopener">Exact image source · PDF ${f.source_page}</a>`:'')}</figcaption></figure>`}).join('')
}
function renderPathPractice(){
 const target=$('#path-practice');if(!target||!pathSession)return;const s=pathSession,sheet=practiceSheet('pathology'),index=s.order[s.at],row=sheet.rows[index];
 const table=$('#sheet-pathology .table-scroll'),search=$('#sheet-pathology .sheet-search');if(table)table.hidden=s.mode!=='table';if(search)search.hidden=s.mode!=='table';target.hidden=s.mode==='table';
 document.querySelectorAll('[data-path-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.pathMode===s.mode)));
 if(s.mode==='table')return;
 const draw=s.mode==='draw',mechanism=s.mode==='mechanism';
 target.innerHTML=`<section class="path-round"><header class="practice-header"><div><p class="eyebrow">PATHOLOGY · ${s.at+1} / ${s.order.length}</p><h2>${draw?esc(pathName(row)):mechanism?'Name the pathology from the image and mechanism':'Reverse recall: name the pathology'}</h2></div><button data-path-shuffle>Shuffle</button></header>${draw?`<p>Quickly sketch the defining mechanism or lesion. Reveal the source image to compare.</p><canvas id="path-draw-canvas" width="1000" height="420" aria-label="Quick pathology drawing area"></canvas><button data-path-clear>Clear drawing</button>`:pathImages(index,!s.revealed)}${mechanism?`<div class="path-mechanism"><h3>Mechanism / pathology</h3>${row.cells[2]}</div>`:!draw?`<div class="path-mechanism"><h3>Clinical pattern</h3>${row.cells[1]}</div>`:''}<div class="practice-actions"><button data-path-reveal>${s.revealed?'Hide answer':'Reveal answer · Space'}</button><button data-path-step="-">← Previous</button><button data-path-step="+">Next →</button></div>${s.revealed?`<div class="practice-complete"><h2>${keywordify(esc(pathName(row)))}</h2>${rowFacts(sheet,row)}${draw?pathImages(index):''}<div class="practice-actions"><button data-path-grade="again">Again</button><button data-path-grade="known">Got it</button></div></div>`:''}</section>`;
 if(draw)mountPathCanvas(index);
}
function mountPathCanvas(index){
 const canvas=$('#path-draw-canvas'),ctx=canvas.getContext('2d');ctx.lineWidth=4;ctx.lineCap='round';ctx.lineJoin='round';ctx.strokeStyle='#203e34';
 const key=`pathology-${index}`,strokes=state.pathDrawings[key]||[];let active=null;
 const paint=()=>{ctx.clearRect(0,0,canvas.width,canvas.height);for(const pts of strokes){ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p[0]*canvas.width,p[1]*canvas.height):ctx.moveTo(p[0]*canvas.width,p[1]*canvas.height));ctx.stroke()}};paint();
 const point=e=>{const r=canvas.getBoundingClientRect();return[(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height]};
 canvas.onpointerdown=e=>{if(e.button!==0)return;active=[point(e)];strokes.push(active);canvas.setPointerCapture(e.pointerId);e.preventDefault()};
 canvas.onpointermove=e=>{if(!active)return;active.push(point(e));paint();e.preventDefault()};
 const finish=()=>{if(active){active=null;state.pathDrawings[key]=strokes;save()}};canvas.onpointerup=finish;canvas.onpointercancel=finish;
}
document.addEventListener('click',e=>{
 const start=e.target.closest('[data-trait-start]');if(start)startTraits(start.dataset.traitStart);
 const opt=e.target.closest('[data-trait-option]');if(opt&&traitSession&&!traitSession.checked){const categories=DATA.review_games[traitSession.kind].categories,ci=Number(opt.dataset.traitCategory),category=categories[ci],value=traitOptions(traitSession.kind,category,traitSession.selections,ci)[Number(opt.dataset.traitOption)],selected=traitSession.selections[category]||[];traitSession.selections[category]=selected.some(v=>traitKey(v)===traitKey(value))?selected.filter(v=>traitKey(v)!==traitKey(value)):[...selected,value];reconcileTraitSelections(traitSession);renderTraits()}
 const clearCategory=e.target.closest('[data-trait-clear-category]');if(clearCategory&&traitSession&&!traitSession.checked){const category=DATA.review_games[traitSession.kind].categories[Number(clearCategory.dataset.traitClearCategory)];delete traitSession.selections[category];reconcileTraitSelections(traitSession);renderTraits()}
 if(e.target.closest('[data-trait-clear-all]')&&traitSession&&!traitSession.checked){traitSession.selections={};renderTraits()}
 if(e.target.closest('[data-trait-check]')&&traitSession){if(traitSession.checked){traitSession.checked=false;traitSession.selections={}}else{traitSession.checked=true;const c=traitCase();state.traitPractice[c.id]={...traitGrade(c),reviewedAt:Date.now()};save()}renderTraits()}
 if(e.target.closest('[data-trait-next]')&&traitSession){traitSession.at=(traitSession.at+1)%traitSession.order.length;traitSession.checked=false;traitSession.selections={};renderTraits()}
 if(e.target.closest('[data-trait-shuffle]')&&traitSession)startTraits(traitSession.kind);
 if(e.target.closest('[data-trait-close]')){$('#trait-practice').hidden=true;traitSession=null}
 const mode=e.target.closest('[data-path-mode]');if(mode)setPathMode(mode.dataset.pathMode);
 if(e.target.closest('[data-path-reveal]')&&pathSession){pathSession.revealed=!pathSession.revealed;renderPathPractice()}
 const step=e.target.closest('[data-path-step]');if(step&&pathSession){pathSession.at=(pathSession.at+(step.dataset.pathStep==='+'?1:-1)+pathSession.order.length)%pathSession.order.length;pathSession.revealed=false;renderPathPractice()}
 if(e.target.closest('[data-path-shuffle]')&&pathSession){pathSession.order=shuffled(pathSession.order);pathSession.at=0;pathSession.revealed=false;renderPathPractice()}
 const grade=e.target.closest('[data-path-grade]');if(grade&&pathSession){state.rapidPathology[`sheet-${pathSession.order[pathSession.at]}`]=grade.dataset.pathGrade;save();pathSession.at=(pathSession.at+1)%pathSession.order.length;pathSession.revealed=false;renderPathPractice()}
 if(e.target.closest('[data-path-clear]')&&pathSession){state.pathDrawings[`pathology-${pathSession.order[pathSession.at]}`]=[];save();renderPathPractice()}
 const image=e.target.closest('[data-path-image]');if(image){const [row,index]=image.dataset.pathImage.split('-').map(Number),recall=!!pathSession&&!pathSession.revealed&&pathSession.mode!=='draw',all=DATA.pathology_images?.[String(row)]||[],figures=all.map(f=>({...f,caption:recall?(f.recall_prompt===false?'Labeled reference figure':'Source image · identify the process'):f.caption,source_url:f.source_url||(f.source_book?DATA.book_pages.books[f.source_book]?.url:'')}));if(figures.length)openImageGallery({key:'path:'+row,images:figures,index,title:'Pathology images'})}
 const shuffle=e.target.closest('[data-shuffle-sheet]');if(shuffle){const body=$(`#sheet-${shuffle.dataset.shuffleSheet} tbody`);shuffled([...body.children]).forEach(row=>body.append(row))}
});
document.addEventListener('keydown',e=>{if(currentView!=='pathology'||!pathSession||pathSession.mode==='table'||e.target.closest('input,textarea,select,[contenteditable=true]')||!$('#side-panel').hidden)return;if(e.key===' '){e.preventDefault();pathSession.revealed=!pathSession.revealed;renderPathPractice()}if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();pathSession.at=(pathSession.at+(e.key==='ArrowRight'?1:-1)+pathSession.order.length)%pathSession.order.length;pathSession.revealed=false;renderPathPractice()}});
