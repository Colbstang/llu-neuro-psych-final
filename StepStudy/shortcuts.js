/* Shared shortcuts for the app and its trusted course frame. */
(function (scope) {
  'use strict';
  const resources = {'Digit1':'first_aid','Digit2':'pathoma','Digit3':'mehlman','Digit4':'in_house'};
  function action(event, selected = '') {
    if (event.defaultPrevented || event.repeat || event.isComposing) return null;
    const editable = event.target?.closest?.('input,textarea,select,[contenteditable="true"],dialog[open]');
    if (editable) return null;
    if (event.altKey && !event.metaKey && !event.ctrlKey && resources[event.code]) return {kind:'resource',family:resources[event.code]};
    if (event.metaKey || event.ctrlKey || event.altKey) return null;
    if (event.key === '?') return {kind:'help'};
    if (event.key.toLowerCase() === 's') return {kind:'search',text:String(selected).trim().slice(0,4000)};
    if (event.key.toLowerCase() === 'r') return {kind:'library'};
    if (event.key.toLowerCase() === 'b') return {kind:'back'};
    return null;
  }
  function help(document, capture = false) {
    const dialog = document.createElement('dialog');dialog.className='study-shortcut-help';
    const heading=document.createElement('h2');heading.textContent='Study shortcuts';dialog.append(heading);
    const table=document.createElement('dl');
    const rows=[['H','Highlight selected course or PDF text'],['D','Define selected text'],['S','Search selected text, or focus search'],['⌥1 / ⌥2 / ⌥3 / ⌥4','First Aid / Pathoma / Mehlman / slides & notes'],['R','Reference library'],['B','Back to reference search results'],['?','Show these shortcuts']];
    if(capture)rows.push(['⌘⇧2','Capture a selected region into the question inbox']);
    for(const [key,description] of rows){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=description;table.append(dt,dd);}dialog.append(table);
    const note=document.createElement('p');note.textContent='Reading shortcuts pause while you type. Space and 1–4 keep their Anki review actions.';dialog.append(note);
    const close=document.createElement('button');close.textContent='Close';close.onclick=()=>dialog.close();dialog.append(close);dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
    return dialog;
  }
  scope.StepShortcuts={action,help};
  if(typeof module!=='undefined'&&module.exports)module.exports=scope.StepShortcuts;
})(typeof window!=='undefined'?window:this);
