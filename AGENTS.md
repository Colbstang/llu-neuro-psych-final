# Agent instructions

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
