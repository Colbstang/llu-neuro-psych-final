"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const project = path.resolve(__dirname, "..");

function makeDashboard() {
  const pages = [
    { id: "p1", week: 8, title: "Alpha", keywords: ["Alpha"], blocks: [
      { id: "a1", title: "Alpha facts", html: "" },
    ] },
    { id: "p2", week: 3, title: "Beta", keywords: ["Beta"], blocks: [
      { id: "b1", title: "Beta facts", html: "" },
    ] },
    { id: "p3", week: 8, title: "Gamma", keywords: ["Gamma"], blocks: [
      { id: "g1", title: "Gamma facts", html: "" },
    ] },
    { id: "p4", week: 8, title: "Delta", keywords: ["Delta"], blocks: [
      { id: "d1", title: "Delta facts", html: "" },
    ] },
  ];
  const groups = [
    { id: "alpha", title: "Alpha and beta", short_title: "Alpha", page_ids: ["p1", "p2"] },
    { id: "beta", title: "Beta and gamma", short_title: "Beta", page_ids: ["p2", "p3"] },
    { id: "delta", title: "Delta", short_title: "Delta", page_ids: ["p4"] },
  ];
  const questions = [
    { id: "q-shared", source_status: "Wrong", suggested_sections: [] },
    { id: "q-reviewed", source_status: "Wrong", suggested_sections: [] },
    { id: "q-right", source_status: "Correct", suggested_sections: [] },
    { id: "q-week8", source_status: "Wrong", suggested_sections: [] },
  ];
  const objectives = [
    { id: "lo-a-bad-1", sections: ["a1"] },
    { id: "lo-a-bad-2", sections: ["a1"] },
    { id: "lo-a-bad-3", sections: ["a1"] },
    { id: "lo-a-good", sections: ["a1"] },
    { id: "lo-a-legacy-later", sections: ["a1"] },
    { id: "lo-a-legacy-reviewed", sections: ["a1"] },
    { id: "lo-b-bad", sections: ["b1"] },
    { id: "lo-g-good", sections: ["g1"] },
    { id: "lo-d-unrated", sections: ["d1"] },
  ];
  const notes = [100, 200, 300, 301, 500, 999].map(id => ({
    id,
    quizTags: [],
    courseTags: [],
    resourceTags: [],
  }));
  const cards = [
    { id: "101", note: 100 },
    { id: "102", note: 200 },
    { id: "103", note: 300 },
    { id: "104", note: 301 },
    { id: "105", note: 500 },
    { id: "106", note: 999 },
  ];
  const ankiTopicMap = {
    Alpha: { noteIds: [100] },
    Beta: { noteIds: [200, 200] },
    Gamma: { noteIds: [300, 301] },
    Delta: { noteIds: [500] },
  };
  const state = {
    page: 0,
    studyScope: "all",
    studyTarget: "both",
    good: { a1: true },
    questions: {
      "q-shared": { review: "later", sections: ["a1", "b1"] },
      "q-reviewed": { review: "reviewed", sections: ["a1"] },
      "q-right": { review: "later", sections: ["a1"] },
      "q-week8": { review: "later", sections: ["g1"] },
    },
    objectives: {
      "lo-a-bad-1": { review: "bad" },
      "lo-a-bad-2": { review: "bad" },
      "lo-a-bad-3": { review: "bad" },
      "lo-a-good": { review: "good" },
      "lo-a-legacy-later": { review: "later" },
      "lo-a-legacy-reviewed": { review: "reviewed" },
      "lo-b-bad": { review: "bad" },
      "lo-g-good": { review: "good" },
    },
  };
  const DATA = {
    topic_groups: { groups },
    week8: { enabled: true, include_page_ids: [], objective_ids: [
      "lo-a-bad-1", "lo-a-bad-2", "lo-a-bad-3", "lo-a-good", "lo-a-legacy-later",
      "lo-a-legacy-reviewed", "lo-g-good", "lo-d-unrated",
    ], supporting_objective_ids: [], question_ids: ["q-week8"] },
    final: { enabled: false },
    objectives,
    questions,
    question_auto_links: [],
    anki_topic_media: { topics: ankiTopicMap },
  };
  const coverage = {
    summary: { synced_at: 123, recent_window_days: 30 },
    cards: {
      "101": { review_count: 4, recent_review_count: 3, recent_again_count: 0, recent_hard_count: 1 },
      "102": { review_count: 5, recent_review_count: 2, recent_again_count: 1, recent_hard_count: 1 },
      "103": { review_count: 7, recent_review_count: 5, recent_again_count: 5, recent_hard_count: 0 },
      // Legacy cache rows carry null signals and must remain unknown.
      "104": { review_count: 2, recent_review_count: null, recent_again_count: null, recent_hard_count: null },
      // Card 105 intentionally has no history record at all.
    },
  };
  const guide = { innerHTML: "" };
  const listeners = [];
  const context = vm.createContext({
    console,
    DATA,
    pages,
    state,
    document: { addEventListener: (_kind, callback) => listeners.push(callback) },
    ankiLibrary: { cards, notes },
    ankiCards: new Map(cards.map(card => [Number(card.id), card])),
    ankiNotes: new Map(notes.map(note => [Number(note.id), note])),
    ankiTopicMap,
    ankiCanonicalAliases: {},
    ankiDetectTopics(text) {
      return Object.keys(ankiTopicMap).filter(topic => String(text).includes(topic));
    },
    ankiPlain: value => String(value || "").replace(/<[^>]*>/g, " "),
    scopeQuestion: undefined,
    studyAppCoverage: coverage,
    studyAppConfig: null,
    studyAppSync: async () => true,
    studyDashboardSyncPending: false,
    studyDashboardSyncMessage: "",
    currentView: "dashboard",
    esc: value => String(value).replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[char]),
    $(selector) { assert.equal(selector, "#guide"); return guide; },
    guideReadingCounts: blocks => ({ full: blocks.length * 10, current: blocks.length * 8, skim: blocks.length * 3 }),
    guideLengthCounts: () => ({ current: 80, full: 100, skim: 40, unit: 10 }),
    navigateView() {},
    save() {},
  });
  context.window = {};
  context.state = state;
  context.DATA = DATA;
  context.pages = pages;
  context.scopeQuestion = undefined;
  context.DATA.week8.enabled = true;

  // Align IDs used by the browser maps with the card records. The production
  // map is keyed numerically; string IDs are intentional in the signal cache.
  context.ankiLibrary = { cards, notes };
  context.ankiCards = new Map(cards.map(card => [Number(card.id), card]));
  context.ankiNotes = new Map(notes.map(note => [Number(note.id), note]));

  vm.runInContext(fs.readFileSync(path.join(project, "study_scope.js"), "utf8"), context,
    { filename: "study_scope.js" });
  vm.runInContext(fs.readFileSync(path.join(project, "study_dashboard.js"), "utf8"), context,
    { filename: "study_dashboard.js" });
  return { context, state, coverage, guide, listeners };
}

