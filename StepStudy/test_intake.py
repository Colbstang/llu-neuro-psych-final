import struct
import sqlite3
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

    def test_restart_migrates_legacy_search_index_without_losing_question_or_repair(self):
        path = self.add("restart.png", b"restart", "A patient with a stroke has weakness. Which option fits?\nA. One\nB. Two")
        question = self.store.ingest_file(path)["question"]
        self.store.close()
        with sqlite3.connect(self.root / "app" / "intake.sqlite3") as db:
            db.executescript("""
              DROP TRIGGER questions_fts_insert;
              DROP TRIGGER questions_fts_update;
              DROP TABLE questions_fts;
              CREATE VIRTUAL TABLE questions_fts USING fts5(full_text,stem,options,content='questions',content_rowid='id');
            """)
        self.store = IntakeStore(self.root / "app" / "intake.sqlite3", self.root / "app" / "screenshots")
        self.assertEqual(self.store.search_questions("stroke")[0]["id"], question["id"])
        self.store.save_repair(question["id"], {"correction": "Checked synthetic distinction", "source_reference": "Synthetic source p. 1"})
        self.store.close()
        self.store = IntakeStore(self.root / "app" / "intake.sqlite3", self.root / "app" / "screenshots")
        self.store.mark_outcome(question["id"], "wrong")
        self.assertEqual(self.store.get_repair(question["id"])["repair"]["correction"], "Checked synthetic distinction")
        self.assertEqual(self.store.search_questions("stroke")[0]["id"], question["id"])

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
        self.assertEqual(tagged["tag_evidence"], [{"subject_id": "neuro", "confidence": "reviewed", "evidence": []}])
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

    def course_fixture(self):
        return {"pages": [
            {"id": "ch-gaze", "title": "Horizontal gaze and brainstem", "keywords": ["gaze palsy", "cranial nerve"]},
            {"id": "ch-vessels", "title": "Cerebral vessels", "keywords": ["stroke", "middle cerebral artery"]}],
            "topic_groups": {"groups": [
                {"id": "brainstem", "title": "Brainstem pathways", "page_ids": ["ch-gaze"]},
                {"id": "vascular", "title": "Vascular disease", "page_ids": ["ch-vessels", "ch-gaze"]}]}}

    def test_multiple_subject_and_chapter_links_have_reviewable_evidence(self):
        self.store.configure_course(self.course_fixture())
        image = self.add("multi.png", b"multi", "A patient has a stroke, cranial nerve palsy, and delirium. Which diagnosis?\nA. One\nB. Two")
        question = self.store.ingest_file(image)["question"]
        self.assertEqual(set(question["topic_ids"]), {"neuro", "psychiatry"})
        self.assertEqual({link["chapter_id"] for link in question["course_links"]}, {"ch-gaze", "ch-vessels"})
        self.assertEqual({link["topic_id"] for link in question["course_links"] if link["chapter_id"] == "ch-gaze"}, {"brainstem", "vascular"})
        self.assertTrue(all(link["confidence"] == "keyword" and link["evidence"] for link in question["course_links"]))
        self.assertEqual(self.store.chapter_link_counts(), {"ch-gaze": 1, "ch-vessels": 1})
        self.assertEqual(self.store.questions_for_chapter("ch-vessels")[0]["id"], question["id"])
        reviewed = self.store.set_course_links(question["id"], ["ch-vessels", "ch-vessels"])
        self.assertEqual(len(reviewed["course_links"]), 1)
        self.assertEqual(reviewed["course_links"][0]["confidence"], "reviewed")
        with self.assertRaises(ValueError):
            self.store.set_course_links(question["id"], ["missing-chapter"])
        self.store.delete_question(question["id"])
        self.assertEqual(self.store.chapter_link_counts(), {})
        self.assertEqual(self.store.questions_for_chapter("ch-vessels"), [])

    def test_capture_cancel_does_nothing_and_explicit_figure_keeps_context_reviewable(self):
        self.store.configure_course(self.course_fixture())
        self.store.capture = lambda target: False
        cancelled = self.store.capture_region({"subject_id": "neuro", "chapter_id": "ch-gaze"})
        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(self.store.list_questions(), [])
        def capture(target):
            png(target, b"figure")
            return True
        self.store.capture = capture
        self.store.ocr = lambda path: "A teaching diagram with labels"
        result = self.store.capture_region({"subject_id": "neuro", "chapter_id": "ch-gaze"})
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["question"]["status"], "needs_confirmation")
        self.assertEqual(result["question"]["topic_ids"], ["neuro"])
        self.assertEqual(result["question"]["course_links"][0]["confidence"], "context")
        self.assertEqual(result["question"]["tag_evidence"][0]["confidence"], "context")
        self.assertEqual(len(self.store.list_questions()), 1)
        self.assertFalse(any(path.name.startswith("capture-") for path in self.store.image_dir.iterdir()))

    def test_existing_intake_state_survives_schema_extension_and_reopening(self):
        question = self.store.ingest_file(self.add("existing.png", b"existing", "A patient has a stroke. Which diagnosis?\nA. One\nB. Two"))["question"]
        self.store.mark_outcome(question["id"], "wrong")
        reopened = IntakeStore(self.store.path, self.store.image_dir, ocr=lambda _: "unused")
        row = reopened.get_question(question["id"])
        self.assertEqual(row["outcome"], "wrong")
        self.assertEqual(row["stem"], question["stem"])
        self.assertEqual(row["course_links"], [])
        self.assertTrue(reopened.image_path(row["id"]).is_file())

    def test_distinctive_metadata_titles_link_multiple_chapters_but_generic_labels_do_not(self):
        self.store.configure_course({"pages": [
            {"id": "stroke-map", "title": "Stroke localization"},
            {"id": "vessels", "title": "Cerebral vessels", "blocks": [{"id": "stroke-detail", "title": "Stroke mechanisms"}]},
            {"id": "generic", "title": "Clinical review and treatment mechanisms"}]})
        links, _ = self.store._automatic_links("A patient has a stroke. Review the clinical treatment.")
        self.assertEqual({link["chapter_id"] for link in links}, {"stroke-map", "vessels"})
        self.assertTrue(all("stroke" in link["evidence"] for link in links))
        generic, _ = self.store._automatic_links("Clinical review and treatment mechanisms")
        self.assertEqual(generic, [])

    def repair_question(self, marker=b"repair"):
        self.store.configure_course(self.course_fixture())
        row = self.store.ingest_file(self.add("repair-"+marker.decode()+".png", marker,
            "A patient has a stroke and delirium. Which diagnosis?\nA. One\nB. Two\nExplanation: original wording."))["question"]
        self.store.mark_outcome(row["id"], "wrong")
        return self.store.get_question(row["id"])

    def test_repair_notes_are_additive_private_and_source_reference_is_required_for_correction(self):
        row = self.repair_question()
        saved = self.store.save_repair(row["id"], {"confidence": "high", "reason_fooled_me": "I overread a clue",
            "missing_distinction": "Distinguish two possibilities", "correction": "My checked correction",
            "source_reference": "Synthetic reference p. 4", "follow_up_prompt": "What distinction changes the answer?"})
        self.assertEqual(saved["question"]["outcome"], "wrong")
        self.assertEqual(saved["question"]["full_text"], row["full_text"])
        self.assertEqual(saved["question"]["topic_ids"], row["topic_ids"])
        self.assertEqual(saved["question"]["course_links"], row["course_links"])
        self.assertEqual(saved["question"]["repair_priority"], 100)
        updated = self.store.save_repair(row["id"], {"reason_fooled_me": "A revised note"})
        self.assertEqual(updated["repair"]["correction"], "My checked correction")
        with self.assertRaises(ValueError):
            self.store.save_repair(row["id"], {"source_reference": ""})
        with self.assertRaises(ValueError):
            self.store.save_repair(row["id"], {"confidence": "mastered"})
        with self.assertRaises(ValueError):
            self.store.save_repair(row["id"], {"outcome": "correct"})
        reopened = IntakeStore(self.store.path, self.store.image_dir, ocr=lambda _: "unused")
        self.assertEqual(reopened.get_repair(row["id"])["repair"]["reason_fooled_me"], "A revised note")
        self.assertEqual(reopened.get_question(row["id"])["outcome"], "wrong")

    def test_independent_retry_is_idempotent_and_does_not_resolve_original_outcome(self):
        row = self.repair_question()
        self.store.clock = lambda: 1780000000.0
        result = self.store.record_retry(row["id"], "My independent answer", "recalled", "retry-unique-1")
        self.assertEqual(result["repair"]["retry_count"], 1)
        self.assertEqual(result["repair"]["history"][0]["answer"], "My independent answer")
        self.assertEqual(result["repair"]["history"][0]["self_result"], "recalled")
        self.assertEqual(result["repair"]["next_retry_at"], 1780000000.0 + 3 * 86400)
        self.assertEqual(result["question"]["outcome"], "wrong")
        self.assertGreater(result["question"]["repair_priority"], 0)
        self.assertEqual(self.store.topic_signals()["neuro"]["wrong"], 1)
        repeated = self.store.record_retry(row["id"], "My independent answer", "recalled", "retry-unique-1")
        self.assertEqual(repeated["repair"]["retry_count"], 1)
        self.assertEqual(repeated["repair"]["next_retry_at"], result["repair"]["next_retry_at"])
        with self.assertRaises(ValueError):
            self.store.record_retry(row["id"], "Changed answer", "recalled", "retry-unique-1")
        with self.assertRaises(ValueError):
            self.store.record_retry(row["id"], "Answer", "correct", "retry-unique-2")
        with self.assertRaises(ValueError):
            self.store.record_retry(row["id"], " ", "needs_practice", "retry-unique-2")

    def test_retry_history_snapshots_source_notes_and_private_export_keeps_full_history(self):
        row = self.repair_question()
        self.store.save_repair(row["id"], {"correction": "First correction", "source_reference": "Source A p. 1", "follow_up_prompt": "First prompt"})
        self.store.clock = lambda: 1780000000.0
        self.store.record_retry(row["id"], "First answer", "needs_practice", "retry-history-1")
        self.store.save_repair(row["id"], {"correction": "Second correction", "source_reference": "Source B p. 2", "follow_up_prompt": "Second prompt"})
        self.store.clock = lambda: 1780000300.0
        self.store.record_retry(row["id"], "Second answer", "recalled", "retry-history-2")
        recent = self.store.get_repair(row["id"], history_limit=1)["repair"]
        self.assertTrue(recent["history_partial"])
        self.assertEqual(recent["history"][0]["prompt"], "Second prompt")
        exported = self.store.export_repairs()
        item = exported["questions"][0]
        self.assertEqual(item["topic_ids"], ["neuro", "psychiatry"])
        self.assertEqual(item["course_links"], row["course_links"])
        history = item["repair"]["history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["correction"], "First correction")
        self.assertEqual(history[0]["source_reference"], "Source A p. 1")
        self.assertNotIn("image_name", str(exported))
        self.assertNotIn(str(self.root), str(exported))

    def test_confident_wrong_questions_take_priority_and_only_explicit_outcome_changes_resolve_them(self):
        confident = self.repair_question(b"confident")
        uncertain = self.repair_question(b"uncertain")
        self.store.save_repair(confident["id"], {"confidence": "high"})
        self.store.save_repair(uncertain["id"], {"confidence": "low"})
        self.store.record_retry(confident["id"], "A recalled distinction", "recalled", "retry-priority-1")
        self.assertEqual(self.store.repair_queue(due_only=False)[0]["id"], confident["id"])
        self.store.mark_outcome(confident["id"], "correct")
        self.assertEqual([item["id"] for item in self.store.repair_queue()], [uncertain["id"]])

    def test_repair_queue_waits_until_next_retry_for_both_explicit_results(self):
        new = self.repair_question(b"new")
        needs = self.repair_question(b"needs")
        recalled = self.repair_question(b"recalled")
        now = 1780000000.0
        self.store.clock = lambda: now
        self.store.record_retry(needs["id"], "Independent attempt", "needs_practice", "retry-due-needs")
        self.store.record_retry(recalled["id"], "Independent attempt", "recalled", "retry-due-recalled")
        self.assertEqual([item["id"] for item in self.store.repair_queue()], [new["id"]])
        self.assertEqual(self.store.repair_counts(), {"total": 3, "due": 1, "scheduled": 2,
            "new": 1, "earliest_due_at": now + 86400})
        self.assertEqual(len(self.store.repair_queue(due_only=False)), 3)
        self.store.clock = lambda: now + 86400
        self.assertEqual({item["id"] for item in self.store.repair_queue()}, {new["id"], needs["id"]})
        self.store.clock = lambda: now + 3 * 86400
        self.assertEqual(len(self.store.repair_queue()), 3)
        self.assertEqual(self.store.topic_signals()["neuro"]["wrong"], 3)
        self.assertTrue(all(self.store.get_question(row["id"])["outcome"] == "wrong" for row in (new, needs, recalled)))

    def test_repair_queue_filters_subject_before_limit_and_counts_without_source_text(self):
        neuro = self.repair_question(b"neuro")
        psychiatry = self.repair_question(b"psychiatry")
        self.store.set_topics(neuro["id"], ["neuro"])
        self.store.set_topics(psychiatry["id"], ["psychiatry"])
        self.store.save_repair(psychiatry["id"], {"confidence": "high"})
        self.assertEqual(self.store.repair_queue(1)[0]["id"], psychiatry["id"])
        self.assertEqual(self.store.repair_queue(1, topic_id="neuro")[0]["id"], neuro["id"])
        self.assertEqual(self.store.repair_counts("neuro"), {"total": 1, "due": 1, "scheduled": 0,
            "new": 1, "earliest_due_at": None})
        with self.assertRaises(ValueError): self.store.repair_queue(topic_id="unknown")

    def test_repair_schema_upgrade_preserves_existing_question_and_links(self):
        row = self.repair_question()
        with self.store._connect() as db:
            db.execute("DROP TABLE question_retries")
            db.execute("ALTER TABLE questions DROP COLUMN repair_json")
        reopened = IntakeStore(self.store.path, self.store.image_dir, ocr=lambda _: "unused")
        upgraded = reopened.get_repair(row["id"])
        self.assertEqual(upgraded["question"]["outcome"], "wrong")
        self.assertEqual(upgraded["question"]["topic_ids"], row["topic_ids"])
        self.assertEqual(upgraded["question"]["course_links"], row["course_links"])
        self.assertEqual(upgraded["repair"]["confidence"], "unknown")
        self.assertEqual(upgraded["repair"]["history"], [])

    def test_deleted_question_repair_cannot_be_changed_or_exported(self):
        row = self.repair_question()
        self.store.save_repair(row["id"], {"confidence": "high"})
        self.store.delete_question(row["id"])
        with self.assertRaises(KeyError):
            self.store.get_repair(row["id"])
        with self.assertRaises(KeyError):
            self.store.save_repair(row["id"], {"confidence": "low"})
        with self.assertRaises(KeyError):
            self.store.record_retry(row["id"], "answer", "recalled", "retry-deleted-1")
        self.assertEqual(self.store.export_repairs()["questions"], [])


if __name__ == "__main__":
    unittest.main()
