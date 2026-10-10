/* Explicit capture, private question links, and isolated local labs. */
(function (scope) {
  'use strict';
  const hooks = {getContext:()=>({}), onCaptured:()=>{}, onError:()=>{}, onNavigate:()=>{}};
  let capturePending = false, chapterCatalog, chapterCounts, chapterCountsAt=0;
  const chapterHosts=new Set();
  const captureButtons = new Set();
  const el = (tag, text, cls) => {
    const node=document.createElement(tag);
    if(text!==undefined)node.textContent=text;
    if(cls)node.className=cls;
    return node;
  };
  async function request(path, body) {
    const options={signal:AbortSignal.timeout(body===undefined?20000:260000)};
    if(body!==undefined)Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json','X-Step-Token':document.querySelector('meta[name="step-token"]')?.content || scope.STUDY_APP_CONFIG?.token || ''},body:JSON.stringify(body)});
    const response=await fetch(path,options),result=await response.json();
    if(!response.ok || result.ok===false)throw new Error(result.error || 'The local action could not finish');
    return result;
  }
  function button(text, action, cls='small') {
    const node=el('button',text,cls);node.type='button';
    node.onclick=async()=>{try{await action();}catch(error){hooks.onError(error.message);}};
    return node;
  }
  function configure(options={}) {for(const key of Object.keys(hooks))if(typeof options[key]==='function')hooks[key]=options[key];}
  async function captureRegion() {
    if(capturePending)return;
    capturePending=true;
    for(const node of captureButtons){if(!node.isConnected){captureButtons.delete(node);continue;}node.disabled=true;node.textContent='Select region…';}
    try {
      const result=await request('/api/capture-region',{context:hooks.getContext() || {}});
      if(!result.cancelled){invalidateQuestionLinks();notifyQuestionLinks();await hooks.onCaptured(result);}
      return result;
    } catch(error) {hooks.onError(error.message);return {ok:false,error:error.message};}
    finally {capturePending=false;for(const node of captureButtons){node.disabled=false;node.textContent='Capture region';}}
  }
  function mountCaptureControls(host) {
    const node=button('Capture region',captureRegion,'small capture-region');
    node.title='⌘⇧2 · Choose a screen region; Escape cancels';
    node.disabled=capturePending;captureButtons.add(node);host.append(node);return node;
  }
  async function catalog() {
    if(!chapterCatalog)chapterCatalog=request('/api/intake/catalog').then(result=>result.chapters).catch(error=>{chapterCatalog=null;throw error;});
    return chapterCatalog;
  }
  function renderQuestionLinks(row, options={}) {
    const root=el('div',undefined,'question-course-links'),links=row.course_links || [];
    if(links.length){
      const summary=el('div',undefined,'question-linked-chapters');
      for(const item of links){
        const link=button(item.title,()=> (options.onNavigate || hooks.onNavigate)(item));
        link.title=item.confidence==='reviewed'?'Reviewed chapter link':`${item.confidence==='context'?'Captured while reading':'Keyword match'}${item.evidence?.length?': '+item.evidence.join(', '):''}`;
        summary.append(link);
      }
      root.append(summary);
    }
    const review=el('details');review.append(el('summary','Review links to course chapters'));
    const content=el('div',undefined,'chapter-link-editor');review.append(content);root.append(review);
    let loaded=false;
    review.addEventListener('toggle',async()=>{
      if(!review.open || loaded)return;
      content.replaceChildren(el('p','Loading chapter list…','muted'));
      try {
        const chapters=await catalog();loaded=true;content.replaceChildren();
        content.append(el('p','Choose every chapter this question belongs to. Keyword and reading-context links are suggestions you can change.','muted'));
        const selected=new Set(links.map(item=>item.chapter_id)), search=el('input');
        search.type='search';search.placeholder='Filter chapter names…';search.setAttribute('aria-label','Filter question chapter links');content.append(search);
        const list=el('div',undefined,'chapter-link-options'), unique=new Map(chapters.map(item=>[item.chapter_id,item]));
        for(const item of unique.values()){
          const label=el('label'),input=el('input');input.type='checkbox';input.checked=selected.has(item.chapter_id);
          input.onchange=()=>input.checked?selected.add(item.chapter_id):selected.delete(item.chapter_id);
          label.append(input,document.createTextNode(' '+item.title));label.dataset.search=(item.title+' '+item.topic_title).toLocaleLowerCase();list.append(label);
        }
        search.oninput=()=>{for(const label of list.children)label.hidden=!label.dataset.search.includes(search.value.trim().toLocaleLowerCase());};
        content.append(list);
        const status=el('p','', 'muted');status.setAttribute('role','status');
        const save=button('Save chapter links',async()=>{
          save.disabled=true;
          try {const result=await request('/api/question',{id:row.id,action:'course-links',chapter_ids:[...selected]});invalidateQuestionLinks();notifyQuestionLinks();status.textContent='Chapter links saved';if(options.onSaved)await options.onSaved(result.question);}
          finally {save.disabled=false;}
        });content.append(save,status);
        if(!chapters.length)status.textContent='Connect the course module to link its chapters.';
      } catch(error){content.replaceChildren(el('p',error.message,'alert-text'));}
    });
    return root;
  }
  function invalidateQuestionLinks() {
    chapterCounts=null;chapterCountsAt=0;
    for(const entry of chapterHosts){if(!entry.host.isConnected){chapterHosts.delete(entry);continue;}entry.host.querySelector('.chapter-saved-questions')?.remove();if(entry.visible)entry.load();}
  }
  function notifyQuestionLinks(){if(scope.parent && scope.parent!==scope)scope.parent.postMessage({app:'step-course',type:'question-links-changed'},location.origin);}
  async function linkCounts() {
    if(!chapterCounts || Date.now()-chapterCountsAt>30000){chapterCountsAt=Date.now();chapterCounts=request('/api/intake/chapter-links').then(result=>result.counts).catch(error=>{chapterCounts=null;throw error;});}
    return chapterCounts;
  }
  function mountChapterQuestions(host, chapterId) {
    if(!chapterId)return;
    for(const entry of chapterHosts)if(!entry.host.isConnected){entry.observer?.disconnect();chapterHosts.delete(entry);}
    const entry={host,visible:false,load};chapterHosts.add(entry);
    async function load(){
      if(host.querySelector('.chapter-saved-questions'))return;
      try {
        const count=(await linkCounts())[chapterId] || 0;
        if(!host.isConnected || !count || host.querySelector('.chapter-saved-questions'))return;
        const details=el('details',undefined,'chapter-saved-questions');
        details.append(el('summary',`${count} saved question${count===1?'':'s'} linked here`));host.append(details);
        let loaded=false;
        details.addEventListener('toggle',async()=>{
          if(!details.open || loaded)return;loaded=true;
          const content=el('div');content.append(el('p','Loading linked questions…','muted'));details.append(content);
          try{
            const result=await request('/api/questions?'+new URLSearchParams({chapter_id:chapterId}));content.replaceChildren();
            const rows=result.questions.filter(row=>['confirmed','needs_confirmation'].includes(row.status));
            for(const row of rows){const article=el('article');article.append(el('p',row.stem || row.full_text),button('Review in question inbox',()=>hooks.onNavigate({inbox:true,question_id:row.id,chapter_id:chapterId})));content.append(article);}
            if(count>rows.length)content.append(el('p',`Showing ${rows.length} of ${count}; review the question inbox for more.`,'muted'));
          }catch(error){content.replaceChildren(el('p',error.message,'alert-text'));loaded=false;}
        });
      }catch(_){/* An unavailable inbox must not block the reading surface. */}
    }
    if(typeof IntersectionObserver==='function'){
      const observer=new IntersectionObserver(entries=>{if(entries.some(item=>item.isIntersecting)){entry.visible=true;observer.disconnect();load();}}, {rootMargin:'150px'});entry.observer=observer;observer.observe(host);
    }else{entry.visible=true;load();}
  }
  async function mountLabs(host) {
    const root=el('section',undefined,'local-labs');host.append(root);
    root.append(el('p','INTERACTIVE STUDY','eyebrow'),el('h1','Local labs'),el('p','Keep an interactive HTML lab beside your studies, or open a native anatomy app. HTML labs run separately from your guide and saved progress.'));
    const list=el('div',undefined,'local-lab-list'),viewer=el('div',undefined,'local-lab-viewer');root.append(list,viewer);
    const add=el('details');add.append(el('summary','Add a local lab'));
    const form=el('form',undefined,'lab-add-form'),path=el('input'),name=el('input');
    path.placeholder='Path to a trusted HTML file or native .app';path.required=true;path.maxLength=2000;path.setAttribute('aria-label','Local lab file path');
    name.placeholder='Lab name (optional)';name.maxLength=120;name.setAttribute('aria-label','Local lab name');
    const status=el('p','HTML labs can use local HTML, JavaScript, images and model files in the same folder. Network access is blocked. A GLB needs a local HTML viewer.','muted');status.setAttribute('role','status');
    const submit=button('Add lab',async()=>{
      if(!path.value.trim()){status.textContent='Choose a local lab file first.';return;}
      submit.disabled=true;
      try{await request('/api/labs/register',{path:path.value.trim(),title:name.value.trim()});path.value='';name.value='';add.open=false;await renderList();}
      finally{submit.disabled=false;}
    });form.onsubmit=event=>{event.preventDefault();submit.click();};form.append(path,name,submit,status);add.append(form);root.append(add);
    async function renderList(){
      const result=await request('/api/labs');list.replaceChildren();
      if(!result.labs.length)list.append(el('p','No local labs connected yet. Add an existing HTML lab or the Horizontal Gaze Lab native app.','empty-box'));
      for(const lab of result.labs){
        const row=el('article',undefined,'local-lab-card');row.append(el('h2',lab.title),el('p',lab.available?(lab.kind==='native'?'Native app · opens in its own window':'HTML / JavaScript · isolated viewer'):'Unavailable · reconnect its drive or select the file again','muted'));
        const open=button(lab.kind==='native'?'Open lab app':'Open lab',async()=>{
          if(lab.kind==='native'){await request('/api/labs/open',{id:lab.id});return;}
          viewer.replaceChildren();const close=button('Close lab',()=>viewer.replaceChildren());
          const frame=el('iframe');frame.title=lab.title;frame.setAttribute('sandbox','allow-scripts');frame.setAttribute('referrerpolicy','no-referrer');frame.src=lab.url;
          viewer.append(close,frame);frame.addEventListener('load',()=>frame.focus(),{once:true});
        });open.disabled=!lab.available;
        row.append(open,button('Remove connection',async()=>{await request('/api/labs/remove',{id:lab.id});viewer.replaceChildren();await renderList();}));list.append(row);
      }
    }
    try{await renderList();}catch(error){list.replaceChildren(el('p',error.message,'alert-text'));}
    return root;
  }
  scope.StepWorkspaceTools={configure,captureRegion,mountCaptureControls,renderQuestionLinks,mountChapterQuestions,invalidateQuestionLinks,mountLabs};
})(typeof window!=='undefined'?window:globalThis);