test("topic signals deduplicate within a group and allow overlap across groups", () => {
  const { context } = makeDashboard();
  const rows = context.studyDashboardRows();
  const alpha = rows.find(row => row.id === "alpha");
  const beta = rows.find(row => row.id === "beta");

  assert.deepEqual(Array.from(alpha.cardIds).sort(), ["101", "102"]);
  assert.deepEqual(Array.from(beta.cardIds).sort(), ["102", "103", "104"]);
  assert.equal(alpha.cardIds.filter(id => id === "102").length, 1);
  assert.equal(beta.cardIds.filter(id => id === "102").length, 1);
  assert.ok(alpha.cardIds.includes("102") && beta.cardIds.includes("102"));
  assert.ok(rows.every(row => !row.cardIds.includes("106")));

  // The same unresolved saved question can inform each overlapping group, but
  // duplicate topic paths cannot make it count twice inside one group.
  assert.deepEqual(Array.from(alpha.wrong, question => question.id), ["q-shared"]);
  assert.deepEqual(Array.from(beta.wrong, question => question.id), ["q-shared", "q-week8"]);
  assert.equal(new Set(alpha.wrong.map(question => question.id)).size, alpha.wrong.length);
  assert.equal(new Set(beta.wrong.map(question => question.id)).size, beta.wrong.length);
});

test("saved review state distinguishes unresolved wrongs and only bad objective ratings", () => {
  const { context } = makeDashboard();
  const rows = context.studyDashboardRows();
  const alpha = rows.find(row => row.id === "alpha");
  const beta = rows.find(row => row.id === "beta");

  assert.equal(alpha.wrong.some(question => question.id === "q-reviewed"), false);
  assert.equal(alpha.wrong.some(question => question.id === "q-right"), false);
  assert.deepEqual(Array.from(alpha.bad, objective => objective.id), [
    "lo-a-bad-1", "lo-a-bad-2", "lo-a-bad-3", "lo-a-legacy-later", "lo-b-bad",
  ]);
  assert.deepEqual(Array.from(alpha.unrated, objective => objective.id), []);
  assert.deepEqual(Array.from(beta.bad, objective => objective.id), ["lo-b-bad"]);
  assert.deepEqual(Array.from(beta.los, objective => objective.id), ["lo-b-bad", "lo-g-good"]);
  assert.equal(beta.unrated.length, 0);
  assert.equal(rows.find(row => row.id === "delta").unrated.length, 1);
});

