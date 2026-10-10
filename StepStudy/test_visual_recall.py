import json
import sqlite3
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from StepStudy.visual_recall import MAX_IMAGE_BYTES, VisualRecallStore, validate_source


def png(width=10, height=8):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)


def mask(identity="region-a", **values):
    return {"id": identity, "x": .1, "y": .2, "width": .3, "height": .4,
            "label": "Caudate", "answer": "Lateral to the lateral ventricle.", **values}


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 10, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


class VisualRecallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="step-visual-recall-")
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.db = self.root / "visual.sqlite3"
        self.store = VisualRecallStore(self.db, clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def test_source_refs_are_opaque_allowlisted_and_never_paths_or_remote_urls(self):
        self.assertEqual(validate_source({"kind": "course_asset", "id": "QWERTY_12"}),
                         {"kind": "course_asset", "id": "QWERTY_12"})
        self.assertEqual(validate_source({"kind": "intake_image", "id": 12}),
                         {"kind": "intake_image", "id": "12"})
        self.assertRaises(ValueError, validate_source, {"kind": "course_asset", "id": "/private/blocked.png"})
        self.assertRaises(ValueError, validate_source, {"kind": "remote", "id": "https://example.com/a.png"})
        self.assertRaises(ValueError, validate_source, {"kind": "upload", "id": "../../private"})
        self.assertRaises(ValueError, validate_source, {"kind": "intake_image", "id": 0})
        self.assertRaises(ValueError, validate_source, {"kind": "course_asset", "id": "id", "path": "/tmp/a"})

    def test_masks_store_normalized_geometry_and_reject_out_of_bounds_or_malformed_rectangles(self):
        row = self.store.create_exercise("Basal ganglia", {"kind": "course_asset", "id": "opaque1"},
                                         topic_ids=["neuro"], chapter_id="neuro.chapter-1", masks=[mask(x=.12501)])
        self.assertEqual(row["masks"][0]["x"], .12501)
        self.assertEqual(row["topic_ids"], ["neuro"])
        self.assertEqual(row["chapter_id"], "neuro.chapter-1")
        for bad in [mask(x=-.1), mask(x=.8, width=.3), mask(width=0), mask(x=True), mask(width=float("nan"))]:
            with self.subTest(mask=bad), self.assertRaises(ValueError):
                self.store.create_exercise("Bad", {"kind": "course_asset", "id": "opaque2"}, masks=[bad])
        with self.assertRaises(ValueError):
            self.store.create_exercise("Bad topic", {"kind": "course_asset", "id": "opaque3"}, topic_ids=["not-a-topic"])

    def test_explicit_self_ratings_schedule_history_and_request_ids_are_idempotent(self):
        row = self.store.create_exercise("Ventricular anatomy", {"kind": "intake_image", "id": 7},
                                         topic_ids=["neuro"], masks=[mask()])
        first = self.store.record_review(row["id"], "good", "request-one")
        self.assertFalse(first["duplicate"])
        self.assertEqual(first["exercise"]["review_count"], 1)
        self.assertEqual(first["exercise"]["interval_days"], 2)
        due = first["exercise"]["due_at"]
        repeated = self.store.record_review(row["id"], "good", "request-one")
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(repeated["exercise"]["review_count"], 1)
        self.assertEqual(repeated["exercise"]["due_at"], due)
        with self.assertRaises(ValueError):
            self.store.record_review(row["id"], "easy", "request-one")
        self.clock.value = datetime(2026, 10, 11, tzinfo=timezone.utc)
        second = self.store.record_review(row["id"], "easy", "request-two")
        self.assertEqual(second["exercise"]["review_count"], 2)
        self.assertEqual(second["exercise"]["interval_days"], 6)
        self.assertEqual([event["rating"] for event in self.store.get_exercise(row["id"])["history"]], ["easy", "good"])
        self.assertEqual(len(self.store.list_exercises(due_only=True)), 0)
        self.assertEqual(len(self.store.list_exercises(topic_id="neuro")), 1)

    def test_private_upload_checks_format_size_and_dimensions_and_round_trips_as_blob(self):
        source = self.store.store_upload(png(), "image/png")
        self.assertEqual(source["kind"], "upload")
        stored = self.store.get_upload(source["id"])
        self.assertEqual((stored["mime_type"], stored["size"], stored["width"], stored["height"]), ("image/png", 24, 10, 8))
        self.assertEqual(stored["body"], png())
        with self.assertRaises(ValueError):
            self.store.store_upload(b"not an image", "image/png")
        with self.assertRaises(ValueError):
            self.store.store_upload(png(10000, 10000), "image/png")
        with self.assertRaises(ValueError):
            self.store.store_upload(b"x" * (MAX_IMAGE_BYTES + 1), "image/png")
        with self.assertRaises(ValueError):
            self.store.store_upload(png(), "image/svg+xml")

    def test_create_from_image_copies_course_image_only_with_valid_exercise_atomically(self):
        with sqlite3.connect(self.db) as db:
            before = db.execute("SELECT COUNT(*) FROM visual_images").fetchone()[0]
        with self.assertRaises(ValueError):
            self.store.create_from_image("Bad course figure", png(), "image/png", masks=[mask(x=.9, width=.3)])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM visual_images").fetchone()[0], before)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM visual_exercises").fetchone()[0], 0)

        row = self.store.create_from_image("Course figure", png(), "image/png", topic_ids=["neuro"],
                                           chapter_id="neuro.chapter-1", masks=[mask()])
        self.assertEqual(row["source"]["kind"], "upload")
        self.assertEqual(row["chapter_id"], "neuro.chapter-1")
        self.assertEqual(self.store.get_upload(row["source"]["id"])["body"], png())
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM visual_images").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM visual_exercises").fetchone()[0], 1)

    def test_update_from_image_preserves_old_exercise_on_invalid_patch_and_source_survives_reload(self):
        original = self.store.create_exercise("Existing exercise", {"kind": "intake_image", "id": 8},
                                              masks=[mask()])
        patch = {"source": {"kind": "course_asset", "id": "ephemeral-token"},
                 "masks": [mask(x=.9, width=.3)]}
        with sqlite3.connect(self.db) as db:
            before = db.execute("SELECT COUNT(*) FROM visual_images").fetchone()[0]
        with self.assertRaises(ValueError):
            self.store.update_from_image(original["id"], patch, png(), "image/png")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM visual_images").fetchone()[0], before)
        unchanged = self.store.get_exercise(original["id"])
        self.assertEqual(unchanged["source"], {"kind": "intake_image", "id": "8"})
        self.assertEqual(unchanged["masks"][0]["x"], .1)

        changed = self.store.update_from_image(original["id"], {"source": patch["source"]}, png(), "image/png")
        self.assertEqual(changed["source"]["kind"], "upload")
        saved_id = changed["source"]["id"]
        reopened = VisualRecallStore(self.db, clock=self.clock)
        reloaded = reopened.get_exercise(original["id"])
        self.assertEqual(reloaded["source"], {"kind": "upload", "id": saved_id})
        self.assertEqual(reopened.get_upload(saved_id)["body"], png())

    def test_backup_includes_private_uploads_masks_and_history_and_imports_without_losing_them(self):
        source = self.store.store_upload(png(), "image/png")
        row = self.store.create_exercise("Private scan", source, topic_ids=["neuro"], masks=[mask()])
        self.store.record_review(row["id"], "hard", "backup-request")
        exported = self.store.export_state()
        self.assertEqual(exported["schema"], "step-visual-recall-v1")
        self.assertEqual(len(exported["uploads"]), 1)
        self.assertIn("body_base64", exported["uploads"][0])
        restored = VisualRecallStore(self.root / "restored.sqlite3", clock=self.clock)
        self.assertEqual(restored.import_state(exported), {"exercises": 1, "reviews": 1, "uploads": 1})
        copy = restored.get_exercise(row["id"])
        self.assertEqual(copy["masks"][0]["answer"], "Lateral to the lateral ventricle.")
        self.assertEqual(copy["history"][0]["rating"], "hard")
        self.assertEqual(restored.get_upload(source["id"])["body"], png())
        self.assertEqual(restored.import_state(exported), {"exercises": 1, "reviews": 1, "uploads": 1})
        self.assertEqual(restored.get_exercise(row["id"])["review_count"], 1)

    def test_schema_upgrade_adds_visual_tables_without_touching_existing_step_state(self):
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE existing_progress(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            db.execute("INSERT INTO existing_progress VALUES('keep','saved')")
            db.execute("PRAGMA user_version=0")
        upgraded = VisualRecallStore(self.db, clock=self.clock)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT value FROM existing_progress WHERE key='keep'").fetchone()[0], "saved")
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='visual_exercises'").fetchone())
        self.assertEqual(upgraded.list_exercises(), [])

    def test_database_path_must_remain_outside_the_checkout(self):
        with self.assertRaises(ValueError):
            VisualRecallStore(Path(__file__).with_name("must-not-exist.sqlite3"))


if __name__ == "__main__":
    unittest.main()
