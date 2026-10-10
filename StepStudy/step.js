/* Step workspace. No browser flag or AI result writes an Anki review. */
(function (scope) {
  'use strict';
  function words(text) { return String(text || '').trim().split(/\s+/).filter(Boolean).length; }
  function latestAttempts(attempts) { const result = {}; for (const row of attempts || []) result[row.topic_id] = row; return result; }
  function evidence(topicId, data) {
    const last = latestAttempts((data.state?.attempts || []).filter(row=>row.grade?.assessed===true && typeof row.score==='number'))[topicId];
    const anki = data.anki?.subjects?.[topicId], questions = data.questions?.topics?.[topicId];
    return {recall:last?.grade?.assessed === true && typeof last.score === 'number' ? last.score : null,
      anki: anki || null, unresolved:questions ? questions.unresolved_count : 0, questions:questions || null,
      course:data.module?.available ? data.module.topics?.[topicId] || null : null};
  }
  function ankiDueSummary(signals) {
    const known = (signals || []).filter(row => Number.isInteger(row?.due_count) && row.due_count >= 0);
    return {count:known.reduce((sum,row)=>sum+row.due_count,0), known:known.length,
      partial:known.length !== signals.length || known.some(row=>row.scope_partial)};
  }
  function ankiReviewLabel(signal) {
    if(!signal)return 'Not connected';
    const reviews=signal.review_count_sampled ?? signal.review_count;
    if(Number.isInteger(reviews) && reviews>=0) {
      const ratings=`${signal.again_count_sampled ?? signal.again_count ?? 0} / ${signal.hard_count_sampled ?? signal.hard_count ?? 0}`;
      if(signal.review_scope==='sample')return ratings+' · '+signal.review_sample_size+'-card sample';
      return reviews>0?ratings:signal.review_history_known?'No reviews in 30d':'History unknown';
    }
    return 'History unknown';
  }
  function readingLength(records, learned = {}) {
    let visibleWords = 0, fullWords = 0, fullImages = 0, visibleImages = 0;
    for (const record of records || []) {
      const full = words(record.summary || record.definition), skim = words(record.definition);
      fullWords += full; visibleWords += learned[record.id] ? skim : full;
      if (record.image) { fullImages++; if (!learned[record.id]) visibleImages++; }
    }
    return {visibleWords, fullWords, visiblePages:visibleWords / 650 + visibleImages * .3, fullPages:fullWords / 650 + fullImages * .3};
  }
  const exported = {words, latestAttempts, evidence, readingLength, ankiDueSummary, ankiReviewLabel};
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  if (typeof document === 'undefined') return;

  const $ = id => document.getElementById(id);
  const token = document.querySelector('meta[name="step-token"]').content;
  const main = $('main-content'), panel = $('reference-content');
  const referenceOnly = new URLSearchParams(location.search).get('reference') === '1';
  let data, catalog = [], lexicon = [], scanner, selectionLookup;
  const workspaceViews = ['today','topics','read','objectives','course-questions','pathology','drugs','bugs','recall','cards','questions','settings'];
  const courseViews = {read:'guide',objectives:'objectives','course-questions':'questions',pathology:'pathology',drugs:'drugs',bugs:'bugs'};
  let view = workspaceViews.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'today';
  let topicId = 'neuro', readRecords = [], recall = null, answerDraft = '', questionFilter = '', questionTopic = '';
  let renderSerial = 0, definitionSerial = 0, hoverSerial = 0, hoverTimer, toastTimer, notesTimer;
  let definitions = new Map(), history = [], historyAt = -1, detachedAliveAt = 0, detachedWanted = false, lastDefinitionRequest;
  let cardSession = null, cardHost = null;
  let courseFrame = null, courseReady = false, courseInfo = null, courseRefreshTimer, coursePending = null;
  let channel;
  try { channel = new BroadcastChannel('step-study-reference-v1'); } catch (_) {}
  function el(tag, text, cls) { const node = document.createElement(tag); if (text !== undefined && text !== null) node.textContent = text; if (cls) node.className = cls; return node; }
  function button(label, action, cls) { const node = el('button', label, cls); node.type = 'button'; node.onclick = async event => { try { await action(event); } catch(error) { toast(error.message || 'The action could not finish'); } }; return node; }
  function link(label, url, cls) { const node = el('a', label, cls); node.href = url; node.target = '_blank'; node.rel = 'noopener noreferrer'; return node; }
  function safeLink(label, url, image = false) { const valid = scope.MedicalTermCards.safeLink(url, image); return valid ? link(label, valid) : el('span', label); }
  function toast(text) { $('toast').textContent = text; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 5000); }
  async function api(path, body, timeout = 120000) {
    const options = {signal:AbortSignal.timeout(timeout)};
    if (body !== undefined) Object.assign(options, {method:'POST', headers:{'Content-Type':'application/json','X-Step-Token':token}, body:JSON.stringify(body)});
    const response = await fetch(path, options), result = await response.json();
    if (!response.ok || (result.ok === false && !path.startsWith('/api/term?'))) throw new Error(result.error || result.reason || 'The local service could not respond');
    return result;
  }
  async function pref(values) { await api('/api/preferences', {preferences:values}); Object.assign(data.state.preferences, values); saved(); }
  function saved() { $('save-status').textContent = 'Saved on this Mac'; }
  function topic() { return catalog.find(row => row.id === topicId) || catalog[0]; }
  function activeTopics() { return catalog.filter(row => data.state.topics[row.id]?.active); }
  async function refresh() { data = await api('/api/workspace', undefined, 12000); catalog = data.catalog; renderSidebar(); saved(); }
  function pageIntro(eyebrow, title, description) { const node = el('div', undefined, 'page-intro'); node.append(el('p', eyebrow, 'eyebrow'), el('h1', title), el('p', description)); return node; }
  function sectionHeading(title, action) { const node = el('div', undefined, 'section-heading'); node.append(el('h2', title)); if (action) node.append(action); return node; }
  function renderSidebar() {
    document.querySelectorAll('[data-view]').forEach(node => node.classList.toggle('active', node.dataset.view === view));
    $('queue-badge').textContent = data.queue.length || '';
    $('question-badge').textContent = data.intake.counts.needs_confirmation || '';
    $('active-topics').replaceChildren();
    for (const row of activeTopics()) { const node = button(row.label, () => navigate('read', row.id)); node.classList.toggle('active', row.id === topicId && ['read','recall','cards',...Object.keys(courseViews)].includes(view)); $('active-topics').append(node); }
    $('automatic-terms').checked = data.state.preferences.automaticTerms !== false;
  }
  async function navigate(next, id, courseOptions) {
    if(!workspaceViews.includes(next))return;
    if(courseViews[next] && next!=='read' && !catalog.find(row=>row.id===(id||topicId))?.existingModule)id='neuro';
    if(courseOptions)coursePending=courseOptions;
    if (id && id !== topicId) { topicId = id; recall = null; answerDraft = ''; cardSession = null; if(data)await pref({lastTopic:id}); }
    view = next; location.hash = next;
    updateViewLabels();
    renderSidebar(); main.scrollTop = 0; window.scrollTo({top:0});
    await render();
  }
  function readPool() { const node = el('div', undefined, 'pool'); activeTopics().forEach(row => node.append(button(row.label, () => navigate('read', row.id), 'pill'))); return node; }
  function metric(value, label, note) { const node = el('div', undefined, 'stat'); node.append(el('strong', value), el('span', label), el('small', note)); return node; }
  function dashboard() {
    main.append(pageIntro('YOUR ONGOING STUDY POOL', 'Build understanding. Keep it.', 'Choose what you’re studying now. Recall, question outcomes, and Anki reviews bring those subjects back into your queue.'));
    const active = activeTopics(), signals = active.map(row => evidence(row.id, data));
    const due = ankiDueSummary(signals.map(row=>row.anki));
    const stats = el('div', undefined, 'stats');
    stats.append(metric(active.length, 'Active subjects', 'Paused subjects keep their history'),
      metric(data.queue.filter(row => row.kind === 'recall').length, 'Concept recalls due', 'Scheduled from your recall attempts'),
      metric(data.anki.available && due.known ? (due.partial?'≥ ':'')+due.count : '—', 'Anki cards due', data.anki.available ? 'Mapped topic totals · cards may overlap' : 'Sync Anki to connect'),
      metric(signals.reduce((n,row) => n + row.unresolved,0), 'Imported questions to revisit', 'Marked wrong or uncertain'));
    main.append(stats, readPool(), sectionHeading('Up next', button('Choose subjects', () => navigate('topics'), 'small')));
    const queue = el('div', undefined, 'queue');
    for (const item of data.queue.slice(0,10)) {
      const row = el('div', undefined, 'queue-item'), text = el('div');
      text.append(el('span', {learn:'First pass', recall:'Recall', anki:'Anki', questions:'Missed questions', course:'Course review'}[item.kind], 'kind'), el('strong', item.title), el('p', item.reason));
      const action = item.kind === 'anki' ? () => navigate('cards',item.topic_id) : item.kind === 'course' ? () => {const missed=!!data.module.topics?.[item.topic_id]?.wrong_count;return navigate(missed?'course-questions':'objectives',item.topic_id,{scope:'all',options:missed?{missed:true}:{reviewBad:true}});} : item.kind === 'questions' ? () => { questionTopic = item.topic_id; navigate('questions'); } : () => navigate(item.kind === 'recall' ? 'recall' : 'read', item.topic_id);
      row.append(text, button(item.kind === 'anki' ? 'Review cards' : item.kind === 'course' ? 'Review in module' : item.kind === 'questions' ? 'Review' : item.kind === 'recall' ? 'Recall' : 'Begin', action, 'small')); queue.append(row);
    }
    if (!data.queue.length) queue.append(el('div', 'No scheduled work is due. Open an active subject to read or practice recall.', 'empty-box'));
    main.append(queue, sectionHeading('Where to focus'), evidenceTable(active));
    if(data.anki.synced_at) main.append(el('p','Anki '+(data.anki.cached?'cached snapshot':'synced')+' · '+new Date(data.anki.synced_at).toLocaleString()+(data.anki.partial?' · bounded scopes; history sampled from up to '+(data.anki.scope?.history_sample_limit || 200)+' cards':''),'progress-note'));
    if(data.module?.available) main.append(el('p','Neuro/Psych reading, objectives, and question review share the module’s saved progress. Mixed topics may appear in both subjects.','progress-note'));
    else main.append(el('p','Course progress: '+(data.module?.reason || 'The Neuro/Psych content is loading.'),'progress-note'));
    const note = el('p', 'Recall scores describe the assessed answer. Anki difficulty and question misses are separate evidence; an unconnected source is shown as unknown.', 'progress-note'); note.style.marginTop = '15px'; main.append(note);
    const newTopics = el('div', undefined, 'section-heading'); newTopics.append(el('h2','Add your next subject'), button('All topics →', () => navigate('topics'), 'small')); main.append(newTopics);
    const suggested = el('div', undefined, 'topic-grid'); for (const id of ['renal','immunology','biochemistry']) { const row = catalog.find(item => item.id === id); if (row && !data.state.topics[id].active) suggested.append(topicCard(row)); } main.append(suggested);
  }
  function evidenceTable(rows) {
    const table = el('table', undefined, 'evidence-table'), head = el('thead'), header = el('tr');
    ['Subject','Last recall','Imported misses','Course LOs: good / bad','Course misses','Anki: Again / Hard · 30d','Cards due'].forEach(text => header.append(el('th',text))); head.append(header); table.append(head);
    const body = el('tbody');
    for (const topic of rows) {
      const info = evidence(topic.id, data), row = el('tr');
      row.append(el('td',topic.label),el('td', info.recall === null ? 'Unassessed' : Math.round(info.recall *100) + '%'), el('td', info.questions ? String(info.unresolved) : 'No tagged questions'),
        el('td', info.course ? `${info.course.lo_good} / ${info.course.lo_bad} · ${info.course.lo_unrated} unrated` : topic.existingModule?'Not connected':'No course module'), el('td', info.course ? String(info.course.wrong_count) : '—'),
        el('td', ankiReviewLabel(info.anki)), el('td', Number.isInteger(info.anki?.due_count)?(info.anki.scope_partial?'≥ ':'')+info.anki.due_count:info.anki?'Unknown':'Not connected'));
      if (info.recall !== null && info.recall < .6) row.children[1].className = 'alert-text';
      if (info.unresolved) row.children[2].className = 'alert-text';
      body.append(row);
    }
    table.append(body); const wrap=el('div',undefined,'table-scroll');wrap.append(table);return wrap;
  }
  async function activate(row, active) { try { data = await api('/api/activate',{topic_id:row.id,active}); catalog = data.catalog; renderSidebar(); await render(); saved(); toast(row.label + (active ? ' added to your study pool' : ' paused; history retained')); } catch(error) { toast(error.message); } }
  function topicCard(row) {
    const enabled = data.state.topics[row.id].active, node = el('div', undefined, 'topic-card' + (enabled ? ' enabled' : ''));
    const top = el('div', undefined, 'card-top'); top.append(el('h3',row.label),el('span',undefined,'dot'));
    node.append(top,el('small',row.existingModule ? 'Populated Neuro/Psych module' : 'Subject placeholder · background references available'));
    const info = evidence(row.id,data); node.append(el('div', enabled ? (info.recall === null ? 'Active · recall not assessed yet' : `Active · last recall ${Math.round(info.recall*100)}%`) : 'Activate when you begin this subject', 'progress-note'));
    const actions = el('div', undefined, 'card-actions'); actions.append(button(enabled ? 'Open' : 'Activate', () => enabled ? navigate('read',row.id) : activate(row,true), enabled ? '' : 'primary'));
    if (enabled) actions.append(button('Pause',() => activate(row,false),'small')); node.append(actions); return node;
  }
  function topicsPage() { main.append(pageIntro('FIRST AID TOPIC SKELETON','Your subjects','Activate a subject when you start it. It stays in the ongoing pool until you pause it. Course and Step evidence can be kept side by side.')); for (const group of [...new Set(catalog.map(row => row.group))]) { main.append(sectionHeading(group)); const grid = el('div',undefined,'topic-grid'); catalog.filter(row => row.group === group).forEach(row => grid.append(topicCard(row))); main.append(grid); } }
  function topicSelector(onChange, onlyActive = false) { const select = el('select'); select.setAttribute('aria-label','Study subject'); (onlyActive ? activeTopics() : catalog).forEach(row => { const option = el('option',row.label); option.value = row.id; select.append(option); }); select.value = topicId; select.onchange = () => onChange(select.value); return select; }
  function sourceAttribution(record) { const s=record.source || {}, node=el('div',undefined,'attribution'); node.append(safeLink(s.name || 'MDWiki',s.url),document.createTextNode(' contributors · '),safeLink(s.license || 'Source license',s.license_url)); if (s.history_url) node.append(document.createTextNode(' · '),safeLink('History',s.history_url)); if(s.original_url) node.append(document.createTextNode(' · '),safeLink('Original article',s.original_url)); if(s.original_history_url) node.append(document.createTextNode(' · '),safeLink('Original contributors',s.original_history_url)); node.append(el('div','Lead excerpt; shortened for display.')); return node; }
  function sourceImage(record) { const img=record.image, url=scope.MedicalTermCards.safeLink(img?.url,true); if(!url || !img.license || !scope.MedicalTermCards.safeLink(img.file_url) || !scope.MedicalTermCards.safeLink(img.license_url)) return null; const fig=el('figure',undefined,'definition-image'), image=el('img'); image.src=url; image.alt=img.alt || record.title; image.loading='lazy'; image.onerror=()=>fig.remove(); fig.append(image); const caption=el('figcaption'); caption.append(document.createTextNode((img.artist || 'Image contributors')+' · '),safeLink(img.license,img.license_url),document.createTextNode(' · '),safeLink('Image source',img.file_url)); fig.append(caption); return fig; }
  async function readingPage(serial) {
    if(topic().existingModule){showCourse();return;}
    const selected = topic(); main.append(pageIntro('READ & UNDERSTAND', selected.label, 'Keep the essential pattern visible. Open a definition without losing your place.'));
    const tools=el('div',undefined,'read-toolbar'); tools.append(topicSelector(id=>navigate('read',id)));
    const target=el('select'); target.setAttribute('aria-label','Study target'); [['both','Course + Step'],['step','Step'],['in-house','In-house']].forEach(([value,label])=>{const o=el('option',label);o.value=value;target.append(o);}); target.value=data.state.preferences.studyTarget || 'both'; target.onchange=async()=>{ await pref({studyTarget:target.value}); navigate('read'); }; tools.append(target);
    const active=data.state.topics[topicId].active; tools.append(button(active?'Practice recall':'Activate this subject',()=>active?navigate('recall'):activate(selected,true), 'primary')); main.append(tools);
    if(target.value==='in-house') {
      main.append(el('div','Course reading has not been added to this subject yet. Switch to Step for its starter background references.','empty-box'));
      appendTopicNotes();return;
    }
    main.append(el('p','This subject starts with a background reference set. Its full course/Step guide is a placeholder; activation adds tracking and recall without claiming curriculum coverage.', 'starter-note'));
    const loading=el('p','Loading source-backed concepts…','muted');main.append(loading);
    const result=await api('/api/topic?'+new URLSearchParams({id:topicId})); if(serial!==renderSerial)return; loading.remove(); readRecords=result.records || [];
    if(!readRecords.length) main.append(el('div','No starter article could be retrieved. Select any term or use the reference search to open an on-demand definition.', 'empty-box'));
    const length=el('p',undefined,'length-label');main.append(length);
    const article=el('article',undefined,'reading');article.dataset.medicalText='';
    const learned=data.state.preferences.learnedTerms || {}, collapsed=data.state.preferences.collapsedTerms || {};
    const updateLength=()=>{const n=readingLength(readRecords,collapsed);length.textContent=`≈ ${n.visiblePages.toFixed(1)} pages visible · ${n.fullPages.toFixed(1)} expanded · ${n.visibleWords.toLocaleString()} words (650/page + image allowance)`;};
    for(const record of readRecords){
      const details=el('details',undefined,'read-section');details.open=!(collapsed[record.id] ?? learned[record.id]);collapsed[record.id]=!details.open;const summary=el('summary');summary.append(el('h2',record.title),el('span',learned[record.id]?'Learned · skim':'Open','muted'));details.append(summary);
      // The defining pattern remains outside the collapsed section.
      const skim=el('p',record.definition,'skim');
      const body=el('div');const text=record.summary || record.definition; text.split(/\n\s*\n|\n/).filter(Boolean).forEach(part=>body.append(el('p',part)));const figure=sourceImage(record);if(figure)body.append(figure);
      const actions=el('div',undefined,'section-actions'); actions.append(button(learned[record.id]?'Reopen for study':'Learned · keep skim',async()=>{learned[record.id]=!learned[record.id];collapsed[record.id]=learned[record.id];await pref({learnedTerms:{...learned},collapsedTerms:{...collapsed}});details.open=!learned[record.id];summary.lastChild.textContent=learned[record.id]?'Learned · skim':'Open';skim.hidden=details.open;updateLength();actions.firstChild.textContent=learned[record.id]?'Reopen for study':'Learned · keep skim';},'small'),button('Define beside reading',()=>openDefinition(record.id,true),'small'));body.append(actions,sourceAttribution(record));details.append(body);article.append(details,skim);skim.hidden=details.open;
      details.ontoggle=()=>{skim.hidden=details.open;const changed=collapsed[record.id]!==!details.open;collapsed[record.id]=!details.open;updateLength();if(changed)pref({collapsedTerms:{...collapsed}}).catch(error=>toast(error.message));};
    }
    main.append(article);updateLength();
    if(result.misses?.length)main.append(el('p',result.misses.length+' additional background reference(s) unavailable; on-demand search remains available.','progress-note'));
    appendTopicNotes();
    attachScanner(article);
  }
  function appendTopicNotes(){
    const noteTopic=topicId, notes=el('div',undefined,'topic-notes'), label=el('label','My notes');label.htmlFor='topic-notes';const text=el('textarea');text.id='topic-notes';text.rows=4;text.value=data.state.topics[noteTopic].notes || '';text.placeholder='Your notes for this subject…';const save=button('Save notes',async()=>{await api('/api/notes',{topic_id:noteTopic,notes:text.value});data.state.topics[noteTopic].notes=text.value;saved();toast('Notes saved');},'small');notes.append(label,text,save);main.append(notes);
  }
  async function cardsPage(serial){
    main.append(pageIntro('ANKI REMAINS YOUR SCHEDULER',topic().label,'Review live cards from this subject’s reported AnKing / Step tags. Revealing or skipping an answer does not record a review.'));
    const tools=el('div',undefined,'read-toolbar');tools.append(topicSelector(id=>navigate('cards',id),true),button('Refresh card queue',async()=>{cardSession=null;await render();},'small'));main.append(tools);
    if(!data.state.topics[topicId]?.active){main.append(el('p','Activate this subject before reviewing cards.','muted'));return;}
    cardHost=el('div',undefined,'live-card');main.append(cardHost);
    if(!cardSession || cardSession.topicId!==topicId){
      cardHost.append(el('p','Reading the subject’s live Anki queue…','muted'));
      const queue=await api('/api/card-queue?'+new URLSearchParams({topic_id:topicId}),undefined,40000);if(serial!==renderSerial)return;
      cardSession={topicId,ids:queue.card_ids,index:0,card:null,revealed:false,busy:false,confirmed:0,partial:queue.partial,reason:queue.reason,request:null,error:null};
      await nextLiveCard(false);return;
    }
    renderLiveCard();
  }
  async function nextLiveCard(advance=true){
    const session=cardSession;if(!session || session.busy)return;
    if(advance)session.index++;
    session.card=null;session.revealed=false;session.error=null;session.request=null;
    if(session.index>=session.ids.length){renderLiveCard();return;}
    session.busy=true;renderLiveCard();
    try{const result=await api('/api/card-start',{topic_id:session.topicId,card_id:session.ids[session.index]},120000);if(session===cardSession)session.card=result;}
    catch(error){if(session===cardSession)session.error=error.message;}
    finally{session.busy=false;if(session===cardSession && view==='cards')renderLiveCard();}
  }
  function liveCardSide(side){
    const node=el('div',undefined,'live-card-side');node.append(el('p',side?.text || 'This card has no readable text; check its images below.'));
    for(const image of side?.images || []){
      if(typeof image.src!=='string' || !/^data:image\/(?:png|jpeg|gif|webp|bmp|svg\+xml);base64,[A-Za-z0-9+/=]+$/.test(image.src))continue;
      const img=el('img');img.src=image.src;img.alt=image.alt || 'Image from your local Anki card';img.loading='lazy';node.append(img);
    }
    return node;
  }
  function renderLiveCard(){
    if(!cardHost?.isConnected || view!=='cards' || !cardSession)return;
    const s=cardSession;scanner?.destroy();scanner=null;cardHost.replaceChildren();
    const progress=el('p',`${Math.min(s.index+1,s.ids.length)} / ${s.ids.length} cards · ${s.confirmed} ratings confirmed in Anki`,'progress-note');cardHost.append(progress);
    if(s.partial)cardHost.append(el('p',s.reason || 'This is a partial queue within a bounded subject scope. Refresh to recheck eligible cards.','progress-note'));
    if(s.busy && !s.card){cardHost.append(el('p','Opening the live card…','muted'));return;}
    if(s.index>=s.ids.length){cardHost.append(el('h2',s.ids.length?'This batch is finished.':'No eligible cards in this subject.'),el('p',s.reason || 'Open Anki to check due cards and unsuspended new cards.','muted'),button('Refresh queue',async()=>{cardSession=null;await render();},'primary'));return;}
    if(s.error)cardHost.append(el('p',s.error,'alert-text'));
    if(!s.card){cardHost.append(button('Retry this card',()=>nextLiveCard(false),'small'),button('Skip without rating',()=>nextLiveCard(),'small'));return;}
    const body=liveCardSide(s.revealed?s.card.back:s.card.front);cardHost.append(body);
    const actions=el('div',undefined,'card-ratings');
    if(!s.revealed)actions.append(button('Show answer · Space',()=>{s.revealed=true;renderLiveCard();},'primary'));
    else if(!s.request){for(const [ease,label] of [[1,'Again'],[2,'Hard'],[3,'Good'],[4,'Easy']]){const b=button(`${label} · ${ease}`,()=>rateLiveCard(ease));b.disabled=s.busy;actions.append(b);}}
    else actions.append(button(s.busy?'Checking Anki…':'Check this rating again',()=>rateLiveCard(s.request.ease),'small'));
    const skip=button('Skip without rating',()=>nextLiveCard(),'small');skip.disabled=s.busy || !!s.request;actions.append(skip);cardHost.append(actions);
    if(s.request)cardHost.append(el('p','Keep this request until Anki confirms it. Checking again reuses the same request; it does not submit a duplicate rating.','progress-note'));
    else cardHost.append(el('p','Again / Hard / Good / Easy writes your chosen rating to Anki. Card content stays local.','progress-note'));
    attachScanner(body);
  }
  async function rateLiveCard(ease){
    const s=cardSession;if(!s || !s.card || !s.revealed || s.busy)return;
    if(!s.request)s.request={id:crypto.randomUUID(),ease};
    if(s.request.ease!==ease)return;
    s.busy=true;s.error=null;renderLiveCard();
    try{
      const result=await api('/api/card-rate',{topic_id:s.topicId,token:s.card.token,ease:s.request.ease,request_id:s.request.id},120000);
      if(result.confirmed){s.confirmed++;s.request=null;data.anki.cached=true;s.busy=false;toast('Rating confirmed in Anki. Sync updates dashboard counts.');if(s===cardSession)await nextLiveCard();}
      else s.error='Anki has not confirmed this rating ('+(result.status || 'unknown')+'). Check again before moving on.';
    }catch(error){s.error=error.message;}
    finally{s.busy=false;if(s===cardSession && view==='cards')renderLiveCard();}
  }
  async function recallPage(serial){
    const selected=topic();main.append(pageIntro('EXPLAIN IT FROM MEMORY',selected.label,'Write a short explanation. The AI compares it with the linked source, then saves the result and schedules a concept recall. Anki scheduling stays separate.'));
    const tools=el('div',undefined,'read-toolbar');tools.append(topicSelector(id=>navigate('recall',id),true));main.append(tools);
    if(!data.state.topics[topicId]?.active){main.append(el('p','Activate this subject before practicing recall.','muted'),button('Activate',()=>activate(selected,true),'primary'));return;}
    if(!recall || recall.topic_id!==topicId){const loading=el('p','Preparing a source-backed prompt…','muted');main.append(loading);const result=await api('/api/recall?'+new URLSearchParams({topic_id:topicId}));if(serial!==renderSerial)return;loading.remove();recall=result.recall;answerDraft='';}
    if(!recall){main.append(el('div','No source-backed prompt is available yet. Open a background reference for this subject first.','empty-box'));return;}
    const card=el('div',undefined,'recall-card');card.append(el('h2',recall.prompt));
    const sources=el('div',undefined,'recall-sources'); sources.append(document.createTextNode('Source: '));recall.sources.forEach(row=>sources.append(safeLink(row.title,row.url)));card.append(sources);
    const answer=el('textarea');answer.className='answer-box';answer.rows=6;answer.maxLength=2000;answer.setAttribute('aria-label','Your recall answer');answer.placeholder='Explain the defining feature, mechanism, and relationship in your own words…';answer.value=answerDraft;answer.oninput=()=>answerDraft=answer.value;card.append(answer);
    const actions=el('div',undefined,'form-row');const grade=button('Grade answer',()=>submit(true),'primary'),save=button('Save without grading',()=>submit(false),'small');actions.append(grade,save);card.append(actions,el('p','Grade sends this prompt, your answer, and its linked excerpt to your signed-in Codex account. Other notes and questions are not included.','provider-status'));
    const status=el('p',data.ai.available?'AI connected · '+data.ai.model:'AI unavailable · '+(data.ai.reason || 'Sign in to Codex CLI to grade.'),'provider-status');card.append(status);grade.disabled=!data.ai.available;
    const expected=el('details',undefined,'expected');expected.append(el('summary','Check the source explanation'));const points=el('ul');recall.expected_points.forEach(point=>points.append(el('li',point)));expected.append(points);card.append(expected);
    const resultNode=el('div');card.append(resultNode);main.append(card);attachScanner(card);
    async function submit(shouldGrade){
      if(!answer.value.trim()){toast('Write an answer first');return;}
      grade.disabled=true;save.disabled=true;answer.disabled=true;status.textContent=shouldGrade?'Assessing your answer…':'Saving your answer…';
      try{const result=await api('/api/answer',{session_id:recall.session_id,answer:answer.value,grade:shouldGrade});const assessment=result.grade;const resultBox=el('div',undefined,'grade');resultBox.append(el('h3',assessment.assessed?Math.round(assessment.score*100)+'% · source-based recall':'Saved · unassessed'),el('p',assessment.feedback));if(assessment.missed_concepts?.length){const list=el('ul');assessment.missed_concepts.forEach(text=>list.append(el('li',text)));resultBox.append(list);}const next=button('Next concept →',async()=>{recall=null;answerDraft='';await refresh();await navigate('recall');},'small');resultBox.append(next);resultNode.replaceChildren(resultBox);status.textContent='Saved; next concept recall scheduled. No Anki rating was written.';await refresh();saved();}catch(error){toast(error.message);grade.disabled=!data.ai.available;save.disabled=false;answer.disabled=false;status.textContent='Your draft is still here.';}
    }
    const past=data.state.attempts.filter(row=>row.topic_id===topicId).slice(-5).reverse();if(past.length){main.append(sectionHeading('Recent recall'));for(const row of past){const item=el('details',undefined,'history-entry');item.append(el('summary',(row.grade?.assessed===true?Math.round(row.score*100)+'%':'Unassessed')+' · '+row.prompt),el('p',row.answer));if(row.grade?.feedback)item.append(el('p',row.grade.feedback,'muted'));main.append(item);}}
  }
  async function questionsPage(serial){
    main.append(pageIntro('PRIVATE QUESTION INBOX','Learn from your questions','New screenshots in your chosen folder are OCR’d locally and tagged by subject. Uncertain detections wait here for confirmation.'));
    const watch=el('div',undefined,'watch-box');watch.append(el('h3',data.intake.watch.running?'Watching '+data.intake.watch.folder_name:'Screenshot intake'));
    watch.append(el('p','Choose a screenshot folder below. Start watches new files; Scan existing imports up to 20 existing images per pass. This reads screenshot files—it does not capture your desktop.'));
    const row=el('div',undefined,'form-row'),folder=el('input');folder.type='text';folder.setAttribute('aria-label','Screenshot folder path');folder.placeholder='Leave blank to watch new Desktop screenshots';folder.value=data.state.preferences.watchFolder || '';
    const toggle=button(data.intake.watch.running?'Stop watching':'Start watching',async()=>{const result=await api('/api/watch',{enabled:!data.intake.watch.running,folder:folder.value.trim() || null});if(result.watch.errors?.length)toast(result.watch.errors.join(' '));await refresh();await navigate('questions');},'small');
    const scan=button('Scan existing',async()=>{if(!folder.value.trim()){toast('Enter a folder to scan existing screenshots');return;}scan.disabled=true;scan.textContent='Scanning…';try{const result=await api('/api/scan-folder',{folder:folder.value.trim()},120000);toast('Scan complete'+(result.scan.truncated?' · more files remain':''));await refresh();await navigate('questions');}catch(error){toast(error.message);scan.disabled=false;scan.textContent='Scan existing';}},'small');row.append(folder,toggle,scan);watch.append(row);
    const counts=data.intake.counts;watch.append(el('small',`${counts.confirmed || 0} questions · ${counts.needs_confirmation || 0} to check · ${counts.excluded || 0} non-questions · ${counts.error || 0} OCR errors`));if(data.intake.watch.errors?.length)watch.append(el('p',data.intake.watch.errors.join(' '),'alert-text'));main.append(watch);
    const search=el('form',undefined,'form-row'),input=el('input');input.type='search';input.placeholder='Search the full OCR question text…';input.setAttribute('aria-label','Search saved questions');input.value=questionFilter;const select=el('select');select.setAttribute('aria-label','Filter question subject');const all=el('option','All subjects');all.value='';select.append(all);catalog.forEach(row=>{const o=el('option',row.label);o.value=row.id;select.append(o);});select.value=questionTopic;search.append(input,select,button('Search',()=>{questionFilter=input.value.trim();questionTopic=select.value;navigate('questions');},'small'));search.onsubmit=event=>{event.preventDefault();questionFilter=input.value.trim();questionTopic=select.value;navigate('questions');};main.append(search);
    const result=await api('/api/questions?'+new URLSearchParams({q:questionFilter,topic_id:questionTopic}));if(serial!==renderSerial)return;
    if(!result.questions.length)main.append(el('div','No saved questions match. Start watching your screenshot folder, or scan the images already there.','empty-box'));
    const list=el('div');for(const row of result.questions)list.append(questionCard(row));main.append(list);attachScanner(list);
  }
  function questionCard(row){
    const node=el('article',undefined,'question-card'),head=el('div',undefined,'question-header');head.append(el('span',row.status==='confirmed'?'Question · '+row.outcome:'Check OCR detection'),el('span',row.topic_ids.map(id=>catalog.find(item=>item.id===id)?.label || id).join(' · ') || 'Subject not identified'));node.append(head,el('p',row.stem || row.full_text,'stem'));
    if(row.options?.length){const options=el('div',undefined,'options');row.options.forEach(text=>options.append(el('div',text)));node.append(options);}
    const actions=el('div',undefined,'outcomes');const update=async(action,values={})=>{await api('/api/question',{id:row.id,action,...values});await refresh();await navigate('questions');};
    if(row.error){node.append(el('p',row.error,'alert-text'));actions.append(button('Retry OCR',()=>update('retry'),'small'));}
    else if(row.status!=='confirmed')actions.append(button('This is a question',()=>update('confirm'),'small'));
    else for(const [value,label] of [['wrong','Wrong'],['uncertain','Uncertain'],['correct','Understood'],['unknown','Unrated']]){const b=button(label,()=>update('outcome',{outcome:value}),'small');b.classList.toggle('selected',row.outcome===value);actions.append(b);}
    actions.append(button('Exclude',()=>update('exclude'),'small'));node.append(actions);
    const tags=el('details');tags.append(el('summary','Check subject tags'));const tagRow=el('div',undefined,'form-row'),selected=new Set(row.topic_ids);for(const topic of catalog){const label=el('label'),box=el('input');box.type='checkbox';box.checked=selected.has(topic.id);box.onchange=()=>box.checked?selected.add(topic.id):selected.delete(topic.id);label.append(box,document.createTextNode(' '+topic.label));tagRow.append(label);}tags.append(tagRow,button('Save subjects',()=>update('topics',{topic_ids:[...selected]}),'small'));node.append(tags);
    const raw=el('details');raw.append(el('summary','Original screenshot & OCR'));const img=el('img');img.loading='lazy';img.src='/api/question-image?id='+encodeURIComponent(row.id);img.alt='Saved source screenshot';raw.append(img,el('pre',row.full_text));node.append(raw);
    if(row.explanation){const explanation=el('details');explanation.append(el('summary','Explanation from the screenshot'),el('p',row.explanation));node.append(explanation);}return node;
  }
  function settingsPage(){main.append(pageIntro('LOCAL APP','Settings & backup','Your study pool, notes, recall history, and questions live in a private database outside the public project.'));
    const backup=el('div',undefined,'settings-block');backup.append(el('h2','Progress backup'),el('p','Download your topic activation, notes, preferences, and recall attempts. This file is personal—keep it out of the public repository.'),link('Export progress','/api/export'));main.append(backup);
    const reference=el('div',undefined,'settings-block');reference.append(el('h2','Reference in a second window'),el('p','Keep the guide in this window and definitions in another. Closing the second window returns new lookups to the side pane.'),button('Open reference window',()=>detachReference()));main.append(reference);
    const connections=el('div',undefined,'settings-block');connections.append(el('h2','Study connections'),el('p',data.anki.available?'Anki connected'+(data.anki.partial?' · bounded scopes and sampled history':''):'Anki: '+(data.anki.reason || 'Not connected')),el('p',data.ai.available?'AI grading connected · '+data.ai.model:'AI grading: '+data.ai.reason),el('p',data.speech.available?'Kitten Micro voice installed · local, on demand':'Local Kitten voice is not installed.'),el('p',data.glossary_count+' definitions cached locally. New terms are requested from MDWiki only when you choose a lookup.'),button('Sync Anki',()=>syncAnki(),'small'));main.append(connections);
    const modules=el('div',undefined,'settings-block');modules.append(el('h2','Neuro/Psych module'),el('p','Your populated guide is integrated into Read, Learning objectives, Course questions, Pathology, Drugs, and Bugs. It uses the existing progress database and reference library.'),button('Read Neuro/Psych',()=>navigate('read','neuro')));main.append(modules);
  }
  async function render(){const serial=++renderSerial;scanner?.destroy();scanner=null;main.replaceChildren();const course=!!courseViews[view]&&!!topic()?.existingModule;main.hidden=course;$('course-host').hidden=!course;$('workspace').classList.toggle('course-active',course);$('open-reference').textContent=course?'References':'Reference ↗';try{if(course)showCourse();else if(view==='today')dashboard();else if(view==='topics')topicsPage();else if(view==='read')await readingPage(serial);else if(view==='recall')await recallPage(serial);else if(view==='cards')await cardsPage(serial);else if(view==='questions')await questionsPage(serial);else settingsPage();}catch(error){if(serial===renderSerial){main.hidden=false;$('course-host').hidden=true;main.append(el('p',error.message,'alert-text'));}} }
  function attachScanner(root){scanner?.destroy();scanner=scope.MedicalTermCards.attach(root,lexicon,{onOpen:id=>openDefinition(id,true),onHover:hoverDefinition});scanner.setEnabled(data.state.preferences.automaticTerms!==false);}
  function updateViewLabels(){
    $('view-label').textContent=({today:'Today',topics:'Topics',read:'Read & understand',objectives:'Learning objectives','course-questions':'Course questions',pathology:'Pathology',drugs:'Drugs',bugs:'Bugs',recall:'Open-ended recall',cards:'Live Anki cards',questions:'Question inbox',settings:'Settings & backup'})[view];
    const course=!!courseViews[view]&&!!topic()?.existingModule;
    $('scope-label').textContent=course?'Neuro / Psych'+(courseInfo?` · ${courseInfo.scope==='week8'?'Week 8 / Quiz 7':'Full course by topic'} · ${courseInfo.currentPages.toFixed(1)} page eq. now`:''):['read','recall','cards'].includes(view)?topic()?.label||'':'Your active subjects';
  }
  function coursePost(type,extra={}){if(courseReady)courseFrame.contentWindow.postMessage({app:'step-course',type,...extra},location.origin);}
  function showCourse(){
    main.hidden=true;$('course-host').hidden=false;
    if(!courseFrame){
      courseFrame=el('iframe');courseFrame.id='course-reader';courseFrame.title='Neuro/Psych study guide';
      courseFrame.src='/course/reader?'+new URLSearchParams({view:courseViews[view],subject:topicId,target:data.state.preferences.studyTarget||'both'});
      $('course-host').append(courseFrame);
    }else if(courseReady){coursePost('navigate',{view:courseViews[view],target:data.state.preferences.studyTarget||'both',...(coursePending||{})});coursePending=null;}
    updateViewLabels();
  }
  window.addEventListener('message',event=>{
    if(!courseFrame||event.source!==courseFrame.contentWindow||event.origin!==location.origin||event.data?.app!=='step-course')return;
    const message=event.data;
    if(message.type==='ready'){
      courseReady=true;coursePost('definitions',{enabled:data.state.preferences.automaticTerms!==false});
      if(courseViews[view]){coursePost('navigate',{view:courseViews[view],target:data.state.preferences.studyTarget||'both',...(coursePending||{})});coursePending=null;}
    }
    if(['ready','view','length','saved'].includes(message.type) && Number.isFinite(message.currentPages) && ['all','week8'].includes(message.scope)){
      courseInfo=message;
      if(message.type==='view'&&courseReady&&!$('course-host').hidden){const next=Object.keys(courseViews).find(key=>courseViews[key]===message.view);if(next){view=next;window.history.replaceState(null,'','#'+next);renderSidebar();}}
      updateViewLabels();
    }
    if(message.type==='saved'){clearTimeout(courseRefreshTimer);courseRefreshTimer=setTimeout(()=>refresh().catch(()=>{}),400);}
  });
  $('course-module-start').onclick=()=>navigate('read','neuro');
  function showReference(){ $('reference-pane').hidden=false;$('workspace').classList.add('with-reference'); }
  function closeHover(){clearTimeout(hoverTimer);hoverSerial++;$('quick-definition').hidden=true;}
  async function lookup(query,isId=false){const key=(isId?'id:':'q:')+query.toLowerCase();if(definitions.has(key))return definitions.get(key);const result=await api('/api/term?'+new URLSearchParams({[isId?'id':'q']:query}),undefined,45000);if(result.ok){definitions.set(key,result.record);definitions.set('id:'+result.record.id,result.record);return result.record;}return result;}
  async function openDefinition(query,isId=false,fromHistory=false,forceLocal=false){
    lastDefinitionRequest={query,isId};closeHover();
    if(!referenceOnly && detachedWanted && Date.now()-detachedAliveAt<6500 && channel && !forceLocal){channel.postMessage({type:'lookup',query,isId});return;}
    showReference();const serial=++definitionSerial;panel.replaceChildren(el('p','Looking up '+(lexicon.find(row=>row.id===query)?.title || query)+'…','muted'));
    try{const result=await lookup(query,isId);if(serial!==definitionSerial)return;if(!result.id){panel.replaceChildren(el('h2',result.error==='source_unavailable'?'Source unavailable':'Choose the specific term'),el('p',result.error==='source_unavailable'?'Cached definitions still work. This new term needs MDWiki to respond.':'No exact definition matched “'+query+'”. Choose an article below or try the full name.','muted'));for(const candidate of result.candidates || [])panel.append(button(candidate.title,()=>openDefinition(candidate.title),'small'));return;}
      if(!lexicon.some(row=>row.id===result.id)){lexicon.push({id:result.id,title:result.title,aliases:result.aliases || []});if(scanner){scanner.compiled=scope.MedicalTermCards.compileLexicon(lexicon);scanner.scan();}}
      panel.replaceChildren(el('p',result.source?.name || 'MDWiki','source-badge'),el('h2',result.title),el('p',result.definition || result.summary,'definition-lead'));
      if(data?.speech.available)panel.append(button('▶ Listen',()=>pronounce(result.title),'small'));
      const image=sourceImage(result);if(image)panel.append(image);
      const details=el('details');details.append(el('summary','More background'));const rest=(result.summary || '').startsWith(result.definition || '')?(result.summary || '').slice((result.definition || '').length).trim():result.summary; if(rest){details.append(el('p',rest));panel.append(details);}panel.append(sourceAttribution(result),safeLink('Full source article ↗',result.source?.url));
      if(!fromHistory && history[historyAt]!==result.id){history=history.slice(0,historyAt+1);history.push(result.id);historyAt=history.length-1;}
      $('definition-back').disabled=historyAt<=0;$('definition-forward').disabled=historyAt>=history.length-1;
      $('reference-pane').scrollTop=0;
    }catch(error){if(serial===definitionSerial)panel.replaceChildren(el('p',error.message,'alert-text'));}
  }
  function hoverDefinition(id,node,immediate=false){clearTimeout(hoverTimer);const serial=++hoverSerial;hoverTimer=setTimeout(async()=>{try{const record=await lookup(id,true);if(serial!==hoverSerial || !node.isConnected || !record.id)return;const hover=$('quick-definition');hover.replaceChildren(el('h3',record.title),el('p',record.definition),el('small','MDWiki · '+(record.source?.license || '')),button('Keep beside reading →',()=>openDefinition(id,true),'small'));hover.hidden=false;const rect=node.getBoundingClientRect();hover.style.left=Math.max(12,Math.min(rect.left,innerWidth-325))+'px';hover.style.top=Math.max(12,Math.min(rect.bottom+8,innerHeight-hover.offsetHeight-12))+'px';}catch(_){}},immediate?0:350);}
  async function pronounce(text){try{const response=await fetch('/api/speech',{method:'POST',headers:{'Content-Type':'application/json','X-Step-Token':token},body:JSON.stringify({text,voice:'Bella'}),signal:AbortSignal.timeout(85000)});if(!response.ok)throw new Error('Local pronunciation is unavailable');const url=URL.createObjectURL(await response.blob()),audio=new Audio(url);audio.onended=()=>URL.revokeObjectURL(url);await audio.play();}catch(error){toast(error.message);}}
  function detachReference(){detachedWanted=true;window.open('/?reference=1','step-study-reference');toast('Reference window opened. Select a term to send it there.');}
  if(channel){channel.onmessage=event=>{const message=event.data;if(!message || typeof message!=='object')return;if(message.type==='ready' || message.type==='alive'){detachedAliveAt=Date.now();if(message.type==='ready' && lastDefinitionRequest)channel.postMessage({type:'lookup',...lastDefinitionRequest});}if(message.type==='closed'){detachedAliveAt=0;detachedWanted=false;}if(referenceOnly && message.type==='lookup' && typeof message.query==='string' && message.query.length<=160)openDefinition(message.query,message.isId===true,false,true);};}
  async function syncAnki(){const node=$('sync-anki');node.disabled=true;node.textContent='Syncing…';try{const result=await api('/api/anki-sync',{},120000);await refresh();await render();toast(result.anki.available?'Anki signals refreshed'+(result.anki.partial?' · bounded sample':''):'Anki unavailable; open Anki with AnkiConnect.');}catch(error){toast(error.message);}finally{node.disabled=false;node.textContent='Sync Anki';}}
  $('definition-search').onsubmit=event=>{event.preventDefault();const query=$('definition-query').value.trim();if(query)openDefinition(query,false,false,true);};
  $('definition-back').onclick=()=>{if(historyAt>0)openDefinition(history[--historyAt],true,true,true);};$('definition-forward').onclick=()=>{if(historyAt<history.length-1)openDefinition(history[++historyAt],true,true,true);};
  $('close-reference').onclick=()=>{$('reference-pane').hidden=true;$('workspace').classList.remove('with-reference');};
  $('open-reference').onclick=()=>!$('course-host').hidden?coursePost('reference'):detachReference();$('sync-anki').onclick=syncAnki;$('settings-button').onclick=()=>navigate('settings');
  $('automatic-terms').onchange=async event=>{await pref({automaticTerms:event.target.checked});scanner?.setEnabled(event.target.checked);coursePost('definitions',{enabled:event.target.checked});closeHover();};
  document.querySelectorAll('[data-view]').forEach(node=>node.onclick=()=>navigate(node.dataset.view));
  addEventListener('hashchange',()=>{const next=location.hash.slice(1);if(workspaceViews.includes(next) && next!==view)navigate(next);});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')closeHover();if(view==='cards' && !event.ctrlKey && !event.metaKey && !event.altKey && !event.target.closest('input,textarea,select,button,a,[contenteditable]')){if(event.code==='Space' && cardSession?.card && !cardSession.busy){event.preventDefault();cardSession.revealed=true;renderLiveCard();}else if(/^[1-4]$/.test(event.key) && cardSession?.revealed && !cardSession.request){event.preventDefault();rateLiveCard(Number(event.key));}}});addEventListener('scroll',closeHover,true);
  main.addEventListener('mouseout',event=>{if(event.target.closest?.('.med-term') && !event.relatedTarget?.closest?.('#quick-definition')){clearTimeout(hoverTimer);setTimeout(()=>{if(!$('quick-definition').matches(':hover'))closeHover();},250);}});$('quick-definition').onmouseleave=closeHover;
  const splitter=$('splitter');let drag=false;
  splitter.onpointerdown=event=>{drag=true;splitter.setPointerCapture(event.pointerId);event.preventDefault();};
  splitter.onpointermove=event=>{if(!drag)return;const rect=$('workspace').getBoundingClientRect();const width=Math.max(25,Math.min(70,(rect.right-event.clientX)/rect.width*100));document.documentElement.style.setProperty('--panel-width',width+'%');};
  splitter.onpointerup=async()=>{drag=false;const value=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--panel-width'));await pref({panelWidth:value});};
  splitter.onkeydown=async event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const now=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--panel-width')) || 50;const width=Math.max(25,Math.min(70,now+(event.key==='ArrowLeft'?3:-3)));document.documentElement.style.setProperty('--panel-width',width+'%');await pref({panelWidth:width});};
  async function boot(){
    try{await refresh();const result=await api('/api/terms');lexicon=result.records || [];topicId=data.state.preferences.lastTopic && catalog.some(row=>row.id===data.state.preferences.lastTopic)?data.state.preferences.lastTopic:'neuro';document.documentElement.style.setProperty('--panel-width',(data.state.preferences.panelWidth || 50)+'%');selectionLookup=scope.MedicalSelectionLookup.attach(main,query=>openDefinition(query));
      if(referenceOnly){document.body.classList.add('reference-only');showReference();detachedWanted=false;channel?.postMessage({type:'ready'});setInterval(()=>channel?.postMessage({type:'alive'}),2000);addEventListener('pagehide',()=>channel?.postMessage({type:'closed'}));}
      else{await navigate(view);setInterval(async()=>{try{await refresh();if(['today','topics'].includes(view))await render();}catch(_) {$('save-status').textContent='Local service disconnected';}},20000);}
    }catch(error){main.replaceChildren(pageIntro('STEP STUDY','Connection unavailable',error.message));$('save-status').textContent='Not connected';}
  }
  boot();
})(typeof window!=='undefined'?window:this);
