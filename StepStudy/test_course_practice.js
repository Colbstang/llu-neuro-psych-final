'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const practice = require('./course_practice.js');

const cases = [
  {id: 'bug-a', traits: {'Organism type': ['Bacterium'], 'Neuro presentation': ['Meningitis', 'Brain abscess']}},
  {id: 'bug-b', traits: {'Organism type': ['Bacterium'], 'Neuro presentation': ['Meningitis']}},
  {id: 'bug-c', traits: {'Organism type': ['Virus'], 'Neuro presentation': ['Encephalitis']}},
];

test('subset filters narrow candidates by their source-backed trait values', () => {
  assert.deepEqual(practice.filterCases(cases, 'Organism type', 'Bacterium').map(record => record.id), ['bug-a', 'bug-b']);
  assert.equal(practice.filterCases(cases, '', '').length, 3);
});

test('choice lists stay bounded and include every supported answer alternative', () => {
  const target = {id: 'target', traits: {finding: ['Answer A', 'Answer B']}};
  const candidates = [target, ...Array.from({length: 30}, (_, index) => ({traits: {finding: [`Distractor ${index}`]}}))];
  const choices = practice.boundedChoices('drugs', 'finding', target, candidates, 8, () => 0);
  assert.ok(choices.length <= 8);
  assert.ok(choices.includes('Answer A'));
  assert.ok(choices.includes('Answer B'));
  assert.ok(choices.slice(0, 2).filter(value => ['Answer A', 'Answer B'].includes(value)).length < 2);
});

test('grading accepts any supported overlapping alternative without requiring all', () => {
  const grade = practice.gradeSelections('bugs', cases[0], ['Organism type', 'Neuro presentation'], {
    'Organism type': ['Bacterium'],
    'Neuro presentation': ['Meningitis'],
  });
  assert.equal(grade.correct, 2);
  assert.equal(grade.total, 2);
  assert.equal(grade.perfect, true);
});

test('unsupported picks remain incorrect while valid alternatives are credited', () => {
  const grade = practice.gradeSelections('bugs', cases[0], ['Neuro presentation'], {
    'Neuro presentation': ['Meningitis', 'Encephalitis'],
  });
  assert.equal(grade.correct, 0);
  assert.equal(grade.extra, 1);
  assert.deepEqual(grade.wrong, ['Neuro presentation: Encephalitis']);
});

test('round size returns only the requested number and supports all', () => {
  assert.equal(practice.roundCases(cases, 2, () => 0).length, 2);
  assert.equal(practice.roundCases(cases, 'all', () => 0).length, 3);
});

test('legacy unbounded game controls are removed from composed drug and bug sheets', () => {
  const html = '<section><div class="practice-modes"><button data-trait-start="bugs">Play organism characteristics</button><button data-shuffle-sheet="bugs">Shuffle</button></div><div id="trait-practice" hidden></div><label class="sheet-search">Filter</label></section>';
  const updated = practice.removeLegacyTraitControls(html, 'bugs');
  assert.equal(updated.includes('data-trait-start'), false);
  assert.equal(updated.includes('id="trait-practice"'), false);
  assert.equal(updated.includes('data-shuffle-sheet'), false);
  assert.equal(updated.includes('class="sheet-search"'), true);
});

test('organism image lookup requires a canonical organism match and never resolves ambiguity', async () => {
  let calls = 0, figureCalls = 0;
  const api = {
    normalizedAlias: value => String(value).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(),
    lookup: async (query, isId) => {
      calls++;
      assert.equal(query, 'Neisseria meningitidis');
      assert.equal(isId, false);
      return {ok: true, record: {title: 'Neisseria meningitidis', aliases: ['meningococcus']}};
    },
    createImageFigure: record => { figureCalls++; return {title: record.title}; },
  };
  const match = await practice.lookupOrganismFigure(api, 'Neisseria meningitidis', {});
  assert.equal(match.status, 'ready');
  assert.equal(calls, 1);
  assert.equal(figureCalls, 1);

  assert.equal(practice.isCanonicalOrganismMatch('Neisseria meningitidis', {title: 'Meningitis'}), false);
  const ambiguous = await practice.lookupOrganismFigure({...api, lookup: async () => ({ok: false, error: 'ambiguous_term', candidates: [{title: 'First'}, {title: 'Second'}]}), createImageFigure: () => { throw new Error('must not create'); }}, 'Neisseria meningitidis', {});
  assert.equal(ambiguous.status, 'ambiguous');
  assert.equal(ambiguous.candidates.length, 2);
});
