import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from TermCards.provider import CACHE_MAX_BYTES, CACHE_MAX_ENTRIES, TermProvider, normalize_term


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def read(self, limit):
        return self.body[:limit]

    def close(self):
        pass


def page(title="Nephrotic syndrome", extract="A kidney disorder.", revid=77, pageprops=None):
    row = {"title": title, "fullurl": "https://mdwiki.org/wiki/" + title.replace(" ", "_"),
           "extract": extract, "revisions": [{"revid": revid, "timestamp": "2026-01-01T00:00:00Z"}]}
    if pageprops:
        row["pageprops"] = pageprops
    return {"query": {"pages": [row]}}


def missing():
    return {"query": {"pages": [{"title": "Requested title", "missing": True}]}}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.seed = self.root / "terms.json"
        self.seed.write_text(json.dumps({"schema": "medical-term-cards-v1", "records": [
            {"id": "nephrotic-syndrome", "title": "Nephrotic syndrome", "aliases": ["NS"],
             "definition": "Local definition", "summary": "Long text", "source": {"name": "MDWiki"}},
            {"id": "same", "title": "Duplicate A", "aliases": [], "source": {"name": "MDWiki"}},
            {"id": "same", "title": "Duplicate B", "aliases": [], "source": {"name": "MDWiki"}},
        ]}), encoding="utf-8")
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def provider(self, payloads=()):
        queue = list(payloads)

        def opener(request, timeout):
            self.calls.append((request, timeout))
            return FakeResponse(queue.pop(0))

        return TermProvider(self.seed, self.root / "cache" / "cache.json", opener=opener)

    def test_seed_title_and_alias_are_exact_and_offline_first(self):
        provider = self.provider()
        self.assertEqual(provider.lookup(query=" nephrotic   syndrome ")["record"]["definition"], "Local definition")
        self.assertEqual(provider.lookup(query="ns")["record"]["id"], "nephrotic-syndrome")
        self.assertEqual(self.calls, [])
        self.assertEqual(provider.lexicon()[0], {"id": "nephrotic-syndrome", "title": "Nephrotic syndrome",
                                                   "aliases": ["NS"], "source": {"name": "MDWiki"}})
        self.assertNotIn("definition", provider.lexicon()[0])

    def test_duplicate_ids_are_reported_as_ambiguous(self):
        result = self.provider().lookup(record_id="same")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ambiguous_term")
        self.assertEqual([x["title"] for x in result["candidates"]], ["Duplicate A", "Duplicate B"])

    def test_requested_term_survives_restart_in_compact_offline_vocabulary(self):
        provider = self.provider([page(title="Nephron", extract="A kidney filtration unit.")])
        result = provider.lookup(query="Nephron")
        self.assertTrue(result["ok"])
        restarted = self.provider()
        cached_terms = [row for row in restarted.lexicon() if row["title"] == "Nephron"]
        self.assertEqual(len(cached_terms), 1)
        self.assertNotIn("definition", cached_terms[0])
        calls_before = len(self.calls)
        self.assertTrue(restarted.lookup(record_id=result["record"]["id"])["ok"])
        self.assertEqual(len(self.calls), calls_before)

    def test_new_exact_source_title_returns_plain_text_record_and_provenance(self):
        provider = self.provider([
            missing(),
            {"query": {"search": [{"title": "Nephrotic syndrome"}]}},
            page(extract="First paragraph.\n\nSecond paragraph.")
        ])
        # Use a query absent from the seed but exactly matching source title.
        provider._records = []
        result = provider.lookup(query="Nephrotic syndrome")
        self.assertTrue(result["ok"])
        record = result["record"]
        self.assertEqual(record["definition"], "First paragraph. Second paragraph.")
        self.assertEqual(record["summary"], "First paragraph. Second paragraph.")
        self.assertEqual(record["source"]["revision_id"], 77)
        self.assertEqual(record["source"]["license"], "CC BY-SA 4.0")
        self.assertEqual(record["source"]["license_url"], "https://creativecommons.org/licenses/by-sa/4.0/")
        self.assertEqual(record["source"]["copyright_url"], "https://mdwiki.org/wiki/WikiProjectMed:Copyright")
        self.assertEqual(record["source"]["changes"], "Lead excerpt; shortened for display.")
        self.assertTrue((self.root / "cache" / "cache.json").is_file())
        for request, timeout in self.calls:
            self.assertIn("mdwiki.org/w/api.php", request.full_url)
            self.assertIn("format=json", request.full_url)
            self.assertLessEqual(timeout, 10)
            self.assertIn("https://github.com/Colbstang/llu-neuro-psych-final", request.get_header("User-agent"))

    def test_attributed_mdwiki_page_image_is_metadata_only_and_license_linked(self):
        article = page(title="Nephrotic syndrome", extract="A kidney disorder.")
        article["query"]["pages"][0].update({
            "pageimage": "Example_histology.jpg",
            "thumbnail": {"source": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example_histology.jpg",
                          "width": 640, "height": 480},
        })
        image = {"query": {"pages": [{"title": "File:Example_histology.jpg", "imageinfo": [{
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Example_histology.jpg",
            "extmetadata": {
                "Artist": {"value": '<a href="/wiki/User:Example">Example artist</a>'},
                "ImageDescription": {"value": "Renal histology &amp; stained tissue."},
                "LicenseShortName": {"value": "CC BY-SA 3.0"},
                "LicenseUrl": {"value": "http://creativecommons.org/licenses/by-sa/3.0/"},
            },
        }]}]}}
        provider = self.provider([article, image])
        provider._records = []
        result = provider.lookup(query="Nephrotic syndrome")
        self.assertTrue(result["ok"])
        self.assertEqual(result["record"]["definition"], "A kidney disorder.")
        self.assertEqual(result["record"]["image"], {
            "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example_histology.jpg",
            "file_url": "https://commons.wikimedia.org/wiki/File:Example_histology.jpg",
            "artist": "Example artist",
            "license": "CC BY-SA 3.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
            "alt": "Renal histology & stained tissue.",
            "width": 640,
            "height": 480,
        })
        self.assertEqual(len(self.calls), 2)
        self.assertLessEqual(self.calls[1][1], 3.0)
        article_params = parse_qs(urlsplit(self.calls[0][0].full_url).query)
        self.assertIn("pageimages", article_params["prop"][0])
        self.assertEqual(article_params["pithumbsize"], ["640"])
        image_params = parse_qs(urlsplit(self.calls[1][0].full_url).query)
        self.assertEqual(image_params["titles"], ["File:Example_histology.jpg"])
        self.assertEqual(image_params["iiprop"], ["url|extmetadata"])

    def test_unattributed_or_unsafe_page_image_is_omitted_without_losing_text(self):
        article = page(title="Nephrotic syndrome", extract="A kidney disorder.")
        article["query"]["pages"][0].update({
            "pageimage": "Example.svg",
            "thumbnail": {"source": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.svg",
                          "width": 640, "height": 480},
        })
        provider = self.provider([article])
        provider._records = []
        result = provider.lookup(query="Nephrotic syndrome")
        self.assertTrue(result["ok"])
        self.assertNotIn("image", result["record"])
        self.assertEqual(len(self.calls), 1)

        article = page(title="Nephrotic syndrome", extract="A kidney disorder.")
        article["query"]["pages"][0].update({
            "pageimage": "Example.jpg",
            "thumbnail": {"source": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.jpg",
                          "width": 640, "height": 480},
        })
        unlicensed = {"query": {"pages": [{"title": "File:Example.jpg", "imageinfo": [{
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Example.jpg",
            "extmetadata": {"Artist": {"value": "Artist"}, "LicenseShortName": {"value": "Unknown"}},
        }]}]}}
        provider = self.provider([article, unlicensed])
        provider._records = []
        result = provider.lookup(query="Nephrotic syndrome")
        self.assertTrue(result["ok"])
        self.assertNotIn("image", result["record"])

    def test_search_candidates_are_never_auto_selected(self):
        provider = self.provider([missing(), {"query": {"search": [
            {"title": "Beta blocker", "snippet": "..."}, {"title": "Beta oxidation"}
        ]}}])
        provider._records = []
        result = provider.lookup(query="beta")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unknown_term")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("action=parse", "&".join(call[0].full_url for call in self.calls))

    def test_disambiguation_page_is_not_returned_as_definition(self):
        provider = self.provider([
            missing(),
            {"query": {"search": [{"title": "Complement system"}]}},
            page("Complement system", "Broad disambiguation text", pageprops={"disambiguation": ""}),
        ])
        provider._records = []
        result = provider.lookup(query="Complement system")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ambiguous_term")

    def test_disambiguation_stub_without_pageprops_returns_linked_choices(self):
        data = page("Posterior", "Posterior may refer to:")
        data["query"]["pages"][0]["links"] = [
            {"title": "Posterior (anatomy)"}, {"title": "Anterior"},
        ]
        provider = self.provider([data])
        provider._records = []
        result = provider.lookup(query="Posterior")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ambiguous_term")
        self.assertEqual([item["title"] for item in result["candidates"]], ["Posterior (anatomy)", "Anterior"])

    def test_mirrored_disambiguation_pages_return_source_link_choices(self):
        def mirrored(title, html):
            missing_page = {"query": {"pages": [{"title": title, "missing": True,
                "revisions": [{"revid": 123, "mirrored": True}]}]}}
            parse = {"parse": {"title": title, "revid": 123, "text": html}}
            return [missing_page, parse]

        posterior_html = ('<p>Posterior may refer to:</p><ul>'
                          '<li><a href="./Posterior_(anatomy)" title="Posterior (anatomy)">Posterior (anatomy)</a></li>'
                          '<li><a href="./Anterior" title="Anterior">Anterior</a></li></ul>')
        provider = self.provider(mirrored("Posterior", posterior_html))
        provider._records = []
        result = provider.lookup(query="Posterior")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ambiguous_term")
        self.assertEqual(result["candidates"][0]["title"], "Posterior (anatomy)")
        self.assertIn("mdwiki.org/wiki/Posterior_%28anatomy%29", result["candidates"][0]["url"])

        hemisphere_html = ('<p>Hemisphere may refer to:</p><ul>'
                           '<li><a href="./Northern_Hemisphere" title="Northern Hemisphere">Northern Hemisphere</a></li>'
                           '<li><a href="./Cerebral_hemisphere" title="Cerebral hemisphere">Cerebral hemisphere</a></li>'
                           '<li><a href="./Cerebellar_hemisphere" title="Cerebellar hemisphere">Cerebellar hemisphere</a></li></ul>')
        provider = self.provider(mirrored("Hemisphere", hemisphere_html))
        provider._records = []
        result = provider.lookup(query="Hemisphere")
        self.assertFalse(result["ok"])
        self.assertEqual([item["title"] for item in result["candidates"][:2]],
                         ["Cerebral hemisphere", "Cerebellar hemisphere"])

    def test_source_backed_posterior_anatomy_redirect_uses_matching_section(self):
        direct = {"query": {
            "redirects": [{"from": "Posterior (anatomy)", "to": "Anatomical terms of location"}],
            "pages": [{"title": "Anatomical terms of location", "missing": True,
                       "fullurl": "https://mdwiki.org/wiki/Anatomical_terms_of_location",
                       "revisions": [{"revid": 77, "mirrored": True}] }],
        }}
        html = ("<h2><span>Anterior and posterior</span></h2>"
                "<p>Anterior describes what is in front, and posterior describes what is to the back of something.</p>"
                "<h2>Other terms</h2><p>This unrelated paragraph must not be included.</p>")
        provider = self.provider([direct, {"parse": {"title": "Anatomical terms of location", "revid": 77, "text": html}}])
        provider._records = []
        result = provider.lookup(query="Posterior (anatomy)")
        self.assertTrue(result["ok"])
        record = result["record"]
        self.assertEqual(record["title"], "Posterior (anatomy)")
        self.assertIn("posterior describes what is to the back", record["definition"])
        self.assertNotIn("unrelated", record["summary"])
        self.assertEqual(record["source"]["section"], "Anterior and posterior")
        self.assertTrue(record["source"]["url"].endswith("#Anterior_and_posterior"))

    def test_stale_cached_disambiguation_stub_is_not_recognized_by_query_or_id(self):
        cache_path = self.root / "cache" / "cache.json"
        cache_path.parent.mkdir(parents=True)
        stale = {"id": "mdwiki-posterior", "title": "Posterior", "aliases": [],
                 "definition": "Posterior may refer to:", "summary": "Posterior may refer to:",
                 "source": {"name": "MDWiki", "url": "https://mdwiki.org/wiki/Posterior"}}
        cache_path.write_text(json.dumps({"query:posterior": stale, "id:mdwiki-posterior": stale}), encoding="utf-8")
        mirrored_missing = {"query": {"pages": [{"title": "Posterior", "missing": True,
            "revisions": [{"revid": 123, "mirrored": True}]}]}}
        parsed = {"parse": {"title": "Posterior", "text":
            '<p>Posterior may refer to:</p><ul><li><a href="./Posterior_(anatomy)" '
            'title="Posterior (anatomy)">Posterior (anatomy)</a></li></ul>'}}
        provider = self.provider([mirrored_missing, parsed])
        provider._records = []
        self.assertFalse(any(row["title"] == "Posterior" for row in provider.lexicon()))
        self.assertEqual(provider.lookup(record_id="mdwiki-posterior")["error"], "unknown_term")
        result = provider.lookup(query="Posterior")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ambiguous_term")
        self.assertEqual(result["candidates"][0]["title"], "Posterior (anatomy)")
        self.assertEqual(len(self.calls), 2)

    def test_invalid_and_unavailable_queries_are_bounded(self):
        provider = self.provider()
        self.assertEqual(provider.lookup(query="x" * 161)["error"], "invalid_query")
        self.assertEqual(provider.lookup(query="\nterm")["error"], "invalid_query")
        self.assertEqual(provider.lookup(query=" ")["error"], "invalid_query")
        self.assertEqual(self.calls, [])

    def test_html_like_source_text_remains_data_and_is_not_interpreted(self):
        provider = self.provider([
            page("Example term", "Literal &lt;em&gt; text.")
        ])
        provider._records = []
        result = provider.lookup(query="Example term")
        self.assertTrue(result["ok"])
        self.assertEqual(result["record"]["definition"], "Literal &lt;em&gt; text.")
        self.assertIn("explaintext", self.calls[0][0].full_url)

    def test_direct_exact_redirect_is_allowed_and_includes_json_format(self):
        direct = page("Interferon beta-1b", "An exact redirect target.")
        direct["query"]["redirects"] = [{"from": "Interferon β-1b", "to": "Interferon beta-1b"}]
        provider = self.provider([direct])
        provider._records = []
        result = provider.lookup(query="Interferon β-1b")
        self.assertTrue(result["ok"])
        self.assertEqual(result["record"]["title"], "Interferon beta-1b")
        self.assertEqual(normalize_term("café"), normalize_term("cafe"))
        self.assertNotEqual(normalize_term("interferon beta-1a"), normalize_term("interferon beta-1b"))
        self.assertIn("format=json", self.calls[0][0].full_url)

    def test_empty_remote_extract_is_not_reported_as_a_success(self):
        provider = self.provider([page("Example term", "   "),
                                  {"parse": {"title": "Example term", "text": "<table><tr><td>No prose</td></tr></table>"}}])
        provider._records = []
        result = provider.lookup(query="Example term")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "content_unavailable")

    def test_mirrored_missing_extract_uses_parse_lead_and_original_revision_links(self):
        missing_mirrored = {"query": {"pages": [{
            "title": "Mirrored exact term", "missing": True,
            "revisions": [{"revid": 1362368972, "timestamp": "2025-01-02T03:04:05Z", "mirrored": True}],
        }]}}
        html = '''<section data-mw-section-id="0">
          <div class="shortdescription"><p>hidden short description.</p></div>
          <p><b>Lead</b> definition with <a href="/wiki/Term">linked prose</a>.<sup class="reference">[1]</sup></p>
          <table class="infobox"><tr><td><p>hidden table material.</p></td></tr></table>
          <ul><li>hidden list material.</li></ul>
          <figure><p>hidden figure material.</p></figure>
          <div class="infobox"><p>hidden infobox paragraph.</p></div>
          <p>Second lead sentence.</p>
          <section data-mw-section-id="1"><h2>Causes</h2><p>later section.</p></section>
        </section>'''
        parse_payload = {"parse": {"title": "Mirrored exact term", "pageid": 123, "revid": 999,
                                    "text": html}}
        provider = self.provider([missing_mirrored, parse_payload])
        provider._records = []
        result = provider.lookup(query="Mirrored exact term")
        self.assertTrue(result["ok"])
        record = result["record"]
        self.assertEqual(record["definition"], "Lead definition with linked prose. Second lead sentence.")
        self.assertNotIn("hidden", record["summary"])
        self.assertNotIn("later section", record["summary"])
        source = record["source"]
        self.assertEqual(source["mirror_revision"], 1362368972)
        self.assertEqual(source["mirror_timestamp"], "2025-01-02T03:04:05Z")
        self.assertEqual(source["original_revision_id"], 1362368972)
        self.assertIn("/wiki/Mirrored_exact_term", source["original_url"])
        self.assertEqual(source["original_revision_url"], "https://en.wikipedia.org/w/index.php?oldid=1362368972")
        self.assertIn("action=history", source["original_history_url"])
        self.assertNotIn("revision_url", source)
        self.assertNotIn("revision_id", source)
        self.assertIn("mdwiki.org/wiki/Mirrored_exact_term", source["url"])
        self.assertEqual(len(self.calls), 2)
        parse_params = parse_qs(urlsplit(self.calls[1][0].full_url).query)
        self.assertEqual(parse_params["action"], ["parse"])
        self.assertNotIn("section", parse_params)
        self.assertEqual(parse_params["format"], ["json"])

    def test_empty_exact_page_can_parse_but_parse_canonical_mismatch_is_rejected(self):
        exact_empty = page("Exact requested title", "")
        mismatched_parse = {"parse": {"title": "Different article", "text": "<p>Wrong content.</p>"}}
        provider = self.provider([exact_empty, mismatched_parse])
        provider._records = []
        result = provider.lookup(query="Exact requested title")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unknown_term")
        self.assertEqual(len(self.calls), 2)

    def test_remote_record_excerpts_are_bounded_and_sentence_complete(self):
        intro = " ".join(f"Sentence {i} has several medically useful words." for i in range(1, 70))
        provider = self.provider([page("Long example", intro)])
        provider._records = []
        record = provider.lookup(query="Long example")["record"]
        self.assertLessEqual(len(record["definition"].split()), 80)
        self.assertLessEqual(len(record["summary"].split()), 350)
        self.assertTrue(record["definition"].endswith("."))
        self.assertTrue(record["summary"].endswith("."))

    def test_cache_trims_to_count_and_serialized_size_limits(self):
        provider = self.provider()
        for index in range(CACHE_MAX_ENTRIES + 20):
            provider._cache[f"key:{index}"] = {"id": str(index), "text": "x"}
        provider._trim_cache()
        self.assertLessEqual(len(provider._cache), CACHE_MAX_ENTRIES)
        provider._cache["large"] = {"id": "large", "text": "x" * (CACHE_MAX_BYTES + 100)}
        provider._trim_cache()
        self.assertLessEqual(len(json.dumps(provider._cache, ensure_ascii=False).encode("utf-8")), CACHE_MAX_BYTES)


if __name__ == "__main__":
    unittest.main()
