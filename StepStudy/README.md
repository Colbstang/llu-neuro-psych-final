# Step Study workspace

This is the broader local studying app. Neuro/Psych remains a separate full course module; the other subjects are First Aid-style topic placeholders with background reading, activation, notes, and recall tracking. Activating a topic does not assert full Step 1 or Step 2 coverage.

## Open the app

On macOS, build the separate launcher once:

```sh
python3 StepStudy/native/build_app.py
```

Then open **Step Study.app** in your Applications folder. The launcher packages only the reviewed public code and licensed term seed, starts its local service on port 8773, and stores study state outside the checkout. It requires the installed Python interpreter; it does not download another runtime or model. It leaves LLU Study and the course module on port 8770 alone.

For development:

```sh
python3 StepStudy/server.py
```

Open <http://localhost:8773>. On macOS, state lives under `~/Library/Application Support/Step Study/`; other systems use the per-user data directory. SQLite preserves topic activation, paused history, notes, recalled answers, and preferences across restarts. Export personal progress from Settings. Screenshot records use a separate SQLite database and private image folder. Neither database belongs on GitHub.

## Studying

- **Today:** the active study pool, due concept recalls, mapped Anki signals, and unresolved imported questions. Saved Neuro/Psych objective ratings and banked-question outcomes are read from the existing local module. These remain separate evidence; missing history is unknown. Progress held only in a file-tab browser is not included; mixed course topics can appear in both Neuro and Psychiatry.
- **Topics:** activate or pause subjects. Pausing retains notes and review history.
- **Read:** sourced background concepts, images when available, persistent learned/skimming states, and notes. The live length includes closed-section skim text at 650 words per page plus an approximate image allowance; it is not a print pagination promise. In-house selects course reading; subjects without a course module say so. Step selects starter background reading. Open Neuro/Psych for the full course guide and its First Aid, Pathoma, Mehlman, and lecture/notes viewer.
- **Definitions:** recognized phrases offer hover cards and a pinned side pane. Select any unrecognized short term and choose **Define**, or press **D** with text selected. Typed search requests an article on demand from MDWiki. Resolved terms enter the local recognition cache. Ambiguous matches offer choices instead of choosing a diagnosis silently.
- **References:** drag the divider or use its arrow keys. A second reference window receives lookups through a local browser channel. Closing it leaves the study window running; later lookups return to the side pane. The new window handles medical definitions; book/slide references remain in the course module.
- **Recall:** write an explanation, then choose Grade answer. The installed, signed-in Codex CLI grades only that answer against a bounded linked source excerpt using `gpt-5.6-luna`. Failed assessments stay unscored; there is no keyword substitute. Save without grading stores an unassessed answer. Concept recall intervals are 1/3/7 days, and weak assessed answers enter the current practice queue. These intervals do not change Anki scheduling.
- **Question inbox:** choose a screenshot folder, watch new files, or explicitly scan existing ones. Apple Vision OCR runs locally; identifiable medical MCQs receive subject tags, uncertain detections await confirmation, and failed OCR can be retried. You mark outcomes yourself; the parser does not invent a correct answer. Full OCR text is searchable. This watches screenshot files and does not take desktop screenshots. Watching runs while this app’s local service is running.
- **Anki:** Sync reads reported Step/AnKing tag categories and normal review history over 30 days. It excludes suspended cards and filtered/manual/future/invalid rating events. Scope queries return mapped card totals; a subject may have a partial category scope. Due counts are verified only when all IDs in that scope fit the 5,000-card per-subject bound. Review history is sampled from at most 200 cards, prioritizing the selected and active subjects, within a 40-second overall budget. Counts and samples are labeled separately; interrupted syncs retain completed counts and show unfinished history as unknown. **Anki cards** opens a subject-scoped batch of up to 200 eligible live cards. Reveal with Space and choose Again/Hard/Good/Easy (1–4) to submit an explicit rating through the private idempotent ledger. Card identity, current eligibility, and the actual Anki review are checked. Skipping, learned flags, and recall scores never submit ratings. If a write is uncertain, checking the same request reconciles it without submitting a duplicate. Sync refreshes dashboard counts after reviews. Card text is rendered safely and images are fetched from local Anki media; card content never enters the public project.
- **Listen:** explicit term pronunciation reuses the installed Kitten Micro voice. There is no automatic system-speech fallback or new model download.

New MDWiki lookups require a network response; bundled and cached definitions work offline. See [the term content license](../TermCards/CONTENT-LICENSE.md) for attribution and image licenses. Public code excludes private books, lectures, Anki exports, question screenshots, personal notes, and progress.

## Verify

```sh
python3 -m unittest StepStudy.test_store StepStudy.test_planning StepStudy.test_anki_signals StepStudy.test_live_cards StepStudy.test_module_signals StepStudy.test_intake StepStudy.test_grading StepStudy.test_recall StepStudy.test_server
node --test StepStudy/test_step.js TermCards/test_term_cards.js
```

Tests use isolated state, synthetic screenshots, and fake Anki/grading transports. Do not rate real cards to test the integration. Run the repository’s existing StudyApp checks as well before shipping a change.
