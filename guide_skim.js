const guideSkimPoints={
 'neuroaxis-umn-lmn-block-1':['Localize first: cortex → brainstem → cord → anterior horn → root/plexus/nerve → NMJ → muscle.','Match motor and sensory findings before choosing a cause.'],
 'neuroaxis-umn-lmn-block-2':['UMN: increased tone and reflexes; extensor plantar response.','LMN: reduced tone and reflexes; atrophy and fasciculations.'],
 'neuroaxis-umn-lmn-block-3':['ALS can combine UMN and LMN signs.','A cord lesion may cause LMN signs at the lesion level and UMN signs below it.'],
 'pattern-localization-block-1':['Cortex/subcortex: contralateral motor and sensory deficits; aphasia or neglect can add localization.','Brainstem: ipsilateral cranial-nerve findings with contralateral long-tract deficits.'],
 'pattern-localization-block-2':['Cord: sensory level, bilateral findings below the lesion, possible autonomic dysfunction.','Brown-Séquard: ipsilateral motor/proprioception loss; contralateral pain/temperature loss below the lesion.','Root/plexus: focal pain, weakness, sensory loss, and reflex change in the relevant distribution.'],
 'pattern-localization-block-3':['Anterior horn: motor weakness without a sensory deficit.','Peripheral nerve: sensory and motor changes in a nerve distribution.','NMJ/muscle: use weakness pattern, sensation, and reflexes to distinguish them.'],
 'tempo-vitamins-block-1':['Abrupt deficit: vascular causes. Focal slow progression: tumor or abscess.','Widespread gradual decline: neurodegeneration. Recurrent attacks: MS, migraine, or seizures.'],
 'tempo-vitamins-block-2':['Vascular · Infectious · Toxic–metabolic · Autoimmune.','Metastatic/neoplastic · Iatrogenic · Neurodegenerative · Systemic/seizure/psychogenic.'],
 'tempo-vitamins-block-3':['Describe syndrome → localize → establish tempo → rank causes that explain the whole pattern.','B12 deficiency can combine dorsal-column, corticospinal, and peripheral sensory findings.'],
 'inflammatory-myopathies-block-1':['Proximal weakness with preserved sensation and reflexes points toward muscle.','CK supports muscle injury; age, tempo, rash, and biopsy refine the disease.'],
 'inflammatory-myopathies-block-2':['IMNM: muscle-fiber necrosis with absent/minimal inflammation.','Polymyositis: endomysial inflammation with necrotic fibers.'],
 'inflammatory-myopathies-block-3':['Heliotrope rash and Gottron papules are clinical clues.','Perifascicular atrophy is the characteristic biopsy clue.'],
 'ibm-dmd-block-1':['IBM: slow progression, especially older men; finger flexors and quadriceps.','Rimmed vacuoles and tubulofilamentous inclusions support the tissue diagnosis.'],
 'ibm-dmd-block-2':['DMD: young boys; X-linked recessive; severe proximal weakness and very high CK.','Gowers’ sign, calf pseudohypertrophy, and absent dystrophin fit the pattern.'],
 'ibm-dmd-block-3':['IBM: older age, selective finger-flexor/quadriceps weakness, slow course, rimmed vacuoles.','DMD: childhood onset, proximal weakness, X-linked inheritance, absent dystrophin.']
};
function blockSkimHtml(block){const key=block.id.replace(/^guide-v2-/,''),items=block.skim||guideSkimPoints[key]||[block.summary];return state.edits['skim-'+block.id]||block.skim_html||`<ul>${items.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>`}
function blockSkimMarkup(block){return `<div class="editable" data-edit="skim-${block.id}" contenteditable="${editing}" aria-label="Edit morning skim for ${esc(block.title)}">${editing?blockSkimHtml(block):decorateReading('skim-'+block.id,keywordify(blockSkimHtml(block)))}</div>`}
function guideTextWords(text){const t=document.createElement('template');t.innerHTML=String(text||'').replace(/<[^>]*>/g,' ');return (t.content.textContent.match(/[\p{L}\p{N}_’]+/gu)||[]).length}
function guideReadingCounts(blocks){
 let full=0,current=0,skim=0;
 for(const b of blocks){
  const heading=guideTextWords(b.title),captions=(b.figures||[]).reduce((n,f)=>n+guideTextWords(f.caption),0);
  const expanded=heading+guideTextWords(state.edits[b.id]||b.html)+captions,brief=heading+guideTextWords(blockSkimHtml(b));
  full+=expanded;skim+=brief;current+=state.good[b.id]?brief:expanded;
 }
 return {full,current,skim};
}
function guideLengthCounts(){
 const entries=scopePageEntries(),counts=guideReadingCounts(entries.flatMap(({p})=>p.blocks));
 const headings=entries.reduce((n,{p})=>n+guideTextWords(p.title)+guideTextWords(p.subtitle),0)+scopeTopicGroups().reduce((n,g)=>n+guideTextWords(g.title),0);
 for(const key of ['full','current','skim'])counts[key]+=headings;
 counts.objectives=scopeObjectives().reduce((n,o)=>{
  const a=DATA.objective_answers[o.id]||{};
  return n+guideTextWords(o.text)+guideTextWords(a.html)+(a.figures||[]).reduce((v,f)=>v+guideTextWords(f.caption),0);
 },0);
 counts.unit=DATA.word_budget?.words_per_page_equivalent||700;
 return counts;
}
function guideLengthMarkup(){
 return `<div class="guide-length" data-guide-length aria-live="polite"></div>`;
}
function refreshGuideLength(){
 const c=guideLengthCounts(),eq=n=>(n/c.unit).toFixed(1),format=n=>n.toLocaleString();
 const markup=`<div class="length-metrics"><span><small>READ NOW</small><strong>${eq(c.current)} page eq.</strong><small>${format(c.current)} words</small></span><span><small>READ EXPANDED</small><strong>${eq(c.full)} page eq.</strong></span><span><small>ALL LEARNED · SKIM</small><strong>${eq(c.skim)} page eq.</strong></span></div><p class="length-reference">LO reference: ${eq(c.objectives)} eq. separately · All study content expanded: <strong>${((c.full+c.objectives)/c.unit).toFixed(2)} eq.</strong></p><details class="length-explanation"><summary>How length is counted</summary><p>One page equivalent is about ${c.unit} words. Read now replaces each learned section’s explanation and image captions with its retained skim. Headings, tables, and your edits count. The separate LO reference includes the verbatim prompts, answers, and image captions. Questions, comparison sheets, personal notes, and image area are excluded.</p></details>`;
 document.querySelectorAll('[data-guide-length]').forEach(el=>{el.innerHTML=markup});
 const budget=$('#word-budget');
 if(budget){
  budget.innerHTML=`Read now<br><strong>${eq(c.current)} page eq.</strong><span class="skim-budget">${format(c.current)} words</span><span class="skim-budget">All learned: ${eq(c.skim)} eq.</span>`;
  budget.title=`Current reading: ${format(c.current)} words. Expanded reading: ${format(c.full)} words. All learned skim: ${format(c.skim)} words. LO reference is separate: ${format(c.objectives)} words. ${c.unit} words per page equivalent.`;
 }
}
document.addEventListener('input',e=>{if(e.target.matches('[data-edit]'))refreshGuideLength()});
function missedQuestionsMarkup(id){
 const qs=DATA.questions.filter(q=>/wrong/i.test(q.source_status||'')&&(qRecord(q.id).sections.includes(id)||(DATA.question_annotations||[]).some(a=>a.block_id===id&&a.question_id===q.id)));
 if(!qs.length)return '';
 return `<details class="missed-inline"><summary>${qs.length} original wrong question${qs.length===1?'':'s'} · inspect the missed facts</summary><div class="missed-inline-list">${qs.map(q=>`<button data-open-wrong-question="${esc(q.id)}">${esc(q.title)}<small>${esc(q.key_fact)}</small></button>`).join('')}</div></details>`;
}
