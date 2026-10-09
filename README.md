# LLU Neuro/Psych Final Study Guide

The broader [Step Study workspace](StepStudy/README.md) adds a First Aid topic skeleton, subject activation, a persistent review queue, source-grounded AI recall grading, local screenshot-question intake, and on-demand MDWiki definitions. Neuro/Psych remains the full course module; the other subjects currently provide placeholders and starter references.

This is the portable public edition of the study guide: **53 chapters, 159 reading sections, and 13 topic groups**, with a separate **Week 8 / Quiz 7** selector. Marking a section learned keeps its essential skim visible and updates the reading-length counter. Existing chapter, block, comparison and progress IDs remain stable.

The **Study** screen gives you a topic starting point with In-house, Step, or Both as the target. It shows unresolved missed questions, objectives marked bad, and actual Anki Again/Hard ratings from the last 30 days when your private data is loaded. These are separate review signals, not a mastery percentage. The current content is Neuro/Psych; expanding to the rest of Step 1 and Step 2 remains future content work.

Reference search offers **First Aid, Pathoma, Mehlman, and in-house notes/slides** as selectable families. The private source runtime opens the matched PDF page with its query highlighted. The public checkout needs your own local source pack and prepared search companion; the generic app server reports when that local search pack is unavailable.

For the small offline neural voice, run `python3 StudyApp/install_tts.py` once. It installs hash-verified Kitten Micro 0.8 assets and a separate speech runtime in private app data. **Listen** plays short terms, with eight voice choices. See [local speech setup](StudyApp/TTS.md). Native reference-window ownership has also been corrected for Swift ARC.

The public dataset contains original explanations, comparison tables, drug/bug practice and seven generated conceptual diagrams. Personal question-bank records, screenshots and progress are excluded. Verbatim course objectives, source page images, First Aid/Pathoma page layers and Anki records are optional local imports; they are not embedded in the public HTML. The author's protected local guide contains 686 answered and illustrated objectives across all eight course weeks. A metadata-only source import does not add those objective records.

Length is reported at 700 words per page equivalent, with the separate LO library counted separately when imported. Figures occupy additional visual space; these are word-based equivalents, not a print-page promise. The local Week 8 guide is 39.86 equivalents with all reading and LO answers expanded. Public text has a different count because its private source material is omitted. The live counter reflects the edition and scope actually open, including collapsed sections and saved text edits.

## Course PDF collection

