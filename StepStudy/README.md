# Step Study workspace

This is the broader local studying app. Neuro/Psych is its populated course module, with reading, answered objectives, questions, images, and comparison sheets in the app's own navigation. The other subjects are First Aid-style topic placeholders with background reading, activation, notes, and recall tracking. Activating a topic does not assert full Step 1 or Step 2 coverage.

## Open the app

On macOS, build the separate launcher once:

```sh
python3 StepStudy/native/build_app.py
```

Then open **Step Study.app** in your Applications folder. The launcher packages the reviewed runtime and public data, starts its local service on port 8773, and stores study state outside the checkout. It requires the installed Python interpreter; it does not download another runtime or model. It leaves the original guide and LLU Study service on port 8770 intact.

For development:

```sh
python3 StepStudy/server.py
```

Open <http://localhost:8773>. On macOS, state lives under `~/Library/Application Support/Step Study/`; other systems use the per-user data directory. SQLite preserves topic activation, paused history, notes, recalled answers, and preferences across restarts. Export personal progress from Settings. Screenshot records use a separate SQLite database and private image folder. Neither database belongs on GitHub.

To load a populated private course, save `course-config.json` in that private app-data directory:

```json
{"bundle_path":"/absolute/path/to/private/index.html"}
```

Alternatively set `LLU_NEURO_PSYCH_BUNDLE` for development. The adapter extracts passive JSON and serves known images through opaque local asset IDs; it never executes scripts from an imported bundle. Without a configured bundle it can read the existing loopback module, or fall back to the public dataset. A configured bundle is required to load successfully; a missing or invalid bundle is reported instead of silently showing different course content. Public data excludes the private questions, books, lectures, and Anki export. Exact PDF pages and sentence search use the existing indexed reference service on 8768.

For automatic recovery of an existing reference installation, save `reference-service.json` beside the private app state with `python_path`, `script_path` (the installed `semantic_search.py`), and `reference_root`. Startup reuses a healthy local service or launches those explicitly configured files. It does not install dependencies. Completed searches and PDF pages have bounded session caches; opening a PDF preloads its next page. Failed requests remain retryable.

Course progress uses the existing **LLU Study** SQLite store; the broader topic/recall workspace uses the **Step Study** store. This retains course notes, objective ratings, learned sections, book highlights, and question outcomes without replacing them on startup. File-tab browser progress can still be imported explicitly through the course toolbar.

## Studying

