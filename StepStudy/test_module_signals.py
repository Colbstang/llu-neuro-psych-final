import json
import io
import unittest

from StepStudy import module_signals as signals


def fixture_data():
    return {
        "pages": [
            {"id": "brain-page", "title": "Neurology", "keywords": ["brain"], "blocks": [
                {"id": "n1", "title": "Neural foundations"}, {"id": "n2", "title": "Stroke"},
            ]},
            {"id": "psych-page", "title": "Psychiatric disorders", "keywords": [], "blocks": [
                {"id": "p1", "title": "Mood disorders"},
            ]},
            {"id": "mixed-page", "title": "Neuropsych assessment", "keywords": [], "blocks": [
                {"id": "m1", "title": "Integrated neuropsych review"},
            ]},
            {"id": "unclear-page", "title": "Week review", "keywords": [], "blocks": [
                {"id": "u1", "title": "Overview"},
            ]},
        ],
        "topic_groups": {"groups": [
            {"id": "foundations", "title": "Neural foundations", "short_title": "Foundations", "page_ids": ["brain-page"]},
            {"id": "psych-disorders", "title": "Psychiatric disorders", "short_title": "Psych", "page_ids": ["psych-page"]},
            {"id": "mixed", "title": "Neuropsych review", "short_title": "Review", "page_ids": ["mixed-page"]},
            {"id": "misc", "title": "Week review", "short_title": "Review", "page_ids": ["unclear-page"]},
        ]},
        "objectives": [
            {"id": "lo1", "sections": ["n1", "n2"]},
            {"id": "lo2", "sections": ["p1"]},
            {"id": "lo3", "sections": ["m1"]},
        ],
        "questions": [
            {"id": "q1", "source_status": "Wrong", "suggested_sections": ["n1"]},
            {"id": "q2", "source_status": "Wrong", "suggested_sections": ["n2"]},
            {"id": "q3", "source_status": "Correct", "suggested_sections": ["p1"]},
            {"id": "q4", "source_status": "Wrong", "suggested_sections": ["m1"]},
        ],
        "question_auto_links": [], "question_link_corrections": [], "question_annotations": [],
    }


