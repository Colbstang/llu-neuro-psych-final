# Agent instructions

## Step Study workspace

- The `StepStudy/` workspace and its native launcher use port 8773. Neuro/Psych is a populated module inside its own navigation; do not replace its reading with background articles or link out to the old app as the primary module flow. Keep the original guide and LLU Study on 8770 intact. Other topics are a planning skeleton with starter background references, not a completed Step curriculum.
- SQLite progress, OCR questions/images, and MDWiki/Anki caches belong in per-user app data outside the checkout. Package only the allowlisted public runtime in the native bundle; never include personal PDFs, Anki content, questions, or state.
- Only an explicit Grade action may send one answer and its bounded reference context through the existing signed-in Codex CLI. A missing/failed assessment has no score. Recall intervals and learned flags never write Anki ratings.
- Screenshot intake watches a chosen folder while the local service runs. OCR is local; preserve source wording and keep ambiguous detections reviewable. Do not convert all screenshots into questions or capture the desktop automatically.
- Anki topic sync is read-only and bounded; show partial/cached results and unknown history explicitly. Test with fake transports, never real review writes.
- Live Step card queues require reported Step/AnKing category tags and verified note/card identities. Reuse `StudyApp.anki_bridge` for explicit ratings and idempotent private ledgers; retain unknown results as unknown. Render card text safely, mask front clozes, and use only validated local media. Never publish the live card cache or ledger.
- Load the private course through `StepStudy/course_module.py`, using a private `course-config.json` or `LLU_NEURO_PSYCH_BUNDLE`. Compose its content surface from the allowlisted `course_reader.py` runtime; never execute imported HTML/scripts. Assets use opaque IDs and bounded private roots. Use the existing LLU Study `ProgressStore` for course state, with merge-safe patches; never replace progress on startup. Derive dashboard aggregates through `module_signals.py` from the same data/store. Keep unmapped metadata and overlapping subjects explicit. Browser-only file-guide progress is not automatically migrated.
- Keep the course frame mounted across workspace navigation to preserve reading/reference position and pending notes. Parent/frame messages must check both same origin and the exact peer window. Same-origin course API routes retain Host/Origin/session-token guards; fixed reference-service routes must not become an arbitrary proxy. Public packaging includes trusted renderer code, never private course bundles or user assets.
- Run the StepStudy Python suites (store, planning, Anki signals, live cards, module signals, intake, grading, recall, server) and `node --test StepStudy/test_step.js TermCards/test_term_cards.js`, together with the existing StudyApp checks below. Verify native auxiliary-window closing and restart persistence when the Mac is available.

## Study app and recall signals

- The Study screen groups the current Neuro/Psych material by topic. In-house, Step, and Both select which evidence to prioritize; they do not imply a complete Step 1 or Step 2 curriculum. Retain the separate Week 8 scope and stable content IDs.
- Show unresolved saved wrong questions, normalized objective ratings, and actual recent Anki ratings separately. Old objective values `later` and `reviewed` mean `bad` and `good`. Missing review history is unknown; never display it as mastery. Topic links may overlap. Anki remains the scheduler.
- Recent signals count normal learn/review/relearn revlog events over 30 days. Exclude filtered/cram, manual/reschedule, invalid, and future records. SQLite upgrades must retain existing progress and cached review history.
- Reference search families are First Aid, Pathoma, Mehlman, and in-house notes/slides. Persist `referenceSearchFamilies`, propagate it to retrieval, and fail closed for an empty selection. Keep exact PDF locators and highlighted passages.
- Speech uses the pinned Kitten Micro 0.8 model through `StudyApp/tts_service.py`, with one isolated worker and bounded startup/generation. Run `StudyApp/install_tts.py` once; weights and runtime stay in private app data. Synthesis must work offline. Do not restore OS speech as an automatic fallback or start speech without an explicit Listen action.
- Native windows owned by Swift ARC must set `isReleasedWhenClosed = false`. Keep reference windows alive until their close animation finishes; closing a reference window must leave the main guide running.
- Verify using fake Anki transports, never by recording real test reviews. Run `python3 -m unittest StudyApp.test_progress_store StudyApp.test_anki_bridge StudyApp.test_app_server StudyApp.test_tts_service StudyApp.test_reference_search_families StudyApp.test_build_local`, and `node --test StudyApp/test_dashboard.js StudyApp/test_client.js StudyApp/test_reference_workspace.js`. The real voice check skips if its local model has not been installed.

## Course source library

