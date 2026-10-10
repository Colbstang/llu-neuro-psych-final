import unittest

from StepStudy.concept_learning import build_catalog, build_item_context
import hashlib


def course_data():
    return {
        "pages": [
            {"id": "neuro-chapter", "title": "Neurology", "keywords": ["brain"], "blocks": [
                {"id": "n-block-a", "title": "Neural signaling", "summary": "Neurons communicate through a stated sequence."},
                {"id": "n-block-b", "title": "Stroke", "summary": "A stroke is a source supplied interruption of cerebral blood flow."},
                {"id": "n-empty", "title": "Unanswered subsection", "summary": ""},
            ]},
            {"id": "psych-chapter", "title": "Psychiatric disorders", "keywords": [], "blocks": [
                {"id": "p-block", "title": "Mood disorders", "summary": "Mood disorders are described in the source."},
            ]},
        ],
        "topic_groups": {"groups": [
            {"id": "neural-foundations", "title": "Neural foundations", "page_ids": ["neuro-chapter"]},
            {"id": "psychiatry", "title": "Psychiatric disorders", "page_ids": ["psych-chapter"]},
        ]},
        "objectives": [
            {"id": "lo-stable-1", "text": "Describe the supplied neural signaling objective.", "sections": ["n-block-a"]},
            {"id": "lo-unanswered", "text": "No answer supplied.", "sections": ["n-block-b"]},
            {"id": "lo-psych", "text": "Describe the supplied mood objective.", "sections": ["p-block"]},
        ],
        "objective_answers": {
            "lo-stable-1": {"html": "<p>First stated mechanism.</p><script>PRIVATE SHOULD NOT APPEAR</script><p>Second stated consequence.</p>",
                            "sources": [{"id": "source-lecture-1", "title": "Lecture source"}]},
            "lo-psych": {"html": "<p>Source supplied mood answer.</p>", "sources": [{"id": "source-lecture-2", "title": "Psych lecture"}]},
        },
        "questions": [
            {"id": "q-private", "stem": "Unanswered prompt", "suggested_sections": ["n-block-a"]},
            {"id": "q-supplied", "stem": "Supplied course question", "suggested_sections": ["n-block-a"]},
        ],
        "question_answers": {"q-supplied": {"answer": "Source supplied answer point.",
                                               "sources": [{"id": "question-source", "title": "Course source"}]}},
    }


