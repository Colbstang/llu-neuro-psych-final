// Freehand marks use a stable content key and normalized points. The PDF/image
// surface keeps the same coordinates when it is resized or zoomed.
state.freehandStrokes ||= {};
let freehandOn=false,freehandDrag=null,freehandFrame=0;
const SVG_NS='http://www.w3.org/2000/svg';
function strokePath(points){return points.map((p,i)=>`${i?'L':'M'}${p[0].toFixed(3)},${p[1].toFixed(3)}`).join(' ')}
function paintStroke(svg,stroke){const path=document.createElementNS(SVG_NS,'path');path.setAttribute('d',strokePath(stroke.points));path.setAttribute('class','freehand-stroke');path.setAttribute('vector-effect','non-scaling-stroke');path.dataset.strokeId=stroke.id;svg.append(path);return path}
function freehandSurface(key,surface){
 if(surface.querySelector(':scope > .freehand-overlay'))return;
 surface.classList.add('freehand-surface');surface.dataset.freehandKey=key;
 const svg=document.createElementNS(SVG_NS,'svg');svg.setAttribute('viewBox','0 0 100 100');svg.setAttribute('preserveAspectRatio','none');svg.setAttribute('class','freehand-overlay');svg.setAttribute('aria-label','Saved freehand highlights');
 (state.freehandStrokes[key]||[]).forEach(s=>paintStroke(svg,s));surface.append(svg);surface.classList.toggle('freehand-active',freehandOn);
}
function prepareFreehandSurfaces(){
 document.querySelectorAll('.book-page-canvas').forEach(s=>{const f=s.closest('[data-book-page-figure]');if(f)freehandSurface(`book:${f.dataset.book}:${f.dataset.page}`,s)});
 document.querySelectorAll('.editable:not(.book-text-layer):not(.book-fallback-text)').forEach(el=>{
  if(el.closest('.freehand-surface'))return;
  const wrap=document.createElement('div');wrap.className='freehand-text-wrap';el.before(wrap);wrap.append(el);freehandSurface(`text:${el.dataset.edit}`,wrap);
 });
 document.querySelectorAll('.figure-open,.objective-image,.path-image,.panel-media>img').forEach(el=>{
  if(el.closest('.book-page-canvas,.freehand-surface'))return;
  const im=el.matches('img')?el:el.querySelector('img');if(!im)return;
  let key=el.dataset.openFigure||el.dataset.loImage||el.dataset.pathImage;
  if(!key){const src=im.getAttribute('src'),asset=embeddedMediaIDs.get(src);key=asset?'asset:'+(DATA.asset_keys?.[asset]||asset):src;if(asset){const oldKey='image:'+DATA.assets[asset],newKey='image:'+key;if(!state.freehandStrokes[newKey]&&state.freehandStrokes[oldKey])state.freehandStrokes[newKey]=state.freehandStrokes[oldKey]}}
  const wrap=document.createElement('div');wrap.className='freehand-image-wrap';el.before(wrap);wrap.append(el);freehandSurface(`image:${key}`,wrap);
 });
}
function setFreehand(on){
 freehandOn=on;
 if(on&&typeof setBookMarkMode==='function')setBookMarkMode(false);
 const toggle=$('#freehand-toggle');toggle.setAttribute('aria-pressed',String(on));toggle.textContent=on?'Freehand on':'Freehand';
 document.body.classList.toggle('freehand-mode',on);document.querySelectorAll('.freehand-surface').forEach(s=>s.classList.toggle('freehand-active',on));
 $('#freehand-controls').hidden=!on;prepareFreehandSurfaces();
}
function freehandPoint(event,box){return [Math.max(0,Math.min(100,(event.clientX-box.left)/box.width*100)),Math.max(0,Math.min(100,(event.clientY-box.top)/box.height*100))]}
const freehandControls=document.createElement('div');freehandControls.id='freehand-controls';freehandControls.className='freehand-controls';freehandControls.hidden=true;
freehandControls.innerHTML='<span>Draw to highlight</span><button data-freehand-undo>Undo last stroke</button><button data-freehand-done>Text mode</button>';document.body.append(freehandControls);
$('#freehand-toggle').addEventListener('click',()=>setFreehand(!freehandOn));
document.addEventListener('pointerdown',e=>{
 const svg=e.target.closest?.('.freehand-overlay');if(!freehandOn||!svg||e.button!==0)return;
 const surface=svg.closest('[data-freehand-key]'),box=svg.getBoundingClientRect(),points=[freehandPoint(e,box)],stroke={id:`stroke-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,7)}`,points};
 freehandDrag={svg,key:surface.dataset.freehandKey,box,stroke,path:paintStroke(svg,stroke),pointerId:e.pointerId};svg.setPointerCapture?.(e.pointerId);e.preventDefault();e.stopPropagation();
});
document.addEventListener('pointermove',e=>{
 if(!freehandDrag||e.pointerId!==freehandDrag.pointerId)return;
 const d=freehandDrag,p=freehandPoint(e,d.box),last=d.stroke.points.at(-1);if(Math.hypot(p[0]-last[0],p[1]-last[1])<.12)return;
 d.stroke.points.push(p);d.path.setAttribute('d',strokePath(d.stroke.points));e.preventDefault();
});
function endFreehand(e){
 if(!freehandDrag||e.pointerId!==freehandDrag.pointerId)return;const d=freehandDrag;freehandDrag=null;
 if(d.stroke.points.length<2){d.path.remove();return}
 (state.freehandStrokes[d.key]||=[]).push(d.stroke);state.freehandLastKey=d.key;save();
}
document.addEventListener('pointerup',endFreehand);document.addEventListener('pointercancel',endFreehand);
document.addEventListener('click',e=>{
 if(e.target.closest('[data-freehand-done]'))setFreehand(false);
 if(e.target.closest('[data-freehand-undo]')){const key=state.freehandLastKey,list=state.freehandStrokes[key]||[],last=list.pop();if(last){document.querySelectorAll('[data-stroke-id]').forEach(p=>{if(p.dataset.strokeId===last.id)p.remove()});save()}}
 if(e.target.closest('#book-mark-toggle')&&freehandOn)setFreehand(false);
});
const freehandObserver=new MutationObserver(()=>{if(freehandFrame)return;freehandFrame=requestAnimationFrame(()=>{freehandFrame=0;prepareFreehandSurfaces()})});
freehandObserver.observe($('#guide'),{childList:true,subtree:true});freehandObserver.observe($('#side-content'),{childList:true,subtree:true});
