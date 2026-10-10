/* User-authored question repair and deliberate, independently answered retry. */
(function(scope){
  'use strict';
  let instances=0;
  const limits={reason_fooled_me:2000,missing_distinction:2000,correction:5000,source_reference:1000,follow_up_prompt:2000};
  const empty=()=>({confidence:'unknown',...Object.fromEntries(Object.keys(limits).map(key=>[key,''])),retry_count:0,next_retry_at:null,history:[]});
  function element(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
  function mount(container,options={}){
    let question=options.question,repair={...empty(),...question?.repair},draft={...repair},dirty=false;
    let mode='repair',loaded=false,loadPromise,busy=false,retry=null,destroyed=false;
    const prefix='question-repair-'+(++instances),root=element('details',undefined,'question-repair');
    const title=element('summary','Repair & retry this question'),content=element('div',undefined,'question-repair-content');root.append(title,content);container.append(root);
    const valid=Number.isInteger(question?.id)&&question.id>0&&typeof options.api==='function';
    if(!valid){content.append(element('p','Connect an existing saved question to use repair practice.','muted'));return {root,ready:Promise.resolve(),open:async()=>{},destroy:()=>root.remove()};}
    function report(error){const text=error?.message||String(error);const status=element('p',text,'repair-error');status.setAttribute('role','alert');content.append(status);options.onError?.(text);}
    function notice(){options.onModeChange?.({mode,sourceRevealed:mode!=='retry'||retry?.revealed===true});}
    function action(label,callback,cls='small'){
      const node=element('button',label,cls);node.type='button';node.onclick=async()=>{try{await callback();}catch(error){report(error);}};return node;
    }
    function update(result){
      if(result.question)question=result.question;
      repair={...empty(),...(result.repair||result.question?.repair||{})};draft={...repair};dirty=false;
    }
    async function load(){
      if(loaded)return;
      if(!loadPromise)loadPromise=options.api('/api/question-repair?'+new URLSearchParams({id:question.id})).then(result=>{update(result);loaded=true;}).finally(()=>loadPromise=null);
      await loadPromise;
    }
    async function save(){
      const patch=Object.fromEntries(['confidence',...Object.keys(limits)].map(key=>[key,draft[key]]));
      const result=await options.api('/api/question-repair',{id:question.id,action:'save',repair:patch});update(result);loaded=true;await options.onSaved?.(result);return result;
    }
    async function open(next='repair'){
      if(destroyed)return;root.open=true;content.replaceChildren(element('p','Loading saved repair…','muted'));
      const requested=['repair','retry','history'].includes(next)?next:'repair';
      options.onModeChange?.({mode:requested,sourceRevealed:requested!=='retry'});
      try{await load();if(requested==='retry'&&dirty)await save();mode=requested;
        if(mode==='retry')retry={answer:'',snapshot:'',revealed:false,saved:false,pendingResult:null,requestId:scope.crypto?.randomUUID?.()||'retry-'+Date.now()+'-'+Math.random().toString(36).slice(2)};
        render();
      }catch(error){if(loaded)render();else{content.replaceChildren(action('Retry loading repair',()=>open(requested)));notice();}report(error);}
    }
    function heading(){
      const nav=element('div',undefined,'repair-navigation');nav.setAttribute('aria-label','Question repair views');
      for(const [next,label] of [['repair','Repair notes'],['retry','Retry without source'],['history','Retry history']]){
        const button=action(label,async()=>{if(next===mode)return;await open(next);});button.setAttribute('aria-pressed',String(mode===next));button.disabled=busy;nav.append(button);
      }
      content.append(nav);
    }
    function field(form,key,label,placeholder){
      const name=element('label',label),input=element(key==='source_reference'?'input':'textarea');name.htmlFor=prefix+'-'+key;input.id=name.htmlFor;input.value=draft[key]||'';input.maxLength=limits[key];
      if(key!=='source_reference')input.rows=key==='correction'?4:2;input.placeholder=placeholder;input.oninput=()=>{draft[key]=input.value;dirty=true;};form.append(name,input);return input;
    }
    function source(){
      const box=element('div',undefined,'repair-source');box.append(element('h3','Original screenshot & OCR'));
      const image=element('img');image.loading='lazy';image.src='/api/question-image?id='+encodeURIComponent(question.id);image.alt='Original saved question screenshot';box.append(image);
      if(question.full_text||question.stem)box.append(element('pre',question.full_text||question.stem));else box.append(element('p','No readable OCR text is available. Review the original screenshot.','muted'));
      if(question.explanation)box.append(element('h3','Explanation from the screenshot'),element('p',question.explanation));return box;
    }
    function repairForm(){
      const form=element('form',undefined,'repair-form');form.onsubmit=event=>event.preventDefault();
      const confidenceLabel=element('label','Confidence when I answered'),confidence=element('select');confidenceLabel.htmlFor=prefix+'-confidence';confidence.id=confidenceLabel.htmlFor;
      for(const [value,label] of [['unknown','Not recorded'],['low','Low'],['medium','Medium'],['high','High — I felt sure']]){const item=element('option',label);item.value=value;confidence.append(item);}confidence.value=draft.confidence;confidence.onchange=()=>{draft.confidence=confidence.value;dirty=true;};form.append(confidenceLabel,confidence);
      field(form,'reason_fooled_me','What fooled me?','The clue I overread, missed, or confused…');
      field(form,'missing_distinction','The distinction I was missing','Describe the difference I need to recognize next time…');
      field(form,'correction','Correction I checked against a source','Write the corrected reasoning in my own words…');
      field(form,'source_reference','Source reference','Lecture or book title + page, or a source URL');
      field(form,'follow_up_prompt','Follow-up prompt','Ask a question that tests the distinction again…');
      form.append(element('p','Corrections are your source-checked notes. Add the reference you used; the app does not generate an answer.','muted'));
      const status=element('p','', 'repair-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
      const button=action('Save repair notes',async()=>{button.disabled=true;try{await save();status.textContent='Repair notes saved';}finally{button.disabled=false;}});form.append(button,status);content.append(form);
      const original=element('details',undefined,'repair-original');original.append(element('summary','Review original screenshot & explanation'));original.addEventListener('toggle',()=>{if(original.open&&original.children.length===1)original.append(source());});content.append(original);
    }
    async function rate(result){
      if(busy||retry.saved)return;retry.pendingResult=retry.pendingResult||result;busy=true;render();
      try{
        const response=await options.api('/api/question-repair',{id:question.id,action:'retry',answer:retry.snapshot,self_result:retry.pendingResult,request_id:retry.requestId});
        update(response);retry.saved=true;busy=false;render();await options.onSaved?.(response);
      }catch(error){busy=false;render();report(error);}
    }
    function retryView(){
      content.append(element('p',repair.follow_up_prompt||'Explain the key distinction that would help you answer this question.','repair-prompt'));
      if(!retry.saved&&Number.isFinite(repair.next_retry_at)&&repair.next_retry_at*1000>Date.now())content.append(element('p','Next scheduled retry: '+new Date(repair.next_retry_at*1000).toLocaleString()+'. You can practice early here.','muted'));
      if(!repair.correction)content.append(element('p','No source-checked correction has been saved yet. You can still attempt the prompt and compare with the original screenshot.','muted'));
      const label=element('label','Your independent retry answer'),answer=element('textarea');label.htmlFor=prefix+'-retry-answer';answer.id=label.htmlFor;answer.rows=5;answer.maxLength=10000;answer.value=retry.revealed?retry.snapshot:retry.answer;answer.readOnly=retry.revealed;answer.disabled=busy;answer.oninput=()=>{retry.answer=answer.value;reveal.disabled=!answer.value.trim();};content.append(label,answer);
      const reveal=action('Reveal source & correction',()=>{if(!retry.answer.trim())return;retry.snapshot=retry.answer.trim();retry.revealed=true;render();});reveal.disabled=!retry.answer.trim();
      if(!retry.revealed){content.append(reveal,element('p','Enter your answer first. The original screenshot, OCR, explanation, and repair notes appear only after you reveal them.','muted'));return;}
      const correction=element('div',undefined,'repair-correction');correction.append(element('h3','Saved correction'),element('p',repair.correction||'No correction saved.'));
      if(repair.source_reference)correction.append(element('p','Source: '+repair.source_reference,'repair-source-reference'));
      if(repair.missing_distinction)correction.append(element('h3','Missing distinction'),element('p',repair.missing_distinction));
      if(repair.reason_fooled_me)correction.append(element('h3','What fooled me'),element('p',repair.reason_fooled_me));content.append(correction,source());
      const actions=element('div',undefined,'repair-rating');
      if(!retry.saved){
        if(retry.pendingResult){const retrySave=action(busy?'Saving retry…':'Retry saving '+(retry.pendingResult==='recalled'?'Recalled':'Needs practice'),()=>rate(retry.pendingResult));retrySave.disabled=busy;actions.append(retrySave);}
        else for(const [value,text] of [['needs_practice','Needs practice'],['recalled','Recalled']]){const node=action(text,()=>rate(value));node.disabled=busy;actions.append(node);}
        actions.append(element('p','Compare your answer with the source, then choose your own result. Needs practice returns in 1 day; Recalled in 3 days.','muted'));
      }else{const status=element('p','Saved: '+(retry.pendingResult==='recalled'?'Recalled':'Needs practice')+' · your self-rating. Original outcome: '+question.outcome+'.','repair-status');status.setAttribute('role','status');actions.append(status);if(Number.isFinite(repair.next_retry_at))actions.append(element('p','Next scheduled retry: '+new Date(repair.next_retry_at*1000).toLocaleString(),'muted'));actions.append(action('Try another independent retry',()=>open('retry')));}
      content.append(actions);
    }
    function historyView(){
      const history=repair.history||[];
      if(!history.length){content.append(element('p','No independent retries saved yet. Try the prompt before revealing its source.','empty-box'));return;}
      if(repair.history_partial)content.append(element('p',`Showing ${history.length} recent retries of ${repair.retry_count}. Your private backup includes the full history.`,'muted'));
      for(const row of history){
        const entry=element('details',undefined,'repair-history-entry'),date=new Date(Number(row.attempted_at)*1000);
        entry.append(element('summary',(Number.isFinite(date.getTime())?date.toLocaleString():'Date unavailable')+' · '+(row.self_result==='recalled'?'Recalled':'Needs practice')));
        if(row.prompt)entry.append(element('p',row.prompt,'repair-prompt'));entry.append(element('p',row.answer));
        if(row.correction)entry.append(element('p','Correction at this retry: '+row.correction));if(row.source_reference)entry.append(element('small','Source: '+row.source_reference));content.append(entry);
      }
    }
    function render(){if(destroyed)return;content.replaceChildren();heading();if(mode==='repair')repairForm();else if(mode==='retry')retryView();else historyView();notice();}
    root.addEventListener('toggle',()=>{if(root.open){if(!loaded&&!loadPromise)open(options.initialMode||'repair');else if(loaded)notice();}else options.onModeChange?.({mode:'closed',sourceRevealed:true});});
    const ready=options.initialMode?open(options.initialMode):Promise.resolve();
    return {root,ready,open,refresh:async()=>{loaded=false;await load();render();},destroy:()=>{destroyed=true;root.remove();options.onModeChange?.({mode:'closed',sourceRevealed:true});}};
  }
  scope.StepRepair={mount};
})(typeof window!=='undefined'?window:globalThis);
