/* Guided recall uses only source-bound server prompts and explicit outcomes. */
(function(scope){
  'use strict';
  function countSession(session){const tasks=session?.tasks||[];return {total:tasks.length,complete:tasks.filter(t=>t.status==='complete').length,skipped:tasks.filter(t=>t.status==='skipped').length};}
  function canGrade(revealed,available){return !revealed && available===true;}
  const exported={countSession,canGrade};
  if(typeof module!=='undefined')module.exports=exported;
  if(typeof document==='undefined')return;
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  function action(label,fn,cls){const b=node('button',label,cls);b.type='button';b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){b.closest('.learning-flow')?.querySelector('[role=status]')?.replaceChildren(document.createTextNode(e.message));}finally{if(b.isConnected&&!b.dataset.locked)b.disabled=false;}};return b;}
  function intro(host,title,text){host.classList.add('learning-flow');host.append(node('h1',title),node('p',text,'learning-intro'));const status=node('p','', 'learning-status');status.setAttribute('role','status');host.append(status);return status;}
  function safeSources(host,sources,onRead){const row=node('div',undefined,'learning-sources');for(const source of sources||[]){const url=source.url||source.source_url;if(typeof url==='string' && (url.startsWith('/api/course/document/')||url.startsWith('https://mdwiki.org/'))){const page=url.match(/#page=(\d+)/)?.[1];const a=node('a',page?'Course source · PDF '+page:'Linked course source');a.href=url;a.target='_blank';a.rel='noopener';row.append(a);}else row.append(node('span',source.title||source.name||'Linked course explanation'));}if(onRead)row.append(action('Back to chapter',onRead,'small'));host.append(row);}
  async function mountConcept(host,opts){
    const status=intro(host,opts.lab?'Predict and trace: eye movements':'Recall from memory',opts.lab?'Make a prediction and sketch the command pathway. Compare your reasoning with the course explanation, then test it in your lab.':'Answer before revealing the explanation. Each objective keeps its own review history.');
    const result=await opts.api('/api/concept?'+new URLSearchParams({item_id:opts.itemId,topic_id:opts.topicId||''}));if(!host.isConnected)return;
    const item=result.recall;let revealed=false,submitted=false;
    host.append(node('p',item.chapter_title||'','learning-context'),node('h2',item.prompt));
    safeSources(host,item.sources,opts.onRead?()=>opts.onRead(item.chapter_id):null);
    const label=node('label','Your explanation');const answer=node('textarea');answer.rows=6;answer.maxLength=2000;answer.value=opts.draft||'';answer.setAttribute('aria-label','Your concept answer');label.append(answer);host.append(label);
    let draftTimer;answer.oninput=()=>{clearTimeout(draftTimer);if(opts.onDraft)draftTimer=setTimeout(()=>opts.onDraft(answer.value).catch(e=>status.textContent=e.message),350);};
    opts.onController?.({flushDraft:async()=>{clearTimeout(draftTimer);if(opts.onDraft&&!submitted)await opts.onDraft(answer.value);}});
    const sketch=node('details',undefined,'learning-sketch');sketch.append(node('summary','Sketch the pathway from memory'));const canvas=node('canvas');canvas.width=850;canvas.height=300;canvas.setAttribute('aria-label','Optional drawing pad; describe your drawing in the answer for keyboard use');const ctx=canvas.getContext('2d');ctx.lineWidth=3;ctx.strokeStyle='#1f6754';let drawing=false;
    function point(e){const r=canvas.getBoundingClientRect();return [(e.clientX-r.left)*canvas.width/r.width,(e.clientY-r.top)*canvas.height/r.height];}
    canvas.onpointerdown=e=>{drawing=true;canvas.setPointerCapture(e.pointerId);ctx.beginPath();ctx.moveTo(...point(e));};canvas.onpointermove=e=>{if(drawing){ctx.lineTo(...point(e));ctx.stroke();}};canvas.onpointerup=()=>drawing=false;canvas.onpointercancel=()=>drawing=false;
    sketch.append(node('p','Optional scratch drawing. Written answers are saved; the drawing pad clears when you leave.','muted'),canvas,action('Clear drawing',()=>ctx.clearRect(0,0,canvas.width,canvas.height),'small'));host.append(sketch);
    const controls=node('div',undefined,'learning-actions'),feedback=node('div',undefined,'learning-feedback');
    const grade=action('Grade answer',()=>submit('grade'),'primary');grade.disabled=!canGrade(revealed,opts.aiAvailable);
    const save=action('Save without grading',()=>submit('save'));
    const reveal=action('Reveal course explanation',async()=>{if(!answer.value.trim()){status.textContent='Write your explanation before checking the source.';return;}const result=await opts.api('/api/concept-reveal',{session_id:item.session_id,answer:answer.value});revealed=true;answer.disabled=true;grade.disabled=true;grade.dataset.locked='true';reveal.dataset.locked='true';feedback.replaceChildren(node('h3','Compare with the course explanation'),node('p',result.reference_context,'source-explanation'));const ratings=node('div',undefined,'learning-actions');for(const [value,text]of[['again','Needs practice'],['hard','Recalled with effort'],['remembered','Recalled']])ratings.append(action(text,()=>submit('self_rating',value),'small'));feedback.append(node('p','Choose how well your answer matched. These are your own recall ratings.','muted'),ratings);reveal.disabled=true;});
    controls.append(grade,save,reveal);host.append(controls,node('p',opts.aiAvailable?'Grade sends only this answer and its bounded course excerpt to your signed-in Codex account.':'AI grading is unavailable. Reveal the course explanation and record your own recall rating.','learning-note'),feedback);
    if(opts.lab){const lab=node('div',undefined,'learning-lab');lab.append(node('h3','Test your prediction in the lab'),node('p','Choose a lesion or change a control, predict the result before applying it, then explain what differed from your prediction.'));const labs=await opts.api('/api/labs');const matches=labs.labs.filter(l=>/gaze/i.test(l.title));for(const l of matches)lab.append(action('Open '+l.title,()=>opts.api('/api/labs/open',{id:l.id}),'small'));if(!matches.length)lab.append(node('p','Register your Horizontal Gaze Lab in Interactive labs to open it here.','muted'));host.append(lab);}
    async function submit(kind,rating){
      if(submitted)return;
      if(!answer.value.trim()){status.textContent='Write your explanation first.';return;}
      grade.disabled=save.disabled=reveal.disabled=true;answer.disabled=true;clearTimeout(draftTimer);status.textContent=kind==='grade'?'Assessing against the linked course excerpt…':'Saving this objective…';
      try{const result=await opts.api('/api/concept-answer',{session_id:item.session_id,answer:answer.value,action:kind,self_rating:rating});submitted=true;grade.dataset.locked=save.dataset.locked=reveal.dataset.locked='true';
        feedback.replaceChildren(node('h3',result.grade?.assessed?Math.round(result.grade.score*100)+'% · assessed answer':kind==='self_rating'?'Recall rating saved':'Saved · unassessed'),node('p',result.grade?.feedback||'This objective’s history is saved on this Mac.'));for(const miss of result.grade?.missed_concepts||[])feedback.append(node('p',miss));
        if(result.reference_context){const details=node('details');details.append(node('summary','Review the source explanation'),node('p',result.reference_context,'source-explanation'));feedback.append(details);}
        status.textContent=result.attempt?.schedule?.due_at?'Review scheduled for this objective.':'Saved without a recall score or new review date.';
        if(opts.onDone)feedback.append(action('Continue',()=>opts.onDone(result),'primary'));else if(opts.onReturn)feedback.append(action('Choose another concept',opts.onReturn,'primary'));
      }catch(e){answer.disabled=false;grade.disabled=!canGrade(revealed,opts.aiAvailable);save.disabled=false;reveal.disabled=revealed;status.textContent=e.message;}
    }
  }
  async function mountCatalog(host,opts){
    const status=intro(host,'Your concept recall','Practice a small group from one chapter. Reveal and rate each objective independently.');
    const filter=node('div',undefined,'learning-actions'),subjects=node('select');subjects.setAttribute('aria-label','Recall subject');for(const t of opts.topics){const o=node('option',t.label);o.value=t.id;subjects.append(o);}subjects.value=opts.topicId;const mode=node('select');mode.setAttribute('aria-label','Concept practice filter');for(const [v,t]of[['all','Due and new'],['due','Due only']]){const o=node('option',t);o.value=v;mode.append(o);}filter.append(subjects,mode);host.append(filter);const list=node('div',undefined,'learning-list');host.append(list);
    async function load(){status.textContent='Loading objectives…';const result=await opts.api('/api/concepts?'+new URLSearchParams({topic_id:subjects.value,chapter_id:opts.chapterId||'',limit:'100'}));if(!host.isConnected)return;status.textContent=`${result.due_count} due · ${result.unseen_count} new objectives`;list.replaceChildren();const rows=result.items.filter(r=>mode.value!=='due'||r.state?.status==='due'||r.status==='due');const groups=new Map();for(const item of rows){if(!groups.has(item.chapter_id))groups.set(item.chapter_id,[]);groups.get(item.chapter_id).push(item);}for(const items of groups.values()){const section=node('section');section.append(node('h2',items[0].chapter_title||'Course concepts'));for(const item of items.slice(0,3)){const row=node('div',undefined,'learning-row');row.append(node('p',item.title||item.prompt),action(item.state?.status==='due'||item.status==='due'?'Review objective':'Recall objective',()=>opts.onChoose(item),'small'));section.append(row);}list.append(section);}if(!rows.length)list.append(node('p','No objectives match this view. Read a chapter or choose another subject.','muted'));}
    subjects.onchange=()=>load().catch(e=>status.textContent=e.message);mode.onchange=()=>load().catch(e=>status.textContent=e.message);await load();
  }
  scope.StepLearning={...exported,mountConcept,mountCatalog,intro};
})(typeof window!=='undefined'?window:this);
