const test = require('node:test');
const assert = require('node:assert/strict');
const {findTerms, normalizedAlias, safeLink, createLookupService, createImageFigure} = require('./term_cards.js');
const {currentSelection} = require('./selection_lookup.js');
const records = [
  {id:'nephrotic',title:'Nephrotic syndrome',aliases:['nephrosis']},
  {id:'edema',title:'Edema',aliases:['oedema']},
  {id:'minimal-change',title:'Minimal change disease',aliases:['MCD']},
  {id:'beta',title:'Interferon beta-1a',aliases:['IFN-β-1a','IFN beta 1a']},
  {id:'gbs',title:'Guillain–Barré syndrome',aliases:['Guillain-Barre syndrome','GBS']},
  {id:'change',title:'Change',aliases:[]}
];
test('whole medical phrases win over component matches and keep original offsets', () => {
  const text = 'Minimal change disease causes edema in nephrotic syndrome.';
  assert.deepEqual(findTerms(text, records).map(m=>[m.id,text.slice(m.start,m.end)]), [['minimal-change','Minimal change disease'],['edema','edema'],['nephrotic','nephrotic syndrome']]);
});
test('never matches inside another word or identifier', () => {
  assert.equal(findTerms('preedematous nephrosis_like XYZGBS', records).length,0);
  assert.equal(findTerms('(edema); GBS.',records).length,2);
});
test('matches flexible punctuation and preserves Greek aliases', () => {
  assert.equal(findTerms('IFN-β-1a or IFN beta–1a',records).length,2);
  assert.equal(findTerms('Guillain-Barre syndrome',records)[0].id,'gbs');
  assert.equal(findTerms('Guillain–Barré syndrome',records)[0].id,'gbs');
});
test('ambiguous abbreviations do not choose a disease silently', () => {
  const data=[{id:'one',title:'First disease',aliases:['ABC']},{id:'two',title:'Second disease',aliases:['ABC']}];
  assert.deepEqual(findTerms('ABC with First disease',data).map(m=>m.id),['one']);
});
test('small ambiguous words, regex punctuation, and non-text aliases are handled', () => {
  const data=[{id:'ms',title:'Multiple sclerosis',aliases:['MS',null,'(M.S.)']}];
  assert.equal(findTerms('MS was written (M.S.)',data).length,1);
});
test('source and image URL validation excludes unsafe protocols and hosts', () => {
  assert.equal(safeLink('javascript:alert(1)'), '');
  assert.equal(safeLink('https://mdwiki.org.evil.test/wiki/Edema'), '');
  assert.equal(safeLink('https://example.com/image.png',true), '');
  assert.ok(safeLink('https://mdwiki.org/wiki/Edema'));
  assert.ok(safeLink('https://upload.wikimedia.org/wikipedia/commons/a/ab/image.png',true));
});
test('normalization supports medicine spelling and does not collapse subtypes', () => {
  assert.equal(normalizedAlias('IFN-β-1a'), normalizedAlias('ifn beta 1a'));
  assert.notEqual(normalizedAlias('IFN beta 1a'), normalizedAlias('IFN beta 1b'));
});
test('typographic apostrophes match disease names without changing their text', () => {
  const data = [{id:'parkinson',title:"Parkinson's disease",aliases:[]}];
  const text = 'Parkinson’s disease';
  assert.equal(findTerms(text,data)[0].text,text);
});
test('selection lookup reads the current range only when it belongs to the reading root', () => {
  const textNode = {}, root = {contains: node => node === textNode};
  const selection = {rangeCount: 1, getRangeAt: () => ({commonAncestorContainer: textNode}), toString: () => ' Cerebral hemisphere '};
  assert.equal(currentSelection(root, selection), 'Cerebral hemisphere');
  assert.equal(currentSelection(root, {...selection, getRangeAt: () => ({commonAncestorContainer: {}})}), '');
  assert.equal(currentSelection(root, {rangeCount: 0, toString: () => ''}), '');
});
test('lookup shares in-flight requests, memoizes successes, and retries failures', async () => {
  let calls = 0, release;
  const service = createLookupService({fetch: () => {
    calls++;
    return new Promise(resolve => { release = () => resolve({json: async () => ({ok:true,record:{id:'n',title:'Nephrotic syndrome',definition:'A syndrome.'}})}); });
  }});
  const first = service.lookup('Nephrotic syndrome');
  const same = service.lookup(' nephrotic   syndrome ');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 1);
  release();
  const [a,b] = await Promise.all([first,same]);
  assert.equal(a.record.id, 'n'); assert.equal(b.record.id, 'n');
  assert.equal((await service.lookup('n', true)).record.id, 'n');
  assert.equal(calls, 1);

  let failures = 0;
  const retrying = createLookupService({fetch: async () => { failures++; throw new Error('temporary outage'); }});
  await assert.rejects(retrying.lookup('Retry term'));
  await assert.rejects(retrying.lookup('Retry term'));
  assert.equal(failures, 2);
});
test('lookup memo rejects cached disambiguation stubs and has a strict entry cap', async () => {
  let calls = 0;
  const service = createLookupService({maxEntries:1, fetch: async url => {
    calls++;
    const id = url.includes('First') ? 'first' : 'second';
    const definition = id === 'first' ? 'First may refer to:' : 'A useful definition.';
    return {json: async () => ({ok:true,record:{id,title:id,definition}})};
  }});
  assert.equal((await service.lookup('First')).error, 'ambiguous_term');
  assert.equal(service.cacheSize(), 0);
  assert.equal((await service.lookup('Second')).ok, true);
  assert.equal(service.cacheSize(), 1);
  await service.lookup('Second');
  assert.equal(calls, 2);
  await service.lookup('First');
  assert.equal(calls, 3);
});
test('visible-term prefetch has concurrency, queue, total bounds, and cancellation', async () => {
  const calls = [];
  const service = createLookupService({maxPrefetches:2,maxConcurrentPrefetch:1,maxQueuedPrefetch:1,fetch:(url,{signal}) => new Promise((resolve,reject) => {
    calls.push(url);
    signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'),{name:'AbortError'})), {once:true});
  })});
  assert.equal(service.prefetch('first',true),true);
  assert.equal(service.prefetch('second',true),true);
  assert.equal(service.prefetch('third',true),false);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length,1);
  assert.deepEqual(service.prefetchState(),{active:1,queued:1,started:1});
  service.cancelPrefetch('first',true);
  await new Promise(resolve => setTimeout(resolve,10));
  assert.equal(calls.length,2);
  assert.equal(service.prefetch('third',true),false);
  assert.equal(service.prefetchState().started,2);
  service.cancelPrefetch('second',true);
});
test('shared image figure API keeps Wikimedia media, source, license, and credit together', () => {
  const makeNode = tagName => ({tagName,children:[],append(...children){this.children.push(...children);},addEventListener(){},remove(){}});
  const doc = {createElement:makeNode,createTextNode:text=>({textContent:text})};
  const image = {
    url:'https://upload.wikimedia.org/wikipedia/commons/a/ab/renal.jpg',
    file_url:'https://commons.wikimedia.org/wiki/File:renal.jpg',
    license:'CC BY-SA 3.0',license_url:'https://creativecommons.org/licenses/by-sa/3.0/',
    artist:'Example artist',alt:'Renal tissue',
  };
  const figure = createImageFigure({title:'Nephrotic syndrome',image},doc);
  assert.equal(figure.tagName,'figure');
  assert.equal(figure.children[0].src,'https://upload.wikimedia.org/wikipedia/commons/a/ab/renal.jpg');
  assert.equal(figure.children[0].alt,'Renal tissue');
  assert.equal(figure.children[0].loading,'lazy');
  const caption=figure.children[1];
  assert.equal(caption.children[0].textContent,'Example artist · ');
  assert.equal(caption.children[1].textContent,'CC BY-SA 3.0');
  assert.equal(caption.children[1].href,'https://creativecommons.org/licenses/by-sa/3.0/');
  assert.equal(caption.children[3].href,'https://commons.wikimedia.org/wiki/File:renal.jpg');
  assert.equal(createImageFigure({title:'Unsafe',image:{...image,url:'https://example.test/image.jpg'}},doc),null);
});
