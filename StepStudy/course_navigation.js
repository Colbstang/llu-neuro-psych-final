/* Reference changes participate in the containing app's browser history.
   Keep bounded snapshots in memory; saved learning state remains independent. */
let stepCourseNavRestoring=false,stepCourseNavAt=0,stepCourseNavCurrent=null;
const stepCourseNavSnapshots=new Map();
function stepCourseNavCapture(){
  const pane=$('#side-panel'),content=$('#side-content');
  const snap={view:currentView,scope:state.studyScope,page:state.page,chapterId:stepCourseActiveChapter(),scrollY:window.scrollY,referenceScroll:content.scrollTop,open:!pane.hidden,
    context:ankiContext?referenceClone(ankiContext):null,mode:ankiReferenceMode,activeRef,referenceBook,rapidIndex,rapidRevealed,cardId:ankiCurrentCard,cardRevealed:ankiCardRevealed,bookPanelActive,bookMarkMode,sideZoom,mediaAt:state.ankiMediaPosition[ankiContext?.key]||0};
  if(snap.open&&sourceViewerActive){
    const source=sourceViewerActive;snap.source={id:source.document.id,page:Number(source.result?.page||source.linkedPage||1),query:source.query||'',linkedPage:source.linkedPage};
  }else if(snap.open){
    snap.title=$('#side-title').textContent;
    snap.html=content.innerHTML;
  }
  snap.families=state.referenceSearchFamilies.slice();
  snap.key=JSON.stringify([snap.view,snap.scope,snap.chapterId,snap.open,snap.source||null,snap.context?.key||'',snap.context?.searchQuery||'',snap.families,snap.mode,snap.mediaAt,snap.cardId,snap.cardRevealed,snap.title||'',snap.rapidIndex,snap.rapidRevealed]);
  return snap;
}
function stepCourseNavRemember(replace=false){
  if(stepCourseNavRestoring||!stepCourseStarted||referenceOnly)return;
  const snap=stepCourseNavCapture();
  if(snap.open&&(/Looking up|Loading source|Loading PDF|Opening the exact|Searching .*…/.test($('#side-content').textContent)||snap.context?.retrieval==='loading'))return;
  const same=stepCourseNavCurrent?.key===snap.key;
  const id=same?stepCourseNavCurrent.id:String(++stepCourseNavAt);
  stepCourseNavSnapshots.set(id,snap);stepCourseNavCurrent={id,key:snap.key};
  while(stepCourseNavSnapshots.size>100)stepCourseNavSnapshots.delete(stepCourseNavSnapshots.keys().next().value);
  stepCoursePost('navigation',{navigation:{id,key:snap.key,view:snap.view,chapterId:snap.chapterId,scrollY:snap.scrollY,scope:snap.scope},replace:replace||same});
}
async function stepCourseNavRestore(id){
  const snap=stepCourseNavSnapshots.get(id);if(!snap)return false;
  stepCourseNavRestoring=true;stepCourseDefinitionSerial++;sourceViewerSerial++;semanticRequestSerial++;semanticAbort?.abort();
  try{
    const oldScope=state.studyScope;state.studyScope=snap.scope;state.page=snap.page;state.referenceSearchFamilies=snap.families.slice();
    ankiContext=referenceClone(snap.context);activeRef=snap.activeRef;ankiReferenceMode=snap.mode;referenceBook=snap.referenceBook;rapidIndex=snap.rapidIndex;rapidRevealed=snap.rapidRevealed;sourceViewerActive=null;
    ankiCurrentCard=snap.cardId;ankiCardRevealed=snap.cardRevealed;sideZoom=snap.sideZoom;bookPanelActive=snap.bookPanelActive;if(ankiContext)state.ankiMediaPosition[ankiContext.key]=snap.mediaAt;
    if(currentView!==snap.view||oldScope!==snap.scope)navigateView(snap.view);
    if(!snap.open){$('#side-panel').hidden=true;document.body.classList.remove('with-panel');}
    else if(snap.source){const doc=(DATA.source_catalog?.documents||[]).find(d=>d.id===snap.source.id);if(doc)await openExactSource(doc,snap.source.page,snap.source.query,null,snap.source.linkedPage,true);}
    else if(snap.html!==undefined){showSidePanel(snap.title,snap.html,{keepScroll:false,keepZoom:true,bookPanel:snap.bookPanelActive});applySideZoom();setBookMarkMode(snap.bookMarkMode===true);}
    await new Promise(resolve=>requestAnimationFrame(resolve));
    $('#side-content').scrollTop=snap.referenceScroll;
    window.scrollTo({top:snap.scrollY,behavior:'instant'});
    stepCourseNavCurrent={id,key:snap.key};window.refreshReferenceSearchControls?.(ankiContext);
  }finally{stepCourseNavRestoring=false;}
  return true;
}
const stepCourseNavShow=showSidePanel;
showSidePanel=function(...args){const result=stepCourseNavShow(...args);queueMicrotask(()=>stepCourseNavRemember());return result;};
const stepCourseNavNavigate=navigateView;
navigateView=function(...args){const result=stepCourseNavNavigate(...args);queueMicrotask(()=>stepCourseNavRemember());return result;};
const stepCourseNavClose=$('#close-side').onclick;
$('#close-side').onclick=function(event){stepCourseNavClose?.(event);stepCourseNavRemember();};
let stepCourseNavScroll;
addEventListener('scroll',()=>{if(stepCourseNavScroll)return;stepCourseNavScroll=requestAnimationFrame(()=>{stepCourseNavScroll=null;stepCourseNavRemember(true);});},{passive:true});
$('#side-content').addEventListener('scroll',()=>{if(stepCourseNavCurrent){const snap=stepCourseNavSnapshots.get(stepCourseNavCurrent.id);if(snap)snap.referenceScroll=$('#side-content').scrollTop;}},{passive:true});
addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==window.parent||event.data?.app!=='step-course')return;
  if(event.data.type==='restore-navigation')stepCourseNavRestore(event.data.id).finally(()=>stepCoursePost('navigation-restored',{id:event.data.id}));
});