- **Today:** the active study pool, due concept recalls, mapped Anki signals, and unresolved imported questions. Saved Neuro/Psych objective ratings and banked-question outcomes come from the integrated module's shared progress store. Queue actions open its LOs or questions inside this app. These remain separate evidence; missing history is unknown. Progress held only in a file-tab browser is not included; mixed course topics can appear in both Neuro and Psychiatry.
- **Topics:** activate or pause subjects. Pausing retains notes and review history. Subjects without a populated course show **Not generated yet** in the catalog and reading view.
- **Read:** Neuro/Psych opens the actual course guide, grouped by topic with a Week 8 / Quiz 7 filter. Learned sections retain their skim, and the live word-based page equivalent shrinks as sections collapse. The LOs, course questions, pathology, drugs, and bugs use the same module and stable IDs. The other subjects provide starter background concepts, learned/skimming states, and notes; their expanded curriculum remains a placeholder.
- **Definitions:** recognized phrases offer hover cards and a pinned side pane. Select any unrecognized short term and choose **Define**, or press **D** with text selected. Typed search requests an article on demand from MDWiki. Resolved terms enter the local recognition cache. Ambiguous matches offer specific meanings; disambiguation stubs are excluded from definitions. Licensed lead images appear when the source supplies usable attribution. Nearby recognized terms prefetch in a bounded queue, and duplicate lookups share a request.
- **References:** First Aid, Pathoma, Mehlman, in-house slides/notes, Anki images, and definitions open beside the integrated guide. **Back to search results** restores the previous matches, query, source filters, and results position. **Reference library** opens the book catalog and search starting page. Drag the divider or use its arrow keys. Exact citations retain PDF page navigation and highlights. The course reference toolbar can detach its viewer into a second tab. Closing it leaves the study window running; later lookups return to the side pane.
- **Recall:** write an explanation, then choose Grade answer. The installed, signed-in Codex CLI grades only that answer against a bounded linked source excerpt using `gpt-5.6-luna`. Failed assessments stay unscored; there is no keyword substitute. Save without grading stores an unassessed answer. Concept recall intervals are 1/3/7 days, and weak assessed answers enter the current practice queue. These intervals do not change Anki scheduling.
- **Drug and bug practice:** choose a subset, categories to practice, and rounds of 5, 10, 20, or all subset entries. Each category has at most eight choices, including supported alternatives; the comparison table stays hidden during a round. After checking, reveal the cited source and any supplied figure. Bugs also offer an explicit licensed organism-image lookup when an exact source match is available.
- **Question inbox:** choose a screenshot folder, watch new files, scan existing ones, or choose **Capture question** for the macOS region picker. The native **Study** menu also provides **⌘⇧2**; Escape cancels capture. Apple Vision OCR runs locally; identifiable medical MCQs receive multiple subject and chapter links with reviewable evidence. Linked questions appear beside their chapters. Uncertain detections await confirmation, and failed OCR can be retried. You mark outcomes yourself; the parser does not invent a correct answer. Full OCR text is searchable. Folder watching runs while this app’s local service is running.
- **Interactive labs:** register a local HTML entry point or an existing native `.app`. HTML/JavaScript labs load in an isolated frame with local assets and no network or app-data access. Model files such as GLB can be used by a local HTML viewer; there is no standalone model viewer. Native Unity labs open in their own window. Registrations and lab content stay private.
- **Shortcuts:** **H** highlights selected reading/PDF text, **D** defines it, **S** searches it or focuses search, **R** opens the reference library, and **B** returns to reference results. **⌥1–4** search First Aid, Pathoma, Mehlman, and slides/notes respectively. **?** opens the shortcut list. Reading shortcuts pause in text inputs; existing Anki Space and 1–4 actions remain available.
- **Anki:** Sync reads reported Step/AnKing tag categories and normal review history over 30 days. It excludes suspended cards and filtered/manual/future/invalid rating events. Scope queries return mapped card totals; a subject may have a partial category scope. Due counts are verified only when all IDs in that scope fit the 5,000-card per-subject bound. Review history is sampled from at most 200 cards, prioritizing the selected and active subjects, within a 40-second overall budget. Counts and samples are labeled separately; interrupted syncs retain completed counts and show unfinished history as unknown. **Anki cards** opens a subject-scoped batch of up to 200 eligible live cards. Reveal with Space and choose Again/Hard/Good/Easy (1–4) to submit an explicit rating through the private idempotent ledger. Card identity, current eligibility, and the actual Anki review are checked. Skipping, learned flags, and recall scores never submit ratings. If a write is uncertain, checking the same request reconciles it without submitting a duplicate. Sync refreshes dashboard counts after reviews. Card text is rendered safely and images are fetched from local Anki media; card content never enters the public project.
- **Listen:** explicit term pronunciation reuses the installed Kitten Micro voice. There is no automatic system-speech fallback or new model download.

New MDWiki lookups require a network response; bundled and cached definitions work offline. See [the term content license](../TermCards/CONTENT-LICENSE.md) for attribution and image licenses. Public code excludes private books, lectures, Anki exports, question screenshots, personal notes, and progress.

## Verify

```sh
python3 -m unittest discover -s StepStudy -p 'test_*.py'
node --test StepStudy/test_*.js TermCards/test_term_cards.js
```

Tests use isolated state, synthetic screenshots, and fake Anki/grading transports. Do not rate real cards to test the integration. Run the repository’s existing StudyApp checks as well before shipping a change.
