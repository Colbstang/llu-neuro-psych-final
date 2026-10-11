/* Explicit local Anki reviews with resumable, confirmed private progress. */
(function(scope){
  'use strict';
  function safeMedia(value){return typeof value==='string'&&/^data:image\/(?:png|jpeg|gif|webp|bmp|svg\+xml);base64,[A-Za-z0-9+/=]+$/.test(value);}
  function editable(target){return !!target?.closest?.('input,textarea,select,button,a,[contenteditable]');}
  function element(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
  async function mount(container,options={}){
    let session=null,card=null,busy=false,error='',destroyed=false,doneId=null;
    const delivered=new Set(),root=element('section',undefined,'anki-review');container.append(root);
    root.setAttribute('aria-label','Live Anki review session');
    function report(value){error=value?.message||String(value);options.onError?.(error);}
    function button(label,callback,cls='small'){
      const node=element('button',label,cls);node.type='button';node.disabled=busy;
      node.onclick=async()=>{if(busy||destroyed)return;try{await callback();}catch(value){report(value);render();}};return node;
    }
    function side(value){
      const body=element('div',undefined,'anki-review-side');body.append(element('p',value?.text||'This card has no readable text. Check its local images.'));
      for(const image of value?.images||[]){if(!safeMedia(image?.src))continue;const node=element('img');node.src=image.src;node.alt=image.alt||'Image from your local Anki card';node.loading='lazy';body.append(node);}
      return body;
    }
    async function api(body){const result=await options.api('/api/anki-session',body,120000);if(result?.ok===false)throw new Error(result.reason||'Anki could not complete this action');return result;}
    function update(result){if(result.session!==undefined)session=result.session;if(result.card!==undefined)card=result.card;}
    async function open(){const result=await api({topic_id:options.topicId,session_id:session.id,action:'open'});if(!destroyed)update(result);}
    async function run(callback){if(busy||destroyed)return;busy=true;error='';render();try{await callback();}catch(value){report(value);}finally{busy=false;if(!destroyed)render();}}
    async function start(limit){await run(async()=>{update(await api({topic_id:options.topicId,action:'start',limit}));if(session?.status==='active')await open();});}
    async function reveal(){if(!card||session?.current?.revealed)return;await run(async()=>update(await api({topic_id:options.topicId,session_id:session.id,action:'reveal'})));}
    async function rate(ease){
      if(!card||!session?.current?.revealed)return;
      const current=session.current;
      if(current.request_id&&current.ease!==ease)return;
      current.request_id=current.request_id||scope.crypto.randomUUID();current.ease=ease;
      await run(async()=>{
        const result=await api({topic_id:options.topicId,session_id:session.id,action:'rate',ease,request_id:current.request_id});
        update(result);
        if(result.review?.confirmed){
          if(!delivered.has(result.review.request_id)){delivered.add(result.review.request_id);await options.onConfirmed?.({...result.review,session_id:session.id,topic_id:options.topicId});}
          card=null;if(session.status==='active')await open();
        }else{error='Anki has not confirmed this rating ('+(result.review?.status||'unknown')+'). Checking again reads the same request and does not repeat the rating.';}
      });
    }
    async function skip(){if(session?.current?.request_id)return;await run(async()=>{update(await api({topic_id:options.topicId,session_id:session.id,action:'skip'}));card=null;if(session.status==='active')await open();});}
    async function dismiss(){
      if(!session?.current?.request_id)return;
      const requestId=session.current.request_id;
      await run(async()=>{
        const result=await api({topic_id:options.topicId,session_id:session.id,action:'dismiss',request_id:requestId});update(result);
        if(result.review?.confirmed&&!delivered.has(result.review.request_id)){delivered.add(result.review.request_id);await options.onConfirmed?.({...result.review,session_id:session.id,topic_id:options.topicId});}
        card=null;if(session.status==='active')await open();
      });
    }
    function render(){
      if(destroyed||root.isConnected===false)return;root.replaceChildren();
      if(error){const alert=element('p',error,'anki-review-error');alert.setAttribute('role','alert');root.append(alert);}
      if(!session){
        root.append(element('h2','Anki in your study session'),element('p','Choose a small batch from this subject’s live Step / AnKing due or new cards. Each explicit rating is recorded by Anki.'));
        const row=element('div',undefined,'anki-review-actions'),select=element('select');select.setAttribute('aria-label','Anki batch size');
        for(const n of [5,10,20,50]){const option=element('option',n+' cards');option.value=String(n);select.append(option);}select.value='10';select.disabled=busy;
        row.append(select,button(busy?'Reading Anki…':'Start Anki batch',()=>start(Number(select.value)),'primary'));root.append(row);return;
      }
      const progress=element('p',`${session.confirmed_count} confirmed in Anki · ${session.skipped_count} skipped${session.unknown_count?' · '+session.unknown_count+' unconfirmed':''} · ${session.total} in this batch`,'anki-review-progress');progress.setAttribute('role','status');progress.setAttribute('aria-live','polite');root.append(progress);
      if(session.partial)root.append(element('p',session.reason||'A bounded portion of this subject’s queue is available.','muted'));
      if(session.status==='finished'){
        root.append(element('h2',session.total?'Anki batch finished':'No eligible cards reported'),element('p',session.total?'Only ratings confirmed in Anki count as reviews.':'Open Anki with AnkiConnect and check this subject’s unsuspended Step / AnKing due or new cards.'),button('Start another batch',()=>start(10),'primary'));
        if(doneId!==session.id){doneId=session.id;options.onDone?.(session);}return;
      }
      root.append(element('h2',`Card ${session.position+1} of ${session.total}`));
      if(!card){
        const pending=!!session.current?.request_id;
        root.append(element('p',busy?'Opening the live card…':pending?'This card could not be opened and its previous rating is still unconfirmed. You can retry opening it or leave the outcome unconfirmed without repeating or counting a rating.':'This card could not be opened. You can retry or skip it without a rating.','muted'));
        if(!busy)root.append(button('Retry opening card',()=>run(open)),pending?button('Leave unconfirmed and continue',dismiss):button('Skip without rating',skip));
        return;
      }
      const body=side(session.current?.revealed?card.back:card.front);root.append(body);options.onCard?.(body);
      const actions=element('div',undefined,'anki-review-actions');
      if(!session.current?.revealed)actions.append(button('Show answer · Space',reveal,'primary'));
      else if(session.current.request_id){actions.append(button(busy?'Checking Anki…':'Check this rating again',()=>rate(session.current.ease),'primary'));if(!busy)actions.append(button('Leave unconfirmed and continue',dismiss));}
      else for(const [ease,label] of [[1,'Again'],[2,'Hard'],[3,'Good'],[4,'Easy']])actions.append(button(`${label} · ${ease}`,()=>rate(ease),'anki-rating anki-rating-'+ease));
      const skipButton=button('Skip without rating',skip);skipButton.disabled=busy||!!session.current?.request_id;actions.append(skipButton);root.append(actions);
      root.append(element('p',session.current?.request_id?'Checking again preserves the same request. Leaving it unconfirmed saves an unknown outcome and continues without counting a review or repeating the rating.':'Space reveals the answer. 1–4 chooses Again, Hard, Good, or Easy after reveal. Skipping does not rate the card.','muted'));
    }
    async function keydown(event){
      if(destroyed||busy||root.isConnected===false||event.ctrlKey||event.metaKey||event.altKey||editable(event.target))return;
      if((event.code==='Space'||event.key===' ')&&card&&!session?.current?.revealed){event.preventDefault();await reveal();}
      else if(/^[1-4]$/.test(event.key)&&card&&session?.current?.revealed&&!session.current.request_id){event.preventDefault();await rate(Number(event.key));}
    }
    document.addEventListener('keydown',keydown);render();
    await run(async()=>{const result=await options.api('/api/anki-session?'+new URLSearchParams({topic_id:options.topicId}));if(result?.ok===false)throw new Error(result.reason||'Anki session unavailable');update(result);if(session?.status==='active')await open();});
    return {root,get session(){return session;},destroy(){destroyed=true;document.removeEventListener('keydown',keydown);root.remove();}};
  }
  scope.StepAnkiReview={mount,safeMedia,editable};
  if(typeof module!=='undefined'&&module.exports)module.exports={safeMedia,editable};
})(typeof window!=='undefined'?window:globalThis);
