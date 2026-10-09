/* The local card graph uses exported card→note, note→tag and note→media edges.
   Text matches are ranked separately; tag neighbors are never labeled as exact
   topic matches. No scheduling or collection writes happen in this viewer. */
const ankiLibrary=window.ANKI_CONTEXT||{notes:[],cards:[],scope:{},graph:{}};
const ankiNotes=new Map(ankiLibrary.notes.map(n=>[Number(n.id),n]));
const ankiCards=new Map(ankiLibrary.cards.map(c=>[Number(c.id),c]));
const ankiTopicMap=DATA.anki_topic_media?.topics||{};
const ankiPostings=new Map(),ankiNoteWords=new Map(),ankiSearchText=new Map(),ankiFieldText=new Map(),ankiTermSources=new Map(),ankiTagEdges=new Map(),ankiMediaEdges=new Map();
const ankiCanonicalAliases={'Brown-Séquard syndrome':'Brown-Séquard','amyotrophic lateral sclerosis':'ALS','Duchenne muscular dystrophy':'Duchenne','DMD':'Duchenne','Gowers’ sign':'Gowers','Immune-mediated necrotizing myopathy':'IMNM','Subacute combined degeneration (B12 deficiency)':'B12 deficiency','IBM':'inclusion body myositis'};
Object.assign(ankiCanonicalAliases,DATA.reference_aliases||{});
let ankiContext=null,ankiReferenceMode='images',ankiCurrentCard=0,ankiCardRevealed=false;
state.referenceViews||={};state.ankiMediaPosition||={};state.ankiCardPosition||={};
const ankiStop=new Set('a an the and or but of to in on at by for with from as is are was were be been being this that these those which what how when where why who it its their they them he she his her you your can may will would should could does do did has have had than then also such most more less not no yes each all both same one two three type types patient patients finding findings following cause causes caused disease diseases syndrome symptoms sign signs associated describe compare identify explain discuss characterize using used use due likely seen occurs occur known result results'.split(' '));
function ankiPlain(html){const el=document.createElement('template');el.innerHTML=html||'';return el.content.textContent.replace(/\{\{c\d+::/gi,'').replace(/\}\}/g,'').replace(/\s+/g,' ').trim()}
function ankiNormalize(value){return String(value||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[’']/g,'').toLowerCase()}
function ankiWords(value){return [...new Set((ankiNormalize(value).match(/[a-z0-9]+/g)||[]).map(w=>w.length>5&&w.endsWith('s')?w.slice(0,-1):w).filter(w=>w.length>1&&!ankiStop.has(w)&&!/^c\d+$/.test(w)))]}
function ankiEdge(map,key,noteId){if(!map.has(key))map.set(key,new Set());map.get(key).add(noteId)}
function ankiSpecificTags(note){return [...new Set([...(note.courseTags||[]),...(note.quizTags||[]),...(note.resourceTags||[])])].filter(t=>{
 if(/::(?:AnKing|TY|Ty)$/i.test(t))return t.split('::').length>=5;
 return t.split('::').length>=4&&!/::(?:NPS|Quiz[_ ]?0?\d+|AnKing|!Ty)$/i.test(t);
})}
function ankiTagLabel(tag){const parts=tag.split('::').filter(p=>!/^#AK_|^#StudyOS$|^AnkiHub_Optional$|^AnKing$|^Ty$/i.test(p));return parts.slice(-3).join(' › ').replace(/_/g,' ')}
for(const note of ankiLibrary.notes){
 const plain=ankiPlain(ankiRenderClozes(note.html,0,true)),back=ankiPlain(ankiRenderClozes(note.extraHtml,0,true)),table=(note.tables||[]).map(t=>ankiPlain(ankiRenderClozes(t.html,0,true))).join(' ');ankiSearchText.set(Number(note.id),plain);ankiFieldText.set(Number(note.id),{front:plain,back,table});
 const sources={front:new Set(ankiWords(plain)),back:new Set(ankiWords(back)),table:new Set(ankiWords(table))};ankiTermSources.set(Number(note.id),sources);
 const words=[...new Set([...sources.front,...sources.back,...sources.table])];ankiNoteWords.set(Number(note.id),new Set(words));
 for(const word of words){if(!ankiPostings.has(word))ankiPostings.set(word,[]);ankiPostings.get(word).push(Number(note.id))}
 for(const tag of ankiSpecificTags(note))ankiEdge(ankiTagEdges,tag,Number(note.id));
 for(const im of note.images||[])ankiEdge(ankiMediaEdges,im.file,Number(note.id));
}
function ankiTopicAliases(key){return [key,...(ankiTopicMap[key]?.aliases||[])].filter(Boolean)}
function ankiTopicMatches(key,note){
 const config=ankiTopicMap[key]||{},fields=ankiFieldText.get(Number(note.id))||{front:note.plain||''};
 const expressions=config.patterns?.length?config.patterns:ankiTopicAliases(key).map(s=>'(?<![\\p{L}\\p{N}])'+s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'(?![\\p{L}\\p{N}])');
 for(const field of ['front','back','table'])if(expressions.some(p=>{try{return new RegExp(p,'iu').test(fields[field]||'')}catch{return false}}))return field;
 return '';
}
function ankiDetectTopics(text){
 const normalized=ankiNormalize(text),hits=[];
 for(const key of Object.keys(ankiTopicMap)){
  const aliases=ankiTopicAliases(key);
  if(aliases.some(a=>{const term=ankiNormalize(a);if(term.length<3)return false;const at=normalized.indexOf(term);if(at<0)return false;return !/[a-z0-9]/.test(normalized[at-1]||'')&&!/[a-z0-9]/.test(normalized[at+term.length]||'')}))hits.push(key);
 }
 return [...new Set(hits.map(key=>ankiCanonicalAliases[key]||key))].sort((a,b)=>b.length-a.length);
}
function ankiRankText(text){
 const terms=ankiWords(text).filter(w=>ankiPostings.has(w));if(!terms.length)return [];
 const total=ankiLibrary.notes.length,weight=w=>Math.log(1+(total-(ankiPostings.get(w)?.length||0)+.5)/((ankiPostings.get(w)?.length||0)+.5));
 const query=terms.sort((a,b)=>weight(b)-weight(a)).slice(0,50),found=new Map();
 for(const word of query)for(const id of ankiPostings.get(word)){const item=found.get(id)||{id,score:0,hits:[],reasons:[]},sources=ankiTermSources.get(id);item.score+=weight(word)*(sources.front.has(word)?1.35:.85);item.hits.push(word);found.set(id,item)}
 return [...found.values()].filter(item=>query.length<=2?item.hits.length===query.length:item.hits.length>=Math.min(3,query.length)&&item.score>=6).map(item=>{
  const size=ankiNoteWords.get(item.id)?.size||1,sources=ankiTermSources.get(item.id);item.score/=Math.pow(Math.max(8,size),.18);const fields=['front','back','table'].filter(field=>item.hits.some(word=>sources[field].has(word)));item.reasons=[`Text match (${fields.join('/')}): ${item.hits.slice(0,5).join(', ')}`];return item;
 }).sort((a,b)=>b.score-a.score).slice(0,250);
}
function ankiNeighbors(noteId){
 const note=ankiNotes.get(Number(noteId));if(!note)return [];
 const result=new Map(),add=(id,score,reason)=>{const item=result.get(id)||{id,score:0,reasons:[]};item.score+=score;if(!item.reasons.includes(reason))item.reasons.push(reason);result.set(id,item)};
 add(Number(note.id),80,'Same original note');
 for(const tag of ankiSpecificTags(note)){
  const linked=ankiTagEdges.get(tag)||new Set();if(linked.size>350)continue;
  const course=(note.courseTags||[]).includes(tag),quiz=(note.quizTags||[]).includes(tag),score=(course?8:quiz?7:4)+Math.log(1+ankiLibrary.notes.length/Math.max(1,linked.size));
  for(const id of linked)if(id!==Number(note.id))add(id,score,`Shared ${course?'course topic':quiz?'quiz topic':'resource section'}: ${ankiTagLabel(tag)}`);
 }
 for(const image of note.images||[]){const linked=ankiMediaEdges.get(image.file)||new Set();if(linked.size>100)continue;for(const id of linked)if(id!==Number(note.id))add(id,6,'Same original card image')}
 return [...result.values()].sort((a,b)=>b.score-a.score).slice(0,250);
}
function ankiMatchContext(context){
 if(context.fromNote)return ankiNeighbors(context.fromNote);
 const results=new Map(),topics=context.topics||[];
 for(const key of topics){
  const preferred=ankiTopicMap[key]?.noteIds||[];
  for(const note of ankiLibrary.notes){const at=preferred.indexOf(Number(note.id)),field=ankiTopicMatches(key,note);if(at<0&&!field)continue;const item=results.get(Number(note.id))||{id:Number(note.id),score:0,reasons:[]};item.score+=at>=0?50+Math.max(0,20-at):field==='front'?30:16;const reason=`${at>=0?'Topic link':'Named in card '+field}: ${key}`;if(!item.reasons.includes(reason))item.reasons.push(reason);results.set(item.id,item)}
 }
 for(const hit of ankiRankText(context.text)){const item=results.get(hit.id);if(item){item.score+=hit.score;if(context.kind!=='topic')item.reasons.push(...hit.reasons)}else if(context.kind!=='topic')results.set(hit.id,hit)}
 return [...results.values()].sort((a,b)=>b.score-a.score);
}
function ankiCardResults(context=ankiContext){
 if(!context)return [];
 const filter=context.filter||'all',query=ankiNormalize(context.filterText||'');
 return context.matches.flatMap(hit=>{const note=ankiNotes.get(hit.id);if(!note)return [];if(filter==='Ty'&&note.kind!=='Ty')return [];if(filter==='AnKing'&&note.kind!=='AnKing')return [];if(query&&!ankiNormalize(note.plain+' '+ankiPlain(note.extraHtml)).includes(query))return [];
  return (note.cardIds||[]).map(id=>ankiCards.get(Number(id))).filter(Boolean).sort((a,b)=>a.ord-b.ord).map(card=>({card,note,reasons:hit.reasons,score:hit.score}));
 });
}
function ankiUsefulImage(im){return im.kind!=='mask'&&(!im.width||!im.height||im.width>=160&&im.height>=85)&&!/(?:^|[_-])(?:logo|icon|button|play|anki[_-]?hub|anking[_-]?v\d|transparent)(?:[_-]|\.)/i.test(im.file)}
function ankiImageScore(image,note,hit){const field=image.field||'',kind=image.kind||'';return hit.score+(kind==='table'?10:kind==='diagram'?8:0)+(/^(Text|Extra|Lecture Notes|Image|Front|Back)$/i.test(field)?10:/^(First Aid|Pathoma)$/i.test(field)?-10:/Sketchy/i.test(field)?-15:-2)}
function ankiMediaResults(context=ankiContext){
 if(!context)return [];
 const results=[],seen=new Set(),push=item=>{const key=item.type==='table'?`table:${item.note.id}:${item.field}:${item.html}`:item.file;if(!seen.has(key)){seen.add(key);results.push(item)}};
 for(const key of context.topics||[])for(const preferred of ankiTopicMap[key]?.preferredImages||[]){const note=ankiNotes.get(Number(preferred.noteId));if(!note)continue;const im=(note.images||[]).find(x=>x.file===preferred.file&&(!preferred.field||x.field===preferred.field));if(im&&ankiUsefulImage(im))push({...im,note,type:'image',score:1000,caption:preferred.caption||`${key} · original ${im.field} image`})}
 const rest=[];
 for(const hit of context.matches.slice(0,55)){const note=ankiNotes.get(hit.id);if(!note)continue;for(const table of note.tables||[])rest.push({...table,note,type:'table',score:hit.score+15,caption:`${context.topic||'Related topic'} · original ${table.field} table`});for(const im of note.images||[])if(ankiUsefulImage(im))rest.push({...im,note,type:'image',score:ankiImageScore(im,note,hit),caption:`${context.topic||'Related topic'} · original ${im.field} image`})}
 rest.sort((a,b)=>b.score-a.score).forEach(push);return results.slice(0,40);
}
function ankiContextKey(context){return context.kind==='topic'?`topic:${context.topic}`:context.fromNote?`note:${context.fromNote}`:`${context.kind}:${context.blockId||''}:${context.text.slice(0,220)}`}
function ankiMakeContext(options){const topic=options.topic||'',text=options.text||topic,topics=[...new Set((options.topics||ankiDetectTopics(text)).map(key=>ankiCanonicalAliases[key]||key))],canonical=ankiCanonicalAliases[topic]||topic;const context={kind:options.kind||'selection',topic,text,title:options.title||topic||'Cards for selected text',topics,blockId:options.blockId||'',fromNote:options.fromNote||0,filter:'all',filterText:'',back:options.back||null};if(canonical&&!context.topics.includes(canonical))context.topics.unshift(canonical);context.key=ankiContextKey(context);context.matches=ankiMatchContext(context);return context}
function ankiBookAvailable(topic,book){return !!DATA.book_pages.keywords[topic]?.[book]?.pages?.length}
function referenceTabsMarkup(mode='books'){
 const topic=ankiContext?.topic||activeRef,count=ankiCardResults().length,hasFA=ankiBookAvailable(topic,'First Aid'),hasPath=ankiBookAvailable(topic,'Pathoma');
 return `<div class="reference-view-tabs" role="group" aria-label="Topic reference view"><button data-reference-view="images" class="${mode==='images'?'selected':''}">Images & tables</button><button data-reference-view="cards" class="${mode==='cards'?'selected':''}">Related cards${count?' · '+count:''}</button>${['First Aid','Pathoma','Both'].map(book=>`<button data-panel-book="${book}" class="${mode==='books'&&referenceBook===book?'selected':''}" ${book==='Both'?hasFA||hasPath?'':'disabled':book==='First Aid'?hasFA?'':'disabled':hasPath?'':'disabled'}>${book}</button>`).join('')}</div>`;
}
function rememberReferenceBook(){if(ankiContext){ankiReferenceMode='books';state.referenceViews[ankiContext.key]='books'}save()}
function openStudyReference(topic,book){
 ankiContext=ankiMakeContext({kind:'topic',topic,text:referenceOrigin?.topic===topic?topic+' '+referenceOrigin.text:topic,title:topic,topics:[topic]});activeRef=topic;ankiCardRevealed=false;ankiCurrentCard=Number(state.ankiCardPosition[ankiContext.key])||0;
 const media=ankiMediaResults(),config=ankiTopicMap[topic]||{},hasBook=ankiBookAvailable(topic,'First Aid')||ankiBookAvailable(topic,'Pathoma');
 ankiReferenceMode=book?'books':state.referenceViews[ankiContext.key]||(config.defaultView==='books'&&hasBook?'books':media.length?'images':hasBook?'books':'cards');
 if(ankiReferenceMode==='books'){openBookReference(topic,book);rememberReferenceBook()}else renderAnkiReference();
}
function openAnkiContext(options){
 const node=options.node;ankiContext=ankiMakeContext(options);activeRef=ankiContext.topic||ankiContext.topics.find(t=>ankiBookAvailable(t,'First Aid')||ankiBookAvailable(t,'Pathoma'))||'';
 if(!ankiContext.topic&&activeRef)ankiContext.topic=activeRef;if(node&&activeRef)setReferenceContext(activeRef,node);
 ankiCardRevealed=false;ankiCurrentCard=options.cardId||Number(state.ankiCardPosition[ankiContext.key])||0;ankiReferenceMode=options.view||'cards';renderAnkiReference();
}
window.openAnkiContext=openAnkiContext;
function ankiContextMarkup(){if(!ankiContext)return '';const context=ankiContext;return `${context.back?'<button data-anki-context-back>← Back to topic cards</button>':''}${context.kind==='topic'?'':`<p class="anki-context-intro">${context.fromNote?'Connections from the selected note':'Related to '+esc(context.kind==='table'?'this table':context.kind==='row'?'this comparison row':'your selected text')}</p><blockquote class="anki-context-quote">${esc(context.text)}</blockquote>`}${context.topics.length>1?`<div class="anki-topic-options" aria-label="Book topic for this passage">${context.topics.slice(0,12).map(topic=>`<button data-anki-book-topic="${esc(topic)}" class="${topic===context.topic?'selected':''}">${esc(topic)}</button>`).join('')}</div>`:''}`}
function ankiMediaMarkup(){
 const media=ankiMediaResults(),context=ankiContext;if(!media.length)return `<div class="anki-empty">No useful local card image or table was found for this passage. The related cards and available book sections remain above.</div>`;
 let index=Number(state.ankiMediaPosition[context.key])||0;index=Math.max(0,Math.min(media.length-1,index));const item=media[index];state.ankiMediaPosition[context.key]=index;
 const sourceCard=(item.note.cardIds||[]).find(id=>ankiCards.has(Number(id)));
 return `<div class="anki-media-navigation"><button data-anki-media-step="-" aria-label="Previous Anki image" ${index===0?'disabled':''}>‹</button><span>${index+1} / ${media.length} images & tables</span><button data-anki-media-step="+" aria-label="Next Anki image" ${index===media.length-1?'disabled':''}>›</button></div><figure class="anki-primary-media">${item.type==='table'?`<div class="anki-table">${ankiRenderClozes(item.html,0,true)}</div>`:`<div class="panel-media"><img src="${esc(item.src)}" alt="${esc(item.caption)}"></div>`}<figcaption>${esc(item.caption)}<br><span class="anki-kind">${item.note.kind==='Ty'?'!Ty':esc(item.note.kind)}</span> Note ${item.note.id} · ${esc(item.field||'')} <button data-anki-open-card="${sourceCard||''}">See source card</button></figcaption></figure><div class="anki-media-strip" aria-label="Related card images and tables">${media.slice(0,24).map((im,i)=>`<button data-anki-media-index="${i}" class="${i===index?'selected':''}" aria-label="Show Anki ${im.type} ${i+1}">${im.type==='table'?'Table':`<img loading="lazy" src="${esc(im.src)}" alt="${esc(im.caption)}">`}</button>`).join('')}</div><div class="panel-zoom"><button data-panel-zoom="-">−</button><span>Image zoom</span><button data-panel-zoom="+">+</button></div>${referenceCourseSources()}`;
}
// Parse balanced clozes so nested AnKing clozes do not leak a hidden answer.
function ankiRenderClozes(html,target,revealed){
 const text=String(html||''),rx=/\{\{c(\d+)::/gi;let out='',cursor=0,match;
 while((match=rx.exec(text))){if(match.index<cursor)continue;out+=text.slice(cursor,match.index);let at=rx.lastIndex,depth=1,end=-1;
  for(;at<text.length-1;at++){if(text.slice(at,at+2)==='{{'){depth++;at++}else if(text.slice(at,at+2)==='}}'){depth--;if(!depth){end=at;break}at++}}
  if(end<0){out+=text.slice(match.index);return out}
  const raw=text.slice(rx.lastIndex,end);let body=raw,hint='',nested=0;
  for(let i=0;i<raw.length-1;i++){const pair=raw.slice(i,i+2);if(pair==='{{'){nested++;i++}else if(pair==='}}'){nested--;i++}else if(pair==='::'&&!nested){body=raw.slice(0,i);hint=raw.slice(i+2);break}}
  const number=Number(match[1]),hidden=number===target&&!revealed;
  out+=hidden?`<span class="anki-cloze">[${hint?esc(ankiPlain(hint)):'…'}]</span>`:`<span class="${number===target?'anki-cloze-answer':'anki-visible-cloze'}">${ankiRenderClozes(body,target,revealed)}</span>`;
  cursor=end+2;rx.lastIndex=cursor;
 }
 return out+text.slice(cursor);
}
function ankiCardLabel(item){const plain=ankiPlain(ankiRenderClozes(item.note.html,0,true));return `${item.note.kind==='Ty'?'!Ty':item.note.kind} · ${plain.slice(0,125)}${plain.length>125?'…':''} · ${item.card.id}`}
function ankiIOMarkup(note,revealed){const io=note.io;return `${io.headerHtml||''}<div class="anki-io">${io.imageHtml||''}<div class="anki-io-mask">${revealed?io.answerMaskHtml||'':io.questionMaskHtml||''}</div></div>`}
function ankiCardsMarkup(){
 const cards=ankiCardResults(),context=ankiContext;let index=cards.findIndex(x=>Number(x.card.id)===Number(ankiCurrentCard));if(index<0)index=0;const item=cards[index];
 const filters=`<div class="anki-filters" role="group" aria-label="Related card scope">${[['all','All'],['Ty','!Ty'],['AnKing','AnKing']].map(([value,label])=>`<button data-anki-filter="${value}" aria-pressed="${context.filter===value}">${label}</button>`).join('')}<label>Find in matches<input type="search" data-anki-card-search value="${esc(context.filterText)}" placeholder="Narrow these cards…"></label></div>`;
 if(!item)return `${filters}<div class="anki-empty">No card matches this ${context.fromNote?'connection filter':'passage and filter'}. Try selecting the specific medical term or a shorter sentence.</div>`;
 ankiCurrentCard=Number(item.card.id);state.ankiCardPosition[context.key]=ankiCurrentCard;
 const cloze=/\{\{c\d+::/i.test(item.note.html),front=item.note.io?ankiIOMarkup(item.note,ankiCardRevealed):ankiRenderClozes(item.note.html,item.card.ord+1,ankiCardRevealed),connections=ankiNeighbors(item.note.id),neighborCards=connections.reduce((sum,n)=>sum+(ankiNotes.get(n.id)?.cardIds?.length||0),0);
 return `${filters}<label class="anki-card-selector">${cards.length} related cards · choose a card<select data-anki-card-choice aria-label="Choose related Anki card">${cards.map((entry,i)=>`<option value="${entry.card.id}" ${i===index?'selected':''}>${esc(ankiCardLabel(entry))}</option>`).join('')}</select></label><div class="anki-card-identity"><span class="anki-kind">${item.note.kind==='Ty'?'!Ty':esc(item.note.kind)}</span><span>Card ${item.card.id} · Note ${item.note.id}${cloze?' · cloze '+(item.card.ord+1):''}</span></div><p class="anki-match-reason">${esc(item.reasons.slice(0,3).join(' · '))}</p><div class="anki-card-content">${front}${ankiCardRevealed&&item.note.extraHtml?`<div class="anki-card-extra">${ankiRenderClozes(item.note.extraHtml,0,true)}</div>`:''}</div><div class="anki-card-navigation"><button data-anki-card-step="-" ${index===0?'disabled':''}>← Previous</button><span>${index+1} / ${cards.length}</span><button data-anki-card-step="+" ${index===cards.length-1?'disabled':''}>Next →</button><button data-anki-reveal>${ankiCardRevealed?'Hide answer':cloze?'Reveal answer · Space':'Show back · Space'}</button></div><details class="anki-graph"><summary>Card connections</summary><p>Card → note → shared course topics, resource sections and original media. Text matches above are separate from these connections.</p><div class="anki-connection-tags">${(item.note.quizTags||[]).slice(0,2).map(tag=>`<span class="anki-connection-tag">${esc(ankiTagLabel(tag))}</span>`).join('')}${ankiSpecificTags(item.note).slice(0,8).map(tag=>`<span class="anki-connection-tag" title="${esc(tag)}">${esc(ankiTagLabel(tag))}</span>`).join('')}</div><button data-anki-neighbors="${item.note.id}">Follow shared connections · ${neighborCards} cards</button></details>${referenceCourseSources()}`;
}
function renderAnkiReference(options={}){
 if(!ankiContext)return;if(ankiReferenceMode==='books'){openBookReference(activeRef,referenceBook);return}
 const sameContext=$('#side-panel').dataset.ankiContextKey===ankiContext.key;
 bookMarkMode=false;showSidePanel(ankiContext.title,`${referenceTabsMarkup(ankiReferenceMode)}${ankiContextMarkup()}${ankiReferenceMode==='images'?ankiMediaMarkup():ankiCardsMarkup()}`,{keepScroll:options.keepScroll??sameContext,anchor:referenceOrigin?.element});
 $('#side-panel').dataset.ankiContextKey=ankiContext.key;
 state.referenceViews[ankiContext.key]=ankiReferenceMode;save();
}
function ankiStepCard(delta){const cards=ankiCardResults(),index=cards.findIndex(x=>Number(x.card.id)===ankiCurrentCard);if(!cards.length)return;ankiCurrentCard=Number(cards[Math.max(0,Math.min(cards.length-1,index+delta))].card.id);ankiCardRevealed=false;renderAnkiReference()}
document.addEventListener('click',e=>{
 const view=e.target.closest('[data-reference-view]');if(view&&ankiContext){ankiReferenceMode=view.dataset.referenceView;ankiCardRevealed=false;renderAnkiReference();return}
 const mediaIndex=e.target.closest('[data-anki-media-index]');if(mediaIndex&&ankiContext){state.ankiMediaPosition[ankiContext.key]=Number(mediaIndex.dataset.ankiMediaIndex);renderAnkiReference();return}
 const mediaStep=e.target.closest('[data-anki-media-step]');if(mediaStep&&ankiContext){const max=ankiMediaResults().length-1;state.ankiMediaPosition[ankiContext.key]=Math.max(0,Math.min(max,(Number(state.ankiMediaPosition[ankiContext.key])||0)+(mediaStep.dataset.ankiMediaStep==='+'?1:-1)));renderAnkiReference();return}
 const card=e.target.closest('[data-anki-open-card]');if(card&&card.dataset.ankiOpenCard){ankiContext.filter='all';ankiContext.filterText='';ankiCurrentCard=Number(card.dataset.ankiOpenCard);ankiReferenceMode='cards';ankiCardRevealed=false;renderAnkiReference();return}
 const reveal=e.target.closest('[data-anki-reveal]');if(reveal){ankiCardRevealed=!ankiCardRevealed;renderAnkiReference({keepScroll:true});return}
 const step=e.target.closest('[data-anki-card-step]');if(step){ankiStepCard(step.dataset.ankiCardStep==='+'?1:-1);return}
 const filter=e.target.closest('[data-anki-filter]');if(filter&&ankiContext){ankiContext.filter=filter.dataset.ankiFilter;ankiCardRevealed=false;renderAnkiReference();return}
 const neighbors=e.target.closest('[data-anki-neighbors]');if(neighbors&&ankiContext){const note=ankiNotes.get(Number(neighbors.dataset.ankiNeighbors));if(note)openAnkiContext({kind:'connections',fromNote:note.id,text:ankiPlain(ankiRenderClozes(note.html,0,true)),title:'Connected Anki cards',topics:ankiContext.topics,topic:ankiContext.topic,back:ankiContext});return}
 if(e.target.closest('[data-anki-context-back]')&&ankiContext?.back){ankiContext=ankiContext.back;activeRef=ankiContext.topic;ankiCardRevealed=false;ankiCurrentCard=Number(state.ankiCardPosition[ankiContext.key])||0;ankiReferenceMode='cards';renderAnkiReference();return}
 const topic=e.target.closest('[data-anki-book-topic]');if(topic&&ankiContext){ankiContext.topic=topic.dataset.ankiBookTopic;activeRef=ankiContext.topic;renderAnkiReference();return}
 const rowButton=e.target.closest('[data-anki-row]');if(rowButton){const row=rowButton.closest('[data-sheet-row]'),sheet=DATA.comparison_sheets.find(s=>s.id===row.dataset.sheetRow),entry=sheet?.rows[Number(row.dataset.sheetIndex)];if(entry){const text=entry.cells.map((cell,i)=>sheet.columns[i]+': '+ankiPlain(cell)).join('\n');const title=ankiPlain(entry.cells[0]);openAnkiContext({kind:'row',title:`Cards · ${title}`,text,node:row,blockId:`sheet-${sheet.id}-${row.dataset.sheetIndex}`})}return}
 const tableButton=e.target.closest('[data-anki-table-block]');if(tableButton){const block=tableButton.closest('[data-block]'),text=[...block.querySelectorAll('.editable table')].map(t=>t.innerText).join('\n');openAnkiContext({kind:'table',text,title:'Cards for this table',node:block,blockId:tableButton.dataset.ankiTableBlock});return}
});
document.addEventListener('change',e=>{if(e.target.matches('[data-anki-card-choice]')){ankiCurrentCard=Number(e.target.value);ankiCardRevealed=false;renderAnkiReference()}});
document.addEventListener('input',e=>{if(!e.target.matches('[data-anki-card-search]')||!ankiContext)return;const value=e.target.value,position=e.target.selectionStart;ankiContext.filterText=value;ankiCardRevealed=false;renderAnkiReference({keepScroll:true});const input=$('[data-anki-card-search]');input?.focus();try{input?.setSelectionRange(position,position)}catch{}});
document.addEventListener('keydown',e=>{
 if($('#side-panel').hidden||ankiReferenceMode!=='cards'||!$('#side-content [data-anki-reveal]')||e.target.closest('input,textarea,select,[contenteditable="true"]')||document.querySelector('dialog[open]')||!window.getSelection()?.isCollapsed)return;
 if(e.key===' '){e.preventDefault();if(ankiCardRevealed)ankiStepCard(1);else{ankiCardRevealed=true;renderAnkiReference({keepScroll:true})}}
 else if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();ankiStepCard(e.key==='ArrowRight'?1:-1)}
});