class ConceptLearningTests(unittest.TestCase):
    def test_catalog_prefers_answered_objectives_and_uses_block_fallback(self):
        items = build_catalog(course_data())
        by_id = {item["item_id"]: item for item in items}
        self.assertEqual(set(by_id), {"lo:lo-stable-1", "lo:lo-psych", "block:n-block-b", "question:q-supplied"})
        self.assertEqual(by_id["lo:lo-stable-1"]["topic_ids"], ["neuro"])
        self.assertEqual(by_id["lo:lo-psych"]["topic_ids"], ["psychiatry"])
        self.assertEqual(by_id["lo:lo-stable-1"]["chapter_id"], "neuro-chapter")
        self.assertEqual(by_id["block:n-block-b"]["kind"], "block")
        self.assertNotIn("PRIVATE", repr(items))
        self.assertNotIn("q-private", repr(items))

    def test_item_context_is_single_item_bounded_and_strips_active_html(self):
        data = course_data()
        context = build_item_context(data, "lo:lo-stable-1")
        self.assertIsNotNone(context)
        self.assertIn("First stated mechanism", context["reference_context"])
        self.assertIn("Second stated consequence", context["reference_context"])
        self.assertNotIn("PRIVATE SHOULD", context["reference_context"])
        self.assertEqual(context["sources"][0]["id"],
                         "course-source:" + hashlib.sha256("lecture source\0".encode()).hexdigest()[:20])
        self.assertLessEqual(len(context["reference_context"]), 3000)
        self.assertIsNone(build_item_context(data, "lo:lo-unanswered"))
        q_context = build_item_context(data, "question:q-supplied")
        self.assertEqual(q_context["reference_context"], "Source supplied answer point.")
        self.assertIsNone(build_item_context(data, "question:q-private"))
        self.assertIsNone(build_item_context(data, "untrusted:item"))

    def test_item_identity_does_not_depend_on_course_row_order_or_title(self):
        data = course_data()
        original = {item["item_id"] for item in build_catalog(data)}
        data["objectives"].reverse()
        data["pages"][0]["blocks"].reverse()
        data["objectives"][0]["text"] = "Edited presentation text with same stable ID."
        self.assertEqual(original, {item["item_id"] for item in build_catalog(data)})

    def test_topic_filter_keeps_overlapping_or_unmapped_content_explicit(self):
        data = course_data()
        data["pages"][0]["title"] = "Neuropsych assessment"
        catalog = build_catalog(data, topic_ids={"psychiatry"})
        self.assertIn("lo:lo-stable-1", {item["item_id"] for item in catalog})
        # A neuropsych chapter is explicitly overlapping in module_signals.
        self.assertIn("block:n-block-b", {item["item_id"] for item in catalog})

    def test_citations_use_stable_label_page_identity_and_never_expose_private_paths(self):
        data = course_data()
        data["objective_answers"]["lo-stable-1"]["sources"] = [{
            "label": "Neuro lecture", "page": 12,
            "path": "/private/course/lecture.pdf", "url": "/opaque/route.pdf",
        }]
        calls = []
        def resolve(alias):
            calls.append(alias)
            return "/api/course/document/0123456789abcdef0123456789abcdef.pdf" if alias.startswith("/") else None
        context = build_item_context(data, "lo:lo-stable-1", source_resolver=resolve)
        source = context["sources"][0]
        # Stable IDs depend only on the human source label and physical page.
        expected = "course-source:" + hashlib.sha256(("neuro lecture\0" + "12").encode()).hexdigest()[:20]
        self.assertEqual(source["id"], expected)
        self.assertEqual(source["title"], "Neuro lecture (p. 12)")
        self.assertEqual(source["url"], "/api/course/document/0123456789abcdef0123456789abcdef.pdf#page=12")
        self.assertNotIn("/private", repr(source))
        self.assertNotIn("opaque", repr(source))
        self.assertTrue(calls)
        self.assertEqual(calls[0], "/opaque/route.pdf")
        self.assertNotIn("/private/course/lecture.pdf", repr(source))

    def test_only_vetted_https_sources_are_linked_and_question_answers_stay_in_context(self):
        data = course_data()
        data["questions"].append({
            "id": "q-trigger", "trigger": "A supplied trigger tests a distinction.",
            "key_fact": "The source supplied key fact.", "trap": "The source supplied trap.",
            "title": "Sensitive diagnostic title that must not leak", "source": "Course source",
            "source_page": 9, "source_url": "https://www.ncbi.nlm.nih.gov/books/example/",
            "suggested_sections": ["n-block-a"],
        })
        data["question_answers"]["q-trigger"] = {"key_fact": "The source supplied key fact."}
        items = {item["item_id"]: item for item in build_catalog(data)}
        item = items["question:q-trigger"]
        self.assertIn("A supplied trigger", item["prompt"])
        self.assertNotIn("Sensitive diagnostic title", repr(item))
        context = build_item_context(data, "question:q-trigger")
        self.assertIn("The source supplied key fact", context["reference_context"])
        self.assertIn("The source supplied trap", context["reference_context"])
        self.assertNotIn("The source supplied key fact", context["prompt"])
        self.assertTrue(context["sources"][0]["url"].startswith("https://www.ncbi.nlm.nih.gov/"))
        data["questions"][-1]["source_url"] = "https://example.com/private.pdf"
        context = build_item_context(data, "question:q-trigger")
        self.assertNotIn("url", context["sources"][0])

    def test_long_prompts_end_on_a_complete_word_with_visible_truncation(self):
        data = course_data()
        data["objectives"][0]["text"] = "clinical " * 300
        prompt = next(item["prompt"] for item in build_catalog(data) if item["item_id"] == "lo:lo-stable-1")
        self.assertLessEqual(len(prompt), 1_200)
        self.assertTrue(prompt.endswith("…"))
        self.assertNotRegex(prompt[:-1], r"clin$")

    def test_explicit_chapter_id_and_authored_group_membership_map_only_their_pages(self):
        data = {
            "pages": [
                {"id": "final-late-neuro-eye-movements", "title": "Course page", "keywords": [],
                 "blocks": [{"id": "eye-core", "title": "Course section", "summary": "Source supplied explanation."}]},
                {"id": "module-938", "title": "Unclassified page", "keywords": [],
                 "blocks": [{"id": "group-core", "title": "Neutral section", "summary": "Another source supplied explanation."}]},
                {"id": "module-939", "title": "Unrelated page", "keywords": [],
                 "blocks": [{"id": "unmapped-core", "title": "Neutral section", "summary": "This must remain unmapped."}]},
            ],
            "topic_groups": {"groups": [
                {"id": "neuro", "title": "Course branch", "page_ids": ["module-938"]},
            ]},
            "objectives": [], "objective_answers": {}, "questions": [],
        }
        items = build_catalog(data)
        by_chapter = {item["chapter_id"]: item for item in items}
        self.assertEqual(by_chapter["final-late-neuro-eye-movements"]["topic_ids"], ["neuro"])
        self.assertEqual(by_chapter["module-938"]["topic_ids"], ["neuro"])
        self.assertNotIn("module-939", by_chapter)

    def test_cross_chapter_objective_does_not_hide_unrepresented_chapter_block(self):
        data = {
            "pages": [
                {"id": "neuro-main", "title": "Main chapter", "keywords": [],
                 "blocks": [{"id": "main-core", "title": "Main section", "summary": "Main source text."}]},
                {"id": "final-late-neuro-eye-movements", "title": "Eye movements", "keywords": [],
                 "blocks": [{"id": "gaze-core", "title": "Gaze section", "summary": "Source supplied gaze explanation."}]},
            ],
            "topic_groups": {"groups": []},
            "objectives": [{"id": "cross-chapter-lo", "text": "Explain the source objective.",
                            "sections": ["main-core", "gaze-core"]}],
            "objective_answers": {"cross-chapter-lo": {"answer": "Source supplied answer."}},
            "questions": [],
        }
        items = build_catalog(data)
        self.assertIn(("lo:cross-chapter-lo", "neuro-main"),
                      {(item["item_id"], item["chapter_id"]) for item in items})
        gaze = [item for item in items if item["chapter_id"] == "final-late-neuro-eye-movements"]
        self.assertEqual([(item["item_id"], item["kind"]) for item in gaze], [("block:gaze-core", "block")])
        self.assertEqual(build_item_context(data, "block:gaze-core")["reference_context"],
                         "Source supplied gaze explanation.")

    def test_block_context_prefers_full_supplied_explanation_over_teaser_summary(self):
        data = course_data()
        block = data["pages"][0]["blocks"][1]
        block["summary"] = "Short chapter teaser."
        block["html"] = "<p>Full source explanation of the linked pathway.</p>"
        context = build_item_context(data, "block:n-block-b")
        self.assertEqual(context["reference_context"], "Full source explanation of the linked pathway.")


if __name__ == "__main__":
    unittest.main()
