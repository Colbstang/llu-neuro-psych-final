# Agent instructions

## Scope and privacy

- Treat this folder as a public GitHub candidate. Never copy private professor-review or lecture PDFs/screenshots, book excerpts or page layers, question-bank screenshots, verbatim learning objectives, licensed Anki exports/media, personal progress, absolute workstation paths, or private URLs into tracked files.
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
