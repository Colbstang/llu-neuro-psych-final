/* The public renderer contains no private lesson text or medical answer bank. */
(function(scope){
  'use strict';
  function observation(lesson,lesionId,direction){
    if(!['left','right'].includes(direction))return {leftEyeMoves:false,rightEyeMoves:false,leftEyeDegrees:0,rightEyeDegrees:0,observation:'Both eyes are centered.'};
    if(lesionId){const item=lesson.lesions.find(item=>item.id===lesionId);if(!item)throw new Error('The selected lesion is unavailable');return item['expected_'+direction];}
    const normal=lesson.lesions.map(item=>item['expected_'+direction]).find(item=>item.leftEyeMoves&&item.rightEyeMoves&&(direction==='right'?item.leftEyeDegrees>0&&item.rightEyeDegrees>0:item.leftEyeDegrees<0&&item.rightEyeDegrees<0));
    if(!normal)throw new Error('The lesson has no preserved observation for this direction');
    return {...normal,observation:'Both eyes move '+direction+'ward in the lesson’s preserved observation.'};
  }
  function groups(lesson){const unique=new Map();for(const item of lesson.lesions.filter(item=>item.quizable)){if(!unique.has(item.equivalence_group))unique.set(item.equivalence_group,[]);unique.get(item.equivalence_group).push(item);}return [...unique].map(([id,items])=>({id,label:items.map(item=>item.name).join(' / '),lesions:items}));}
  function matchesGroup(lesson,lesionId,group){return typeof group==='string'&&group.length>0&&lesson.lesions.find(item=>item.id===lesionId)?.equivalence_group===group;}
  function pupilOffset(degrees){return -Number(degrees)*.95;}
  const exported={observation,groups,matchesGroup,pupilOffset};
  if(typeof module!=='undefined')module.exports=exported;
  if(typeof document==='undefined')return;
  const $=id=>document.getElementById(id),node=(tag,text,cls)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;};
  let lesson,lesionId='',mode='explore',direction='',checked=false,lastHidden='';
  const testedDirections=new Set();
  function lesion(){return lesson.lesions.find(item=>item.id===lesionId);}
  function circuit(){
    const root=$('circuit');root.replaceChildren();
    if(mode==='challenge'&&!checked){$('lesion-reasoning').textContent='Check your localization before revealing the lesion and its reasoning.';return;}
    const selected=lesion();$('lesion-reasoning').textContent=selected?selected.name+': '+selected.function+' '+selected.evidence_note:'Trace the two commands below. The crossing connections are specified by the selected source lesson.';
    const names=new Map(lesson.entities.map(item=>[item.id,item.name]));
    for(const path of lesson.pathways){const section=node('section',undefined,'circuit-path');section.append(node('h2',path.name));for(const edge of path.edges){const row=node('div',undefined,'circuit-edge');row.append(node('span',names.get(edge.source),'circuit-node'+(selected?.site===edge.source?' lesion-site':'')),node('span','→','circuit-arrow'),node('span',names.get(edge.target),'circuit-node'+(selected?.site===edge.target?' lesion-site':'')),node('p',edge.function));section.append(row);}root.append(section);}
  }
  function paint(result){$('left-pupil').setAttribute('transform','translate('+pupilOffset(result.leftEyeDegrees)+' 0)');$('right-pupil').setAttribute('transform','translate('+pupilOffset(result.rightEyeDegrees)+' 0)');$('eye-description').textContent=result.observation;$('observation').textContent=result.observation;}
  function test(command){
    direction=command;if(command)testedDirections.add(command);const result=observation(lesson,lesionId,command);paint(result);
    if(mode==='explore'&&command){const good=$('predict-left').checked===result.leftEyeMoves&&$('predict-right').checked===result.rightEyeMoves;$('feedback').textContent=good?'Your movement prediction matches the selected lesson.':'Compare your prediction: '+(result.leftEyeMoves?'left eye moves':'left eye stays centered')+'; '+(result.rightEyeMoves?'right eye moves':'right eye stays centered')+'.';}
    else $('feedback').textContent='';
  }
  function pickHidden(){const options=lesson.lesions.filter(item=>item.quizable&&item.id!==lastHidden);const chosen=options[Math.floor(Math.random()*options.length)];if(!chosen)return;lesionId=chosen.id;lastHidden=lesionId;checked=false;direction='';testedDirections.clear();$('localization-choice').value='';$('feedback').textContent='Test both gaze directions, then select a localization. Pontine sites with the same modeled observation are grouped.';$('source-details').open=false;paint(observation(lesson,lesionId,''));circuit();}
  function changeMode(next){mode=next;$('explore-mode').setAttribute('aria-pressed',String(mode==='explore'));$('challenge-mode').setAttribute('aria-pressed',String(mode==='challenge'));$('explore-controls').hidden=mode!=='explore';$('challenge-controls').hidden=mode!=='challenge';if(mode==='challenge')pickHidden();else{lesionId=$('lesion-choice').value;checked=false;$('feedback').textContent='';paint(observation(lesson,lesionId,''));circuit();}}
  async function start(){try{
    const response=await fetch('lesson.json',{signal:AbortSignal.timeout(12000)});if(!response.ok)throw new Error('The selected lesson could not load');lesson=await response.json();
    $('lesion-choice').append(new Option('Preserved circuit',''));for(const item of lesson.lesions)$('lesion-choice').append(new Option(item.name,item.id));
    $('localization-choice').append(new Option('Choose a modeled localization…',''));const choices=groups(lesson);for(const item of choices)$('localization-choice').append(new Option(item.label,item.id));$('challenge-mode').disabled=!choices.length;
    $('lesion-choice').onchange=()=>{lesionId=$('lesion-choice').value;direction='';$('feedback').textContent='';paint(observation(lesson,lesionId,''));circuit();};
    $('explore-mode').onclick=()=>changeMode('explore');$('challenge-mode').onclick=()=>changeMode('challenge');
    $('gaze-left').onclick=()=>test('left');$('gaze-right').onclick=()=>test('right');$('gaze-center').onclick=()=>test('');
    $('check-localization').onclick=()=>{if(testedDirections.size<2){$('feedback').textContent='Test both leftward and rightward gaze before localizing.';return;}if(!$('localization-choice').value){$('feedback').textContent='Select a localization first.';return;}checked=true;const match=matchesGroup(lesson,lesionId,$('localization-choice').value);$('feedback').textContent=(match?'Your localization matches the modeled observation. ':'Compare with the source lesson: ')+lesion().name+'. '+lesion().evidence_note;circuit();};
    $('next-challenge').onclick=pickHidden;
    $('load-status').textContent='Loaded '+lesson.lesions.length+' authored lesion observations and '+lesson.pathways.length+' command pathways.';$('lab-controls').hidden=false;circuit();
  }catch(error){$('load-status').textContent=error.message+'; reconnect or register the local lesson again.';}}
  start();
})(typeof window!=='undefined'?window:globalThis);