class ModuleSignalsTests(unittest.TestCase):
    def setUp(self):
        signals._METADATA_CACHE = None

    def test_summarizes_normalized_ratings_unresolved_wrong_and_overlap(self):
        state = {
            "objectives": {"lo1": {"review": "reviewed"}, "lo2": {"review": "later"}},
            "questions": {"q2": {"review": "reviewed"}},
            "good": {"n1": True, "p1": False, "m1": True},
        }
        result = signals.summarize_module(fixture_data(), state, synced_at="2026-10-09T12:00:00Z")
        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "Neuro/Psych module")
        self.assertEqual(result["topics"]["neuro"], {
            "lo_good": 1, "lo_bad": 0, "lo_unrated": 1,
            "learned_sections": 2, "total_sections": 3, "wrong_count": 2, "question_count": 3,
        })
        self.assertEqual(result["topics"]["psychiatry"], {
            "lo_good": 0, "lo_bad": 1, "lo_unrated": 1,
            "learned_sections": 1, "total_sections": 2, "wrong_count": 1, "question_count": 2,
        })
        self.assertEqual(result["unmapped_metadata"], {
            "unclassified_sections": 1, "unmapped_objectives": 0, "unmapped_questions": 0,
        })

    def test_empty_ratings_remain_unrated_and_do_not_imply_mastery(self):
        result = signals.summarize_module(fixture_data(), {})
        self.assertEqual(result["topics"]["neuro"]["lo_unrated"], 2)
        self.assertEqual(result["topics"]["neuro"]["learned_sections"], 0)

    def test_collect_reads_fake_transport_and_caches_only_metadata(self):
        html = "<!doctype html><script>const DATA=" + json.dumps(fixture_data()) + ";</script>"
        progress = json.dumps({"state": {"objectives": {"lo1": {"review": "good"}}},
                               "updated_at": "2026-10-09T12:00:00Z"}).encode()
        requests = []

        def fake_get(url):
            requests.append(url)
            return progress if url == signals.PROGRESS_URL else html.encode()

        first = signals.collect_module_signals(fetch=fake_get)
        second = signals.collect_module_signals(fetch=fake_get)
        self.assertTrue(first["available"])
        self.assertTrue(second["available"])
        self.assertEqual(requests.count(signals.MODULE_URL), 1)
        self.assertEqual(requests.count(signals.PROGRESS_URL), 2)
        self.assertEqual(set(signals._METADATA_CACHE["questions"][0]), {"id", "source_status", "sections"})
        self.assertNotIn("html", signals._METADATA_CACHE)

    def test_question_link_sources_merge_deduplicate_and_apply_corrections(self):
        data = fixture_data()
        data["questions"][0]["suggested_sections"] = ["n1", "n2"]
        data["questions"][1]["suggested_sections"] = []
        data["questions"][3]["suggested_sections"] = []
        data["question_auto_links"] = [
            {"question_id": "q1", "sections": ["n2", "m1"], "tags": ["private-tag"], "rationale": "private rationale"},
            {"question_id": "q2", "sections": ["p1"], "tags": [], "rationale": "private rationale"},
        ]
        data["question_link_corrections"] = [
            {"question_id": "q1", "old_section": "n2", "new_section": "p1"},
        ]
        data["question_annotations"] = [
            {"question_id": "q1", "block_id": "m1", "quote": "private quote", "reason": "private reason"},
            {"question_id": "q1", "block_id": "m1", "quote": "another quote", "reason": "another reason"},
        ]
        metadata = signals._section_metadata(data)
        links = {question["id"]: question["sections"] for question in metadata["questions"]}
        self.assertEqual(links["q1"], ["m1", "n1", "p1"])
        self.assertEqual(links["q2"], ["p1"])
        self.assertNotIn("n2", links["q1"])
        cached = repr(metadata)
        for private_value in ("private-tag", "private rationale", "private quote", "private reason", "another quote"):
            self.assertNotIn(private_value, cached)
        state = {"questions": {}}
        result = signals._summarize_metadata(metadata, state)
        self.assertEqual(result["topics"]["neuro"]["question_count"], 1)
        self.assertEqual(result["topics"]["neuro"]["wrong_count"], 1)
        self.assertEqual(result["topics"]["psychiatry"]["question_count"], 3)
        self.assertEqual(result["topics"]["psychiatry"]["wrong_count"], 2)

    def test_data_extractor_does_not_require_or_execute_html_scripts(self):
        data = signals._extract_data("<script>const DATA = {\"pages\": []}; throw new Error()</script>")
        self.assertEqual(data, {"pages": []})

    def test_streaming_extractor_stops_after_metadata_before_large_assets(self):
        data = fixture_data()
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        html = ("<script>const DATA=" + encoded + ";const ASSETS=").encode("utf-8")
        tail = b'"asset-data";' + b"x" * 2_000_000

        class TrackingStream(io.BytesIO):
            bytes_read = 0

            def read(self, size=-1):
                result = super().read(size)
                self.bytes_read += len(result)
                return result

        stream = TrackingStream(html + tail)
        extracted = signals._extract_metadata_stream(stream, chunk_size=128)
        self.assertEqual(set(extracted), {
            "pages", "topic_groups", "objectives", "questions", "question_auto_links",
            "question_link_corrections", "question_annotations",
        })
        self.assertEqual(extracted["pages"][0]["title"], "Neurology")
        self.assertLess(stream.bytes_read, len(html) + 1_000)

    def test_streaming_extractor_handles_utf8_split_across_chunks(self):
        data = fixture_data()
        data["pages"][0]["title"] = "Neurology Café"
        html = ("prefix const DATA=" + json.dumps(data, ensure_ascii=False) + ";").encode("utf-8")
        extracted = signals._extract_metadata_stream(io.BytesIO(html), chunk_size=1)
        self.assertEqual(extracted["pages"][0]["title"], "Neurology Café")

    def test_assignment_search_limit_uses_assignment_offset_not_chunk_end(self):
        prefix = b"x" * 32
        html = prefix + ("const DATA=" + json.dumps(fixture_data()) + ";").encode()
        extracted = signals._extract_metadata_stream(io.BytesIO(html), chunk_size=4096, search_limit=32)
        self.assertIn("pages", extracted)

    def test_streaming_extractor_fails_on_incomplete_bounded_metadata(self):
        html = b'const DATA={"pages":[],"topic_groups":{"groups":[]},"objectives":[]'
        with self.assertRaisesRegex(ValueError, "incomplete|missing"):
            signals._extract_metadata_stream(io.BytesIO(html), chunk_size=8, max_bytes=128)

    def test_oversize_html_reports_unavailable(self):
        progress = json.dumps({"state": {}}).encode()
        result = signals.collect_module_signals(fetch=lambda url: progress if url == signals.PROGRESS_URL else b"x" * (signals.MAX_HTML_BYTES + 1))
        self.assertFalse(result["available"])
        self.assertIn("40 MB", result["reason"])


if __name__ == "__main__":
    unittest.main()
