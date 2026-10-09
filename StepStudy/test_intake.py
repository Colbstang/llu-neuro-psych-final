import struct
import tempfile
import unittest
from pathlib import Path

from StepStudy.intake import IntakeStore


def png(path: Path, marker: bytes = b"fixture") -> Path:
    # The intake validates the PNG signature and IHDR dimensions. Fake OCR means
    # pixel payload is deliberately unnecessary in this isolated test fixture.
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" +
                     struct.pack(">II", 16, 16) + b"\x08\x02\x00\x00\x00" + marker)
    return path


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.answers = {}
        self.store = IntakeStore(self.root / "app" / "intake.sqlite3",
                                 self.root / "app" / "screenshots",
                                 ocr=lambda path: self.answers[path.read_bytes()[-10:]])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add(self, name, marker, text):
        path = png(self.root / name, marker)
        self.answers[path.read_bytes()[-10:]] = text
        return path

    def test_classifies_parses_tags_searches_and_tracks_user_marked_signals(self):
        path = self.add("one.png", b"one", "A patient with acute stroke has weakness. Which artery is most likely involved?\nA. MCA\nB. ACA\nC. PCA\nExplanation: cortical signs.")
        record = self.store.ingest_file(path)["question"]
        self.assertEqual(record["status"], "confirmed")
        self.assertEqual(len(record["options"]), 3)
        self.assertIn("Explanation", record["full_text"])
        self.assertIn("neuro", record["topic_ids"])
        self.assertEqual(self.store.search_questions("stroke artery")[0]["id"], record["id"])
        self.store.mark_outcome(record["id"], "wrong")
        self.assertEqual(self.store.topic_signals()["neuro"]["wrong"], 1)
        self.assertEqual(self.store.topic_signals()["neuro"]["correct"], 0)

    def test_ambiguous_and_nonquestion_images_receive_conservative_states(self):
        ambiguous = self.add("ambiguous.png", b"ambiguous", "What is the diagnosis for a patient with tremor?")
        other = self.add("notes.png", b"notes", "Neurotransmitters\nDopamine\nSerotonin")
        survey = self.add("survey.png", b"survey", "Which snack do you prefer?\nA. Apples\nB. Pears\nC. Grapes")
        self.assertEqual(self.store.ingest_file(ambiguous)["question"]["status"], "needs_confirmation")
        self.assertEqual(self.store.ingest_file(other)["question"]["status"], "excluded")
        self.assertEqual(self.store.ingest_file(survey)["question"]["status"], "needs_confirmation")
        self.assertEqual(len(self.store.list_questions("needs_confirmation")), 2)

    def test_sha_deduplication_and_id_scoped_private_image_access(self):
        source = self.add("first.png", b"same", "Which diagnosis is most likely?\nA. One\nB. Two")
        duplicate = self.root / "copy.jpg"
        duplicate.write_bytes(source.read_bytes())
        self.answers[duplicate.read_bytes()[-10:]] = self.answers[source.read_bytes()[-10:]]
        first = self.store.ingest_file(source)
        again = self.store.ingest_file(duplicate)
        self.assertFalse(first["duplicate"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(self.store.image_path(first["question"]["id"]), self.store.image_path(again["question"]["id"]))
        self.store.delete_question(first["question"]["id"])
        with self.assertRaises(KeyError):
            self.store.image_path(first["question"]["id"])

    def test_user_can_override_topic_tags_with_catalog_validated_ids(self):
        path = self.add("untagged.png", b"untagged", "Which answer is correct?\nA. One\nB. Two")
        question = self.store.ingest_file(path)["question"]
        self.assertEqual(question["status"], "needs_confirmation")
        tagged = self.store.set_topics(question["id"], ["neuro", "neuro"])
        self.assertEqual(tagged["topic_ids"], ["neuro"])
        with self.assertRaises(ValueError):
            self.store.set_topics(question["id"], ["made-up-topic"])

    def test_clinical_cues_can_confirm_without_catalog_keyword_but_survey_cannot(self):
        clinical = self.add("clinical.png", b"clinical", "A patient develops aphasia. Which artery is occluded?\nA. Anterior cerebral artery\nB. Middle cerebral artery")
        clinical_question = self.store.ingest_file(clinical)["question"]
        self.assertEqual(clinical_question["status"], "confirmed")
        self.assertEqual(clinical_question["topic_ids"], ["neuro"])
        survey = self.add("preference.png", b"preference", "Which option do you prefer?\nA. One\nB. Two")
        self.assertEqual(self.store.ingest_file(survey)["question"]["status"], "needs_confirmation")

    def test_ocr_failure_is_counted_cannot_be_confirmed_and_can_retry_in_place(self):
        source = self.add("retry.png", b"retry", "unused")
        self.store.ocr = lambda path: (_ for _ in ()).throw(RuntimeError("Vision temporarily unavailable"))
        failed = self.store.ingest_file(source)["question"]
        question_id = failed["id"]
        image = self.store.image_path(question_id)
        self.assertEqual(failed["full_text"], "")
        self.assertIn("temporarily unavailable", failed["error"])
        status = self.store.status()["counts"]
        self.assertEqual(status["error"], 1)
        self.assertEqual(status.get("confirmed", 0), 0)
        with self.assertRaises(ValueError):
            self.store.confirm(question_id)

        self.store.ocr = lambda path: "A patient develops aphasia. Which artery is occluded?\nA. Anterior cerebral artery\nB. Middle cerebral artery"
        retried = self.store.retry_question(question_id)
        self.assertEqual(retried["id"], question_id)
        self.assertEqual(retried["status"], "confirmed")
        self.assertEqual(retried["error"], "")
        self.assertEqual(retried["topic_ids"], ["neuro"])
        self.assertEqual(self.store.image_path(question_id), image)
        self.assertEqual(self.store.status()["counts"]["error"], 0)
        self.assertEqual(len(self.store.list_questions()), 1)

    def test_empty_ocr_output_is_reported_as_failure(self):
        source = self.add("blank-ocr.png", b"blank", "unused")
        self.store.ocr = lambda path: "  \n"
        record = self.store.ingest_file(source)["question"]
        self.assertEqual(record["full_text"], "")
        self.assertIn("no readable text", record["error"])
        self.assertEqual(self.store.status()["counts"]["error"], 1)

    def test_watcher_baselines_existing_files_and_waits_for_stable_new_image(self):
        folder = self.root / "watch"
        folder.mkdir()
        baseline = self.add("baseline.png", b"base", "Which condition?\nA. One\nB. Two")
        baseline.rename(folder / "existing.png")
        self.store.start_watch(folder)
        self.assertEqual(self.store.poll_watch()["processed"], [])
        watch_path = png(folder / "arriving.png", b"watch")
        self.answers[watch_path.read_bytes()[-10:]] = "A patient has a stroke. Which condition?\nA. One\nB. Two"
        self.assertEqual(self.store.poll_watch()["processed"], [])
        self.assertEqual(self.store.poll_watch()["processed"][0]["question"]["status"], "confirmed")
        self.assertEqual(self.store.status()["counts"]["confirmed"], 1)
        self.store.stop_watch()
        self.assertFalse(self.store.watch_status()["running"])

    def test_explicit_scan_includes_existing_and_reports_bad_images(self):
        folder = self.root / "scan"
        folder.mkdir()
        self.add("scan.png", b"scan", "A patient has a stroke. Which diagnosis?\nA. One\nB. Two").rename(folder / "scan.png")
        (folder / "broken.jpg").write_bytes(b"not an image")
        counts = self.store.scan_folder(folder)
        self.assertEqual(counts["confirmed"], 1)
        self.assertEqual(len(counts["errors"]), 1)

    def test_unavailable_watch_or_scan_folder_returns_errors_without_raising(self):
        missing = self.root / "missing"
        self.assertFalse(self.store.start_watch(missing)["running"])
        self.assertTrue(self.store.watch_status()["errors"])
        self.assertTrue(self.store.scan_folder(missing)["errors"])

    def test_explicit_scan_has_a_bounded_batch(self):
        folder = self.root / "large-scan"
        folder.mkdir()
        text = "A patient has a stroke. Which artery?\nA. One\nB. Two"
        for index in range(21):
            self.add(f"batch-{index:02}.png", f"batch-{index}".encode(), text).rename(folder / f"batch-{index:02}.png")
        counts = self.store.scan_folder(folder)
        self.assertEqual(counts["scanned"], 20)
        self.assertTrue(counts["truncated"])


if __name__ == "__main__":
    unittest.main()
