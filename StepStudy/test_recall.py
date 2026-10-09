import unittest

from StepStudy.recall import build_recall


def record(record_id="complement-system", title="Complement system", definition="The complement system helps clear microbes and damaged cells.", summary=None):
    return {"id": record_id, "title": title, "definition": definition,
            "summary": summary or definition,
            "source": {"name": "MDWiki", "url": "https://mdwiki.org/wiki/Complement_system"}}


class RecallTests(unittest.TestCase):
    def test_builds_open_ended_question_with_source_grounded_points(self):
        result = build_recall("immunology", [record(summary=(
            "The complement system helps clear microbes. The underlying mechanism involves "
            "a cascade that promotes inflammation and attacks pathogen membranes."))], topic_label="Immunology")
        self.assertIsNotNone(result)
        self.assertIn("Explain the immune mechanism", result["prompt"])
        self.assertNotIn("in your own words", result["prompt"])
        self.assertEqual(result["sources"][0]["id"], "complement-system")
        self.assertTrue(any("underlying mechanism" in point for point in result["expected_points"]))
        self.assertIn("attacks pathogen membranes", result["reference_context"])

    def test_generic_prompt_uses_only_supplied_definition_when_mechanism_is_absent(self):
        supplied = record("nephrotic-syndrome", "Nephrotic syndrome",
                          "Nephrotic syndrome includes protein in urine, low blood albumin, and swelling.")
        result = build_recall("renal", [supplied])
        self.assertIsNotNone(result)
        self.assertIn("Explain the renal concept Nephrotic syndrome", result["prompt"])
        self.assertEqual(result["reference_context"], supplied["definition"])
        self.assertEqual(result["expected_points"], [supplied["definition"]])
        self.assertNotIn("podocyte", result["reference_context"])

    def test_no_source_record_means_no_fabricated_prompt(self):
        self.assertIsNone(build_recall("neuro", []))
        self.assertIsNone(build_recall("psychiatry", [{"title": "Mood disorders"}]))

    def test_old_attempts_rotate_away_from_recently_used_record(self):
        first = record()
        second = record("proteinuria", "Proteinuria", "Proteinuria is excess protein in urine.")
        result = build_recall("renal", [first, second], [{"source_id": "complement-system"}])
        self.assertEqual(result["sources"][0]["id"], "proteinuria")

    def test_rotation_reaches_concepts_past_the_first_four(self):
        records = [record(f"concept-{i}", f"Concept {i}", f"Concept {i} is a stated process.")
                   for i in range(7)]
        attempts = [{"prompt": f"Explain the concept Concept {i}."} for i in range(5)]
        result = build_recall("renal", records, attempts)
        self.assertEqual(result["sources"][0]["id"], "concept-5")

    def test_full_cycle_returns_least_recent_and_uses_last_hundred_attempts(self):
        records = [record(f"concept-{i}", f"Concept {i}", f"Concept {i} is a stated process.")
                   for i in range(3)]
        attempts = ([{"source_id": "concept-0"}] +
                    [{"source_id": "concept-1"}, {"source_id": "concept-2"}] * 50)
        result = build_recall("renal", records, attempts)
        self.assertEqual(result["sources"][0]["id"], "concept-0")

    def test_reference_and_source_count_are_bounded(self):
        result = build_recall("renal", [record(definition="A " * 3000)])
        self.assertLessEqual(len(result["reference_context"]), 1200)
        many = [record(f"term-{i}", f"Term {i}", f"Definition {i}.") for i in range(10)]
        self.assertIn(build_recall("renal", many)["sources"][0]["id"], {f"term-{i}" for i in range(10)})
        beyond_limit = [record(f"term-{i}", f"Term {i}", f"Definition {i}.") for i in range(22)]
        attempts = [{"source_id": f"term-{i}"} for i in range(20)]
        self.assertEqual(build_recall("renal", beyond_limit, attempts)["sources"][0]["id"], "term-0")


if __name__ == "__main__":
    unittest.main()
