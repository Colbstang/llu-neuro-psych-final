# Medical term-card pilot

A separate prototype of automatic medical vocabulary recognition and on-demand definition cards. It does not modify the Neuro/Psych guide, its progress, Anki, or the AMBOSS add-on.

From the repository root:

```sh
python3 -m TermCards.server
```

Open <http://localhost:8772>. Hover over an underlined term for a short source definition; click it to keep the definition and any available illustration beside the reading. Additional background is collapsed initially. Back/forward changes definitions without navigating or scrolling the reading. Automatic definitions can be turned off. The separate pilot preference uses the `term-cards-pilot-v1` browser key.

Try the renal, immunology, or Neuro/Psych samples, or paste your own paragraph under **Your text**. Pasted text stays in the browser and is not saved. Recognition runs locally against the supplied vocabulary. **Look up** and **Define selected text** can request an exact term from MDWiki; the app sends only that term, up to 160 characters. Ambiguous matches offer choices instead of silently selecting the first search result. This is a small vocabulary pilot, not a complete medical dictionary or Step curriculum.

Known definitions are bundled and work without a network connection. Newly requested definitions are cached in per-user app data outside the checkout and become part of the locally recognized vocabulary on later visits. MDWiki access is needed for new lookups; illustrations load from their attributed source hosts. If served with a basic static HTTP server, only the bundled definitions are available. Opening the HTML directly as a `file:` URL is not supported because the browser must load the vocabulary JSON.

`term_cards.js` exports `MedicalTermCards.attach(root, records, options)` for use in a future reading module. Options include `onOpen(id, node)`, `onHover(id, node, immediate)`, and `onScan(count, enabled)`. The scanner preserves the surrounding markup, skips existing links and interactive/editable content, and does not send the document to a remote scanner. Its returned instance supports `setEnabled`, `scan`, and `destroy`.

Text excerpts retain MDWiki article, revision, contributor-history and license attribution. Illustrations have separate author, file-page and license metadata. See [content licensing](CONTENT-LICENSE.md) and [the source audit](data/source-audit.json). The repository's code license does not replace the licenses on source text and images.

Tests:

```sh
python3 -m unittest TermCards.test_provider TermCards.test_server
node --test TermCards/test_term_cards.js
```
