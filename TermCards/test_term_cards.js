const test = require('node:test');
const assert = require('node:assert/strict');
const {findTerms, normalizedAlias, safeLink} = require('./term_cards.js');
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
