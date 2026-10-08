# Private source-pack schema

`build_local.py` reads a private JSON file and merges its fields with the public chapter/comparison dataset, then builds the same full guide in the Git-ignored `local-private/runtime/` folder. Use JSON, not executable JavaScript. HTML strings in imported content are sanitized before rendering: ordinary formatting and table markup, cloze spans, local or embedded raster images, and safe source links are retained; scripts, frames, active elements, event handlers, unsafe URL schemes, and inline styles are removed. Preserve existing source IDs and references where possible.

```json
{
  "format": "llu-neuro-private-source-pack-v1",
  "data": {
    "objectives": [],
    "objective_answers": {},
    "objective_aliases": {},
    "objective_coverage": {},
    "questions": [],
    "question_annotations": [],
    "question_auto_links": [],
    "references": {},
    "book_pages": {"books": {}, "keywords": {}},
    "book_link_focus": {"topics": {}},
    "book_text_layers": {"books": {}},
    "source_catalog": {"documents": []},
    "source_notes": {"notes": []},
    "anki_topic_media": {"topics": {}},
    "pathology_images": {},
    "review_games": {},
    "rapid_pathology": []
  },
  "anki_context": {"notes": [], "cards": [], "scope": {}, "graph": {}},
  "assets": {}
}
```

Each field is optional; omit a field to retain the public edition's safe empty default. The `data` object can also include a complete `pages` array when you intentionally want a private chapter-content build. `assets` may map IDs to data URIs. For referenced local files, use relative paths in your JSON and place the pack next to those files; the builder copies found assets under `local-private/runtime/imported-sources/` and rewrites their references. Absolute workstation paths are accepted only as local inputs; discovered files are copied into the ignored local runtime and their original paths are rewritten there.

For Anki, either include `anki_context` directly or place the companion `anki_context_data.js` beside a personal HTML bundle. `build_local.py` reads its JSON assignment without evaluating JavaScript. If your notes reference media using `Anki/media/…`, the builder looks beside the source bundle for that folder or you can place media under `local-private/media/` or pass `--media-dir PATH`; only referenced media files are copied into the private runtime.

To import a compiled personal guide bundle:

```sh
python3 build_local.py --bundle local-private/personal-bundle.html
```

To import a JSON pack:

```sh
python3 build_local.py --pack local-private/private-pack.json
```

These commands create an ignored `local-private/runtime/index.html` with the full app behavior, backend modules, and imported local sources. `prepare_local_search.py` builds the exact-source catalog and Anki index; when both First Aid and Pathoma PDFs are imported, it also builds book passage search. The first setup downloads pinned models into the ignored runtime. Source-page rendering requires Poppler. Imported material remains on your computer; inspect the resulting private data locally before use.
