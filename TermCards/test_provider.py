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
