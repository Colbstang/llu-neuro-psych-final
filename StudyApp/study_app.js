/* The app keeps personal state in SQLite. Anki alone schedules real reviews. */
const studyAppConfig=window.STUDY_APP_CONFIG||null;
let studyAppReady=false,studyAppRevision=0,studyAppBaseline={},studyAppSaving=false,studyAppTimer=null,studyAppConnection=null;
let studyAppAnkiSession=null,studyAppReviewPending=false,studyAppReviewMessage='',studyAppCoverage={summary:{},cards:{}};
const studyAppBrowserSave=save;
function studyAppCopy(value){return JSON.parse(JSON.stringify(value))}
function studyAppDiff(before,after){
 if(referenceEqual(before,after))return undefined;
 if(referenceObject(before)&&referenceObject(after)){
  const patch={};for(const key of new Set([...Object.keys(before),...Object.keys(after)])){
   if(!(key in after))patch[key]=null;else{const part=studyAppDiff(before[key],after[key]);if(part!==undefined)patch[key]=part}
  }return Object.keys(patch).length?patch:undefined;
 }
 if(Array.isArray(before)&&Array.isArray(after)&&[...before,...after].every(item=>referenceObject(item)&&item.id)){
  const previous=new Map(before.map(item=>[String(item.id),item])),next=new Map(after.map(item=>[String(item.id),item]));
  const remove=[...previous.keys()].filter(id=>!next.has(id)),upsert=after.filter(item=>!referenceEqual(previous.get(String(item.id)),item));
  return remove.length||upsert.length?{__llu_array_delta__:{remove,upsert}}:undefined;
 }
 return studyAppCopy(after);
}
function studyAppStatus(message){const target=$('#save-status');if(target)target.textContent=message}
async function studyAppRequest(route,body){
 const options={cache:'no-store',headers:{'X-LLU-App-Token':studyAppConfig.token}};
 if(body!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(body)}
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),route==='/anki/sync'?120000:20000);options.signal=controller.signal;
 try{const response=await fetch(studyAppConfig.apiBase+route,options),value=await response.json();if(!response.ok)throw Error(value.error||value.detail||'The app service could not complete this request.');return value}finally{clearTimeout(timer)}
}
save=function(){
 studyAppBrowserSave();if(!studyAppConfig)return;
 if(!studyAppReady){studyAppStatus('Loading saved progress…');return}
 if(studyAppDiff(studyAppBaseline,state)===undefined){studyAppStatus('Saved to app');return}
 studyAppStatus('Saving to app…');clearTimeout(studyAppTimer);studyAppTimer=setTimeout(studyAppFlush,250);
};
async function studyAppFlush(){
 if(!studyAppReady||studyAppSaving)return;studyAppSaving=true;
 try{
  while(true){const sent=studyAppCopy(state),patch=studyAppDiff(studyAppBaseline,sent);if(patch===undefined)break;
   const result=await studyAppRequest('/progress',{patch,base_revision:studyAppRevision});
   state={...state,...referenceMergeDelta(sent,state,result.state)};studyAppBaseline=studyAppCopy(result.state);studyAppRevision=result.revision;studyAppBrowserSave();
  }studyAppStatus('Saved to app');
 }catch{studyAppStatus('Saved in this window · app save needs retry');clearTimeout(studyAppTimer);studyAppTimer=setTimeout(studyAppFlush,5000)}finally{studyAppSaving=false}
}
async function studyAppRefresh(){
 if(!studyAppReady||studyAppSaving||studyAppDiff(studyAppBaseline,state)!==undefined)return;
 const revision=studyAppRevision,baseline=studyAppCopy(studyAppBaseline);
 try{const result=await studyAppRequest('/progress');
  if(studyAppSaving||studyAppRevision!==revision||studyAppDiff(baseline,state)!==undefined)return;
  if(result.revision!==studyAppRevision){state={...state,...referenceMergeDelta(baseline,state,result.state)};studyAppBaseline=studyAppCopy(result.state);studyAppRevision=result.revision;studyAppBrowserSave()}
  studyAppStatus('Saved to app');
 }catch{}
}
function studyAppProgressNotice(){
 if($('#study-app-import-notice'))return;const notice=document.createElement('aside');notice.id='study-app-import-notice';notice.className='study-app-import-notice';notice.innerHTML='<span>Your earlier browser progress can be brought into the app. In the old guide, choose <b>Export progress</b>; here, choose <b>Import progress</b>.</span><button data-app-dismiss-import aria-label="Dismiss progress import reminder">×</button>';document.querySelector('.shell').prepend(notice);
}
function studyAppDialog(){
 let dialog=$('#study-app-settings');if(dialog)return dialog;
 dialog=document.createElement('dialog');dialog.id='study-app-settings';dialog.innerHTML='<div class="app-dialog-head"><h2>Study app</h2><button data-app-close aria-label="Close app status">×</button></div><p data-app-state-status>Progress is saved on this Mac, with automatic backups.</p><div data-app-anki-status></div><button data-app-anki-sync>Sync Anki reviews</button><p data-app-anki-sync-status role="status"></p><p class="app-help">Anki owns scheduling. Reading, revealing answers, and marking a section learned do not record an Anki review. Use <b>Review in Anki</b> on a card, reveal its answer, then choose a rating.</p>';document.body.append(dialog);return dialog;
}
function studyAppSummaryMarkup(){
 const summary=studyAppCoverage.summary||{},cards=Object.values(studyAppCoverage.cards||{}),reviewed=cards.filter(card=>card.reviewed||card.review_count>0).length;
 const connected=!!studyAppConnection?.available,scoped=Number(studyAppConnection?.allowed_card_count||0)>0;
 return `<p><b>${connected?(scoped?'Anki connected':'Anki connected · no card scope imported'):'Anki is not connected'}</b>${studyAppConnection?.anki_connect_version?' · AnkiConnect '+esc(studyAppConnection.anki_connect_version):''}</p><p>${reviewed.toLocaleString()} linked cards reviewed${summary.review_count?' · '+Number(summary.review_count).toLocaleString()+' recorded reviews':''}${summary.synced_at?' · Last synced '+esc(new Date(summary.synced_at).toLocaleString()):''}</p>`;
}
async function studyAppLoadAnki(){
 try{studyAppConnection=await studyAppRequest('/anki/status');studyAppCoverage=studyAppConnection.coverage||await studyAppRequest('/anki/coverage')}catch{studyAppConnection={available:false}}
 const button=$('#study-app-button');if(button)button.textContent=studyAppConnection?.available?(Number(studyAppConnection.allowed_card_count||0)>0?'App · Anki connected':'App · Anki scope needed'):'App · connect Anki';
 const target=$('[data-app-anki-status]');if(target)target.innerHTML=studyAppSummaryMarkup();
}
async function studyAppSync(){
 const target=$('[data-app-anki-sync-status]'),button=$('[data-app-anki-sync]');if(button)button.disabled=true;if(target)target.textContent='Reading your Anki review history…';
 try{if(!studyAppConnection?.available){if(target)target.textContent='AnkiConnect is not available.';return}if(!Number(studyAppConnection.allowed_card_count||0)){if(target)target.textContent='No Anki card scope has been imported.';return}studyAppCoverage=await studyAppRequest('/anki/sync',{});await studyAppLoadAnki();if(target)target.textContent='Review history synced. Your learned sections stay as you marked them.';studyAppRefreshReviewControls()}
 catch(error){if(target)target.textContent=error.message}finally{if(button)button.disabled=false}
}
function studyAppTopicCoverage(){
 const config=ankiTopicMap[ankiContext?.topic],noteIds=new Set((config?.noteIds||[]).map(Number));if(!noteIds.size)return '';
 const linked=ankiLibrary.cards.filter(card=>noteIds.has(Number(card.note))),reviewed=linked.filter(card=>studyAppCoverage.cards?.[String(card.id)]?.review_count>0).length;
 return `<p class="app-topic-coverage">Anki practice: ${reviewed} / ${linked.length} linked cards reviewed</p>`;
}
function studyAppIsBrandingImage(node,src){
 const label=[src,node.getAttribute('alt')||'',node.getAttribute('title')||''].join(' ');
 return /anking[^/\\]*logo|logo[^/\\]*anking|anki[^/\\]*logo|logo[^/\\]*anki/i.test(label);
}
function studyAppLiveHTML(html,noteId,liveMedia={}){
 const template=document.createElement('template');template.innerHTML=String(html||'');
 template.content.querySelectorAll('script,iframe,object,embed,style,link,base,meta,form').forEach(node=>node.remove());
 const media=new Map((ankiNotes.get(Number(noteId))?.images||[]).map(image=>[image.file,image.src]));for(const [file,src]of Object.entries(liveMedia||{}))if(typeof src==='string'&&/^data:image\/(?:png|jpeg|gif|webp|bmp|svg\+xml);base64,/i.test(src))media.set(file,src);
 for(const node of template.content.querySelectorAll('*')){
  for(const attribute of [...node.attributes])if(/^on/i.test(attribute.name)||['srcdoc','formaction','srcset'].includes(attribute.name)||(attribute.name==='style'&&/url\s*\(|@import|expression\s*\(/i.test(attribute.value)))node.removeAttribute(attribute.name);
  for(const key of ['href','src']){const value=node.getAttribute(key);if(!value)continue;
   if(key==='src'){const name=value.split('/').pop();let decoded=name;try{decoded=decodeURIComponent(name)}catch{}const local=media.get(decoded);if(local)node.setAttribute('src',local);else if(!/^(?:data:image\/|blob:)/i.test(value)){if(studyAppIsBrandingImage(node,value)){node.remove();break}node.removeAttribute('src');node.setAttribute('alt','Image available in Anki');node.dataset.appMediaMissing='true'}}
   else if(!/^https?:\/\//i.test(value)&&!value.startsWith('#'))node.removeAttribute('href');else{node.setAttribute('target','_blank');node.setAttribute('rel','noopener')}
  }
 }return template.innerHTML;
}
function studyAppReviewMarkup(){
 if(!studyAppConfig||!ankiCurrentCard)return '';
 const session=studyAppAnkiSession&&Number(studyAppAnkiSession.card?.cardId)===Number(ankiCurrentCard)?studyAppAnkiSession:null;
 const record=studyAppCoverage.cards?.[String(ankiCurrentCard)],count=Number(record?.review_count||0);
 if(!session)return `<div class="app-anki-review" data-app-review-card="${ankiCurrentCard}"><button data-app-begin-review ${studyAppReviewPending?'disabled':''}>${studyAppReviewPending?'Connecting…':'Review in Anki'}</button><small>${count?count+' Anki reviews':'No synced review yet'}</small>${studyAppReviewMessage?`<p role="status">${esc(studyAppReviewMessage)}</p>`:''}${studyAppTopicCoverage()}</div>`;
 const card=session.card,labels=['Again','Hard','Good','Easy'],next=card.nextReviews||[];
 return `<div class="app-anki-review is-live" data-app-review-card="${ankiCurrentCard}"><b>Live Anki review</b><small>${esc(card.deckName||'')}${card.isNew?' · New card':card.isDue===false?' · Before due date':''}</small><p class="app-help">${session.missingMedia?'This card has an image unavailable here. Review it in Anki until the local image library is refreshed.':ankiCardRevealed?'Choose a rating to record this answer in Anki.':'Reveal the answer, then rate it. Anki chooses the next review date.'}</p>${ankiCardRevealed?`<div class="app-anki-ratings" role="group" aria-label="Record review in Anki">${labels.map((label,index)=>`<button data-app-rate="${index+1}" ${studyAppReviewPending||session.missingMedia?'disabled':''}>${label}${next[index]?`<small>${esc(next[index])}</small>`:''}</button>`).join('')}</div>`:''}<button data-app-cancel-review ${studyAppReviewPending?'disabled':''}>Return to practice</button>${studyAppReviewMessage?`<p role="status">${esc(studyAppReviewMessage)}</p>`:''}</div>`;
}
function studyAppRefreshReviewControls(){
 if(!studyAppConfig||!$('#side-content .anki-card-navigation'))return;
 let target=$('#side-content .app-anki-review');
 const session=studyAppAnkiSession;if(session&&Number(session.card?.cardId)===Number(ankiCurrentCard)){
  const content=$('#side-content .anki-card-content');if(content){
   content.replaceChildren();const shadow=content.shadowRoot||content.attachShadow({mode:'open'});
   const css=String(session.card.css||'').replace(/@import[^;]*;?/gi,'').replace(/url\s*\([^)]*\)/gi,'none').replace(/expression\s*\([^)]*\)/gi,'').replace(/<\/?style/gi,'');
   shadow.innerHTML=`<style>:host{display:block}.card{font:18px/1.5 system-ui;color:#193345;text-align:left}${css}</style><div class="card">${studyAppLiveHTML(ankiCardRevealed?session.card.answer:session.card.question,session.card.noteId,session.card.media)}</div>`;
   session.missingMedia=!!shadow.querySelector('[data-app-media-missing]');
   shadow.querySelectorAll('img[src]').forEach(img=>img.addEventListener('error',()=>{if(studyAppAnkiSession!==session)return;session.missingMedia=true;const controls=$('#side-content .app-anki-review');if(controls)controls.outerHTML=studyAppReviewMarkup()},{once:true}));
  }
 }
 const markup=studyAppReviewMarkup();if(target)target.outerHTML=markup;else $('#side-content .anki-card-navigation').insertAdjacentHTML('afterend',markup);
}
async function studyAppBeginReview(){
 const cardId=Number(ankiCurrentCard);studyAppReviewPending=true;studyAppReviewMessage='';studyAppRefreshReviewControls();
 try{const session=await studyAppRequest('/anki/begin',{cardId});if(cardId!==Number(ankiCurrentCard))return;studyAppAnkiSession=session;ankiCardRevealed=false;renderAnkiReference({keepScroll:true})}
 catch(error){studyAppReviewMessage=error.message}finally{studyAppReviewPending=false;studyAppRefreshReviewControls()}
}
async function studyAppRate(ease){
 const session=studyAppAnkiSession;if(studyAppReviewPending||!session||session.missingMedia||!ankiCardRevealed||Number(session.card?.cardId)!==Number(ankiCurrentCard))return;
 studyAppReviewPending=true;studyAppReviewMessage='Recording in Anki…';studyAppRefreshReviewControls();
 session.requestId||=crypto.randomUUID();const cardId=Number(ankiCurrentCard);
 try{const result=await studyAppRequest('/anki/review',{token:session.token,ease,request_id:session.requestId});
  if(!result.confirmed){studyAppReviewMessage=result.detail||'Anki has not confirmed this review. Check Anki before trying again.';return}
  studyAppAnkiSession=null;studyAppReviewMessage='Saved in Anki.';
  const old=studyAppCoverage.cards[String(cardId)]||{};studyAppCoverage.cards[String(cardId)]={...old,reviewed:true,review_count:Number(old.review_count||0)+1,last_review:result.review||{id:result.reviewId,ease}};
  studyAppReviewPending=false;if(cardId===Number(ankiCurrentCard))ankiStepCard(1);
 }catch(error){studyAppReviewMessage=error.message+' This rating was not confirmed; it will not be sent again automatically.'}
 finally{studyAppReviewPending=false;studyAppRefreshReviewControls()}
}
async function startStudyApp(){
 if(!studyAppConfig)return;
 document.body.classList.add('study-app-loading');studyAppStatus('Loading saved progress…');
 const local=studyAppCopy(state);let result;
 try{
  result=await studyAppRequest('/progress');
  if(result.exists){state={...state,...result.state};studyAppBaseline=studyAppCopy(result.state)}
  else{studyAppBaseline={};if(Object.values(local.notes||{}).some(Boolean)||Object.values(local.good||{}).some(Boolean))result=await studyAppRequest('/progress/import',{state:local});else studyAppProgressNotice()}
  studyAppRevision=result.revision||0;studyAppReady=true;
  localStorage.setItem(KEY,JSON.stringify(state));referenceStateSnapshot=studyAppCopy(state);
  currentView=location.hash.slice(1)||state.view||'guide';if(!['guide','objectives','questions','pathology','drugs','bugs'].includes(currentView))currentView='guide';ensureScopePage();render();save();
 }catch{render();studyAppStatus('App service unavailable · progress stays in this window')}
 finally{document.body.classList.remove('study-app-loading')}
 const button=document.createElement('button');button.id='study-app-button';button.textContent='App · Anki';button.onclick=async()=>{studyAppDialog().showModal();await studyAppLoadAnki()};document.querySelector('.toolbar').insertBefore(button,$('#export'));
 const originalRenderAnki=renderAnkiReference;renderAnkiReference=function(options){const result=originalRenderAnki(options);studyAppRefreshReviewControls();return result};
 const originalStep=ankiStepCard;ankiStepCard=function(delta){if(studyAppReviewPending)return;studyAppAnkiSession=null;studyAppReviewMessage='';return originalStep(delta)};
 const importControl=$('#import');importControl.onchange=async event=>{try{const value=JSON.parse(await event.target.files[0].text());if(value.format!==KEY||!referenceObject(value.state))throw Error('wrong format');const imported=await studyAppRequest('/progress/import',{state:value.state});state={...state,...imported.state};studyAppBaseline=studyAppCopy(imported.state);studyAppRevision=imported.revision;referenceStateSnapshot=studyAppCopy(state);ensureScopePage();studyAppBrowserSave();render();studyAppStatus('Progress imported · saved to app');$('#study-app-import-notice')?.remove()}catch{studyAppStatus('Could not import this progress file')}event.target.value=''};
 document.addEventListener('click',event=>{
  if(event.target.closest('[data-app-close]'))studyAppDialog().close();
  if(event.target.closest('[data-app-dismiss-import]'))$('#study-app-import-notice')?.remove();
  if(event.target.closest('[data-app-anki-sync]'))studyAppSync();
  if(event.target.closest('[data-app-begin-review]'))studyAppBeginReview();
  if(event.target.closest('[data-app-cancel-review]')){studyAppAnkiSession=null;studyAppReviewMessage='';renderAnkiReference({keepScroll:true})}
  const rating=event.target.closest('[data-app-rate]');if(rating)studyAppRate(Number(rating.dataset.appRate));
 });
 window.addEventListener('pagehide',()=>{if(studyAppReady&&studyAppDiff(studyAppBaseline,state)!==undefined){const patch=studyAppDiff(studyAppBaseline,state);fetch(studyAppConfig.apiBase+'/progress',{method:'POST',headers:{'Content-Type':'application/json','X-LLU-App-Token':studyAppConfig.token},body:JSON.stringify({patch,base_revision:studyAppRevision}),keepalive:true}).catch(()=>{})}});
 setInterval(studyAppRefresh,12000);await studyAppLoadAnki();
 if(studyAppConnection?.available&&!studyAppCoverage.summary?.synced_at)studyAppSync();
}