test("objective topic navigation returns only objectives connected to that topic's blocks", () => {
  const { context } = makeDashboard();
  vm.runInContext(fs.readFileSync(path.join(project, "objective_ui.js"), "utf8"), context,
    { filename: "objective_ui.js" });
  context.objectiveCard = objective => `<article data-test-objective="${objective.id}"></article>`;

  function topicObjectiveIds(topic) {
    const list = { innerHTML: "" }, count = { textContent: "" };
    context.objectiveList = list;
    context.objectiveCount = count;
    vm.runInContext(`objectiveTopic=${JSON.stringify(topic)}; objectiveView='all'; objectiveSearch=''; objectiveSection=''; objectiveQuiz='';
      renderObjectiveList({querySelector(selector){return selector==='#objective-list'?objectiveList:objectiveCount;}});`, context);
    return [...list.innerHTML.matchAll(/data-test-objective="([^"]+)"/g)].map(match => match[1]);
  }

  assert.deepEqual(topicObjectiveIds("alpha"), [
    "lo-a-bad-1", "lo-a-bad-2", "lo-a-bad-3", "lo-a-good", "lo-a-legacy-later",
    "lo-a-legacy-reviewed", "lo-b-bad",
  ]);
  assert.deepEqual(topicObjectiveIds("beta"), ["lo-b-bad", "lo-g-good"]);
});

test("week 8 scope limits pages, questions, objectives, and direct topic-linked cards", () => {
  const { context, state } = makeDashboard();
  state.studyScope = "week8";
  const rows = context.studyDashboardRows();
  const alpha = rows.find(row => row.id === "alpha");
  const beta = rows.find(row => row.id === "beta");

  assert.deepEqual(Array.from(alpha.chapters, page => page.id), ["p1"]);
  assert.deepEqual(Array.from(beta.chapters, page => page.id), ["p3"]);
  assert.deepEqual(Array.from(alpha.cardIds).sort(), ["101"]);
  assert.deepEqual(Array.from(beta.cardIds).sort(), ["103", "104"]);
  assert.deepEqual(Array.from(alpha.wrong, question => question.id), ["q-shared"]);
  assert.deepEqual(Array.from(beta.wrong, question => question.id), ["q-week8"]);
  assert.deepEqual(Array.from(alpha.los, objective => objective.id), [
    "lo-a-bad-1", "lo-a-bad-2", "lo-a-bad-3", "lo-a-good", "lo-a-legacy-later",
    "lo-a-legacy-reviewed",
  ]);
  assert.deepEqual(Array.from(beta.los, objective => objective.id), ["lo-g-good"]);
});

test("missing and legacy Anki signals stay unknown, and target changes priority ordering", () => {
  const { context, state, guide } = makeDashboard();
  let rows = context.studyDashboardRows();
  const alpha = rows.find(row => row.id === "alpha");
  const beta = rows.find(row => row.id === "beta");
  const delta = rows.find(row => row.id === "delta");

  assert.equal(alpha.known, 2);
  assert.equal(alpha.again, 1);
  assert.equal(beta.known, 2); // card 104's null signal is not counted
  assert.equal(beta.again, 6);
  assert.equal(delta.known, 0); // no record is unknown, not confident zero recall
  assert.equal(delta.again, 0);

  state.studyTarget = "in_house";
  rows = context.studyDashboardRows().sort((a, b) => b.priority - a.priority);
  assert.equal(rows[0].id, "alpha");
  assert.equal(rows.find(row => row.id === "alpha").priority, 6);
  assert.equal(rows.find(row => row.id === "beta").priority, 3);

  state.studyTarget = "step";
  rows = context.studyDashboardRows().sort((a, b) => b.priority - a.priority);
  assert.equal(rows[0].id, "beta");
  assert.equal(rows.find(row => row.id === "beta").priority, 8);
  assert.equal(rows.find(row => row.id === "alpha").priority, 2);

  state.studyTarget = "both";
  context.renderStudyDashboard();
  assert.match(guide.innerHTML, /—<\/b> Again<small>not synced<\/small>/);
  assert.match(guide.innerHTML, /data-dashboard-los="alpha"/);
  assert.match(guide.innerHTML, /data-dashboard-missed="beta"/);
});