The [Drive catalog](https://drive.google.com/drive/folders/1Kquft1oLerZXM3_Y-u1yrKDYfqbTNWe7) contains the original inventory and import metadata. Use the refreshed [source catalog](data/source-catalog.json) and [source import pack](data/source-import-pack.json) in this repository for the current guide. They preserve 202 stable catalog IDs, including all 195 IDs used by the latest local guide, while resolving to the same 177 unique archived PDFs. The hash inventory is [data/source-manifest.json](data/source-manifest.json).

The [source PDF collection](https://drive.google.com/drive/folders/1L6wViXDyns_N5mSrKBG3GLscBHiPp-i_) is in the owner's personal COTR.group **My Drive**, with **Anyone with the link — Viewer** access verified October 9, 2026. Its seven archives contain 177 unique PDFs, approximately 1.9 GB before compression: 147 lectures, 21 notes, two reviews, First Aid USMLE Step 1 (2024), Pathoma (2021), and five other supporting PDFs. Twenty-five duplicate copies were omitted. Every distinct course PDF used by the latest guide is already in these archives; no bulk reupload was needed. Personal questions and question screenshots are excluded. Third-party material retains its original rights; the repository's MIT license covers only the code and original commentary.

Extract the source archives together into `local-private/SourceLibrary/`, retaining their `pdfs/` paths. Copy the current repository metadata into that same directory, verify the PDFs against the hash inventory, and run:

```sh
cp data/source-catalog.json local-private/SourceLibrary/source-catalog.json
cp data/source-import-pack.json local-private/SourceLibrary/source-pack.private.json
cp data/source-manifest.json local-private/SourceLibrary/manifest.public.json
python3 build_local.py --pack local-private/SourceLibrary/source-pack.private.json
```

Then follow the local search setup below to enable exact slide viewing and book passage search. This metadata-only pack adds sources to the public guide; it does not include the personal guide's answered objectives, Anki cards, or questions. The catalog preserves existing source IDs and physical PDF page numbering (First Aid: 866 pages; Pathoma: 234 pages). Mehlman's HY Neuro, HY Neuroanatomy, and HY Psych are additional local references in the author's edition; they are separate from this archived course collection. Local packs can add them as `supplement` documents.

The current 2026 NPS 98 review packet remains an upload placeholder on the Canvas page checked October 9. Its linked recording is dated October 13, 2025 and is supplemental. The Neuroradiology 1 handout requests 16 scans across nine cases; none can be verified as that numbered case set in the available handout and 56-slide PDF. The specific serology result for infection case 8 is also absent. These gaps prevent a guarantee of exam completeness. See [audit/release-audit.json](audit/release-audit.json) for the current verification scope.

The Fragile X link was checked against both full local PDFs and the page images: First Aid 2024 has the direct entry at physical PDF page 79 / printed page 60. Full text and visual index checks found no Fragile X entry in the supplied Pathoma 2021 edition; no Pathoma page is fabricated.

## Run the public edition

Python 3.10 or newer is sufficient for the static public guide. From this folder:

```sh
python3 build_public.py
python3 -m http.server 8767 --bind 127.0.0.1
```

Open <http://localhost:8767>. The browser app supports search, learned-section compaction, skim reading, editing, comparison sheets, pathology practice, saved progress export/import, and local browser storage. No package installation or network connection is needed for the static guide.

## Run the private local app

The local app adds per-user SQLite progress and optional AnkiConnect review support without changing the public data. Its service binds only to `127.0.0.1:8770`; progress databases and Anki review ledgers live in the operating system's per-user app-data folder, outside this checkout. Start it from this folder with:

```sh
python3 study_app_server.py
```

Open <http://127.0.0.1:8770>. To build the native macOS wrapper on a Mac with Xcode command-line tools:

```sh
python3 StudyApp/native/build_app.py
```

The generated app is placed under the ignored `local-private/` directory. To move existing browser progress, use **Export progress** in the static guide, then **Import progress** in the local app. The local app keeps its own SQLite copy after import.

AnkiConnect is optional. This public edition ships with no linked Anki card library, so it reports an empty scope and cannot write ratings by default. The owner may import their own context export to store only card/note IDs and ordinals in private app data; this does not add card text or media to the guide. For example:

```sh
python3 StudyApp/import_anki_scope.py /path/to/your/anki_context_data.js
```

Restart the local app service after importing. Review writes remain limited to a matching live new or due card opened from a linked card library, with an explicit Again/Hard/Good/Easy choice. Viewing or revealing content does not schedule a review. The service does not start Anki or AnkiConnect automatically.

## Build a private local edition

Private imports are parsed as data and assembled into the same full app. They stay under the Git-ignored `local-private/` directory. The build copies referenced local source images and PDFs into `local-private/runtime/imported-sources/`, rewrites paths, and embeds imported objectives, question records, references, book page data, and Anki context into the local runtime. It also copies the exact-source and semantic-search backend modules and prepares safe defaults for the private indexes. The builder does not publish or upload those files.

For an existing personal app bundle, importing directly from its original folder lets the builder find its adjacent images, PDFs, appendix, and Anki companion. Run:

```sh
python3 build_local.py --bundle /path/to/original/index.html
```

You can also place the HTML bundle and its referenced assets under `local-private/`, name the bundle `personal-bundle.html`, and run `python3 build_local.py`.

Alternatively, put a JSON source pack at `local-private/private-pack.json` and run:

```sh
python3 build_local.py --pack local-private/private-pack.json
```

See [SOURCE_PACK_SCHEMA.md](SOURCE_PACK_SCHEMA.md) for the import shape. For either import type, use Python 3.12 or newer for the pinned search dependencies, create the local environment, and prepare the private indexes:

```sh
python3 -m venv local-private/.venv
source local-private/.venv/bin/activate
python3 -m pip install -r requirements.txt
python3 prepare_local_search.py
```

The first search setup downloads pinned model files from their official model repositories. They are stored in the ignored private runtime. After preparation, run these commands in separate terminals:

```sh
python3 -m http.server 8767 --bind 127.0.0.1 --directory local-private/runtime
```

```sh
local-private/.venv/bin/python local-private/runtime/semantic_search.py
```

Open <http://localhost:8767>. The browser talks to the local service on port 8768; queries and personal source content stay on your computer. Imported source PDFs are cataloged only from your local pack. Exact pages are available for imported PDFs; First Aid and Pathoma passage search is enabled when both local PDFs are present. Anki image references can resolve from `local-private/media/`; the builder copies only referenced files into the ignored private runtime. Source-page indexing needs Poppler (`pdfinfo`, `pdftotext`, and `pdftoppm`) on the local path.

## Data and license

`data/public-study-guide.json` is the sanitized data source used to produce the portable `index.html`. Run `python3 build_public.py --sanitize` only when preparing a newly reviewed source dataset. The checked-in audit records row counts and privacy checks without recording workstation paths or private source text: [audit/public-data-audit.json](audit/public-data-audit.json).

The MIT License in [LICENSE](LICENSE) applies to the code and original study-guide commentary in this project. It does not grant rights to third-party source material or personal imports. See [NOTICE](NOTICE).
