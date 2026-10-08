# LLU Neuro/Psych Final Study Guide

This is a portable edition of the local-first study guide. It keeps the original 52 chapters, 156 explanatory blocks, skim mode, comparison tables, pathology practice, objectives, questions, Anki context, source viewer, book highlights, quick drawing, and saved progress features. Chapter, block, and comparison IDs remain stable so local progress can move between builds.

The public dataset contains original explanatory text and seven generated conceptual diagrams, including four pathology illustrations. It excludes private learning-objective wording, question-bank records and screenshots, licensed Anki exports/media, book excerpts and PDF layers, course/lecture screenshots, personal URLs and paths, and saved progress. The optional objectives, questions, source references, book pages, and Anki data fields remain available to a private local build.

## Run the public edition

Python 3.10 or newer is sufficient for the static public guide. From this folder:

```sh
python3 build_public.py
python3 -m http.server 8767 --bind 127.0.0.1
```

Open <http://localhost:8767>. The browser app supports search, learned-section compaction, skim reading, editing, comparison sheets, pathology practice, saved progress export/import, and local browser storage. No package installation or network connection is needed for the static guide.

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