- Start with the current `data/source-catalog.json`, `data/source-import-pack.json` and `data/source-manifest.json` in this repository. The [Drive metadata folder](https://drive.google.com/drive/folders/1Kquft1oLerZXM3_Y-u1yrKDYfqbTNWe7) retains the original catalog; use the repository versions for the latest aliases. There are 202 stable catalog records, including all 195 IDs in the current local guide, mapping to 177 unique PDFs.
- The [source PDF archives](https://drive.google.com/drive/folders/1L6wViXDyns_N5mSrKBG3GLscBHiPp-i_) are in the owner's personal COTR.group My Drive, not a shared drive. At the user's explicit request, Anyone with the link — Viewer access was applied and verified October 9, 2026. Preserve read-only access. The MIT license does not grant rights to third-party source material.
- The collection contains 177 unique PDFs: 147 lectures, 21 notes, two reviews, First Aid 2024, Pathoma 2021, and five other supporting PDFs. Personal question-bank records, screenshots, Anki exports, notes entered in the app, and progress are excluded from this collection.
- Use `data/source-manifest.json` to verify SHA-256 hashes and physical PDF page counts. Extract the archives together under `local-private/SourceLibrary/`, retaining their `pdfs/` paths. Copy `data/source-catalog.json` beside the PDFs and copy `data/source-import-pack.json` there as `source-pack.private.json`. Run `python3 build_local.py --pack local-private/SourceLibrary/source-pack.private.json` and follow the README's local search setup.
- This source pack adds PDF catalog entries only. It does not contain answered learning objectives, personal questions, Anki cards, or the full personal guide. Preserve document IDs and exact aliases when adding these sources to another local build; duplicate basenames must never select a document silently.
- Still missing from the available collection: the current 2026 NPS 98 psychiatric case-review packet and the numbered scan set for nine Neuroradiology 1 cases (16 scan references). Generic lecture examples do not establish a match to those numbered scans. A prior-year review or generated drawing cannot establish the contents of missing course assets.
- Organize the full guide by its 13 topic groups; retain the Week 8 / Quiz 7 filter and stable IDs. Count collapsed skim text in the live reading-length display. The protected author's local edition has 686 answered objectives across all eight weeks; the public metadata-only pack does not include those records. Never claim that PDF import alone populates the LO library.
- When the user is actively studying, do not rebuild or edit their current guide, source-map inputs, browser ratings, notes or progress. Prepare changes in a separate checkout or staging directory until the user requests applying them.
- Fragile X is a direct First Aid 2024 reference at physical PDF 79 / printed 60. The supplied Pathoma 2021 full text and visual index have no Fragile X entry. Distinguish absent coverage from failed OCR; verify exact pages instead of inventing a Pathoma link.

## Scope and privacy

- Treat this folder as a public GitHub candidate. Never copy private professor-review or lecture PDFs/screenshots, book excerpts or page layers, question-bank screenshots, verbatim learning objectives, licensed Anki exports/media, personal progress, absolute workstation paths, or private URLs into tracked files.
- The portable local app lives in `StudyApp/` and `study_app_server.py`; build public HTML only from `data/public-study-guide.json`. The service binds to loopback port 8770, and progress/Anki ledgers belong in the user's per-user app-data directory, never in this checkout.
- Keep the public Anki library empty unless public-use rights are verified. A user may explicitly import a local Anki export with `StudyApp/import_anki_scope.py`; the allowlist is private and contains only card/note identity fields. Anki is optional, reviews require a live due/new card and an explicit rating, and no progress flag may schedule or submit a review.
- Keep personal source packs, imported assets, runtime bundles, semantic indexes, and downloaded models under `local-private/` or another Git-ignored location. Do not weaken the ignore rules to make private files easier to commit.
- Preserve the real app behavior and use `build_public.py` to rebuild `index.html` from the reviewed public dataset. Private material belongs in the local `build_local.py` workflow; never place private content in public data or runtime builds.
- Imported HTML and JavaScript are untrusted. Extract only JSON assignments; never execute imported scripts or render raw imported HTML.

## Source priority and verification

1. Follow the user's explicit instructions and scope.
2. For course-specific details, prefer the professor's course guide/doctor's notes and actual lecture or class notes. Check the exact source page when available. Treat Anki cards as a secondary cross-check for recall coverage, never as the sole evidence for a factual change.
3. Use reliable, current references to verify general clinical facts when the course material is silent or ambiguous. Mark uncertain points instead of filling gaps from model memory alone.
4. Preserve provenance in private records, but do not publish restricted excerpts, source page images, or attribution metadata that exposes a private source location.
5. Before keeping an image or citation, verify that the exact asset matches the depicted concept, its provenance is known, and it is permitted in the public project. Never infer reuse rights from a filename.

## Stable data and state

- Preserve chapter IDs, block IDs, comparison sheet IDs, row order, and all state-key IDs. Browser progress and edits depend on those identifiers; do not regenerate IDs from titles or array positions.
- Keep `layout.chapter_order` aligned with `pages`, and block order aligned within each chapter. Preserve skim text when present.
- For new public material, assign a stable unique ID once and record provenance and reuse rights in the audit.

## Bulk work and review

- For moderately large data-processing tasks, especially Anki, image, or source inventories, delegate independent extraction and organization to lower-cost agents when reasonable. For non-trivial coding, make a short plan and delegate separable work when reasonable. Review every delegated result before merging it.
- Keep changes within the requested scope. Label optional suggestions explicitly and do not silently include them.
- Before handoff, validate JSON, stable-ID counts, generated image references, local build behavior, and scans for private paths, private URLs, copyrighted excerpts, and retained question-bank details. Preview the app when practical.
