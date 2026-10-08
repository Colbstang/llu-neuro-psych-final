# Agent instructions

## Course source library

- Start with the [public source catalog and import metadata](https://drive.google.com/drive/folders/1Kquft1oLerZXM3_Y-u1yrKDYfqbTNWe7). The same inventory is checked in at `data/source-manifest.json`.
- The [complete source PDF archives](https://drive.google.com/drive/folders/1L6wViXDyns_N5mSrKBG3GLscBHiPp-i_) are stored in the owner's personal COTR.group My Drive, not a shared drive. As of October 8, 2026, the archives are restricted while public redistribution permission is unresolved. Do not describe them as publicly downloadable or change their access without resolving that permission.
- The collection contains 177 unique PDFs: 147 lectures, 21 notes, two reviews, First Aid 2024, Pathoma 2021, and five other supporting PDFs. Personal question-bank records, screenshots, Anki exports, notes entered in the app, and progress are excluded from this collection.
- Use `manifest.public.json` to verify SHA-256 hashes and physical PDF page counts. Extract authorized archives together, preserving the `pdfs/` folders, beside `source-pack.private.json` under `local-private/SourceLibrary/`. Run `python3 build_local.py --pack local-private/SourceLibrary/source-pack.private.json` and follow the README's local search setup.
- This source pack adds PDF catalog entries only. It does not contain answered learning objectives, personal questions, Anki cards, or the full personal guide. Preserve document IDs and exact aliases when adding these sources to another local build; duplicate basenames must never select a document silently.
- Still missing from the available collection: the current 2026 NPS 98 psychiatric case-review packet and nine Neuroradiology 1 case scan panels. A prior-year review or generated drawing cannot establish the contents of those missing course assets.

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
