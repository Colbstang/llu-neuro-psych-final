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
function blockSkimHtml(block){const key=block.id.replace(/^guide-v2-/,''),items=block.skim||guideSkimPoints[key]||[block.summary];return state.edits['skim-'+block.id]||`<ul>${items.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>`}
function blockSkimMarkup(block){return `<div class="editable" data-edit="skim-${block.id}" contenteditable="${editing}" aria-label="Edit morning skim for ${esc(block.title)}">${editing?blockSkimHtml(block):decorateReading('skim-'+block.id,keywordify(blockSkimHtml(block)))}</div>`}
function guideTextWords(text){const t=document.createElement('template');t.innerHTML=text;return (t.content.textContent.match(/[\p{L}\p{N}’]+/gu)||[]).length}
function guideReadingCounts(blocks){let full=0,current=0,skim=0;for(const b of blocks){const expanded=guideTextWords(state.edits[b.id]||b.html),brief=guideTextWords(blockSkimHtml(b));full+=expanded;skim+=brief;current+=state.good[b.id]?brief:expanded}return {full,current,skim}}
function missedQuestionsMarkup(id){
 const qs=DATA.questions.filter(q=>/wrong/i.test(q.source_status||'')&&(qRecord(q.id).sections.includes(id)||(DATA.question_annotations||[]).some(a=>a.block_id===id&&a.question_id===q.id)));
 if(!qs.length)return '';
 return `<details class="missed-inline"><summary>${qs.length} original wrong question${qs.length===1?'':'s'} · inspect the missed facts</summary><div class="missed-inline-list">${qs.map(q=>`<button data-open-wrong-question="${esc(q.id)}">${esc(q.title)}<small>${esc(q.key_fact)}</small></button>`).join('')}</div></details>`;
}
