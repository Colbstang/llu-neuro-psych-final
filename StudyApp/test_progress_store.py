from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .progress_store import ProgressStore


class ProgressStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "progress.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_shared_store_across_http_worker_threads(self):
        with ProgressStore(self.db) as store:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda index: store.apply_patch({'notes': {str(index): 'saved'}}, 0), range(16)))
            self.assertEqual(len(store.load()['state']['notes']), 16)
            self.assertEqual(store.load()['revision'], 16)
        with ProgressStore(self.db) as reopened:
            self.assertEqual(len(reopened.load()['state']['notes']), 16)

    def test_empty_first_run_and_restart(self):
        with ProgressStore(self.db) as store:
            self.assertEqual(store.load(), {"state": {}, "revision": 0, "updated_at": "", "exists": False})
            result = store.apply_patch({"notes": {"section-a": "remember"}})
            self.assertEqual(result["revision"], 1)
            self.assertTrue(result["exists"])
        with ProgressStore(self.db) as store:
            self.assertEqual(store.load()["state"], {"notes": {"section-a": "remember"}})
            self.assertEqual(store.load()["revision"], 1)

    @unittest.skipIf(os.name == "nt", "POSIX file mode bits are unavailable on Windows")
    def test_private_database_permissions(self):
        with ProgressStore(self.db) as store:
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(store.path.parent.stat().st_mode), 0o700)

    def test_stale_interleaved_patches_merge(self):
        with ProgressStore(self.db) as store:
            first = store.apply_patch({"notes": {"main": "a"}})
            stale = first["revision"]
            store.apply_patch({"book": {"page-1": {"label": "child"}}}, stale)
            result = store.apply_patch({"notes": {"pane": "b"}}, stale)
            self.assertEqual(result["state"], {"notes": {"main": "a", "pane": "b"}, "book": {"page-1": {"label": "child"}}})

    def test_stale_interleaved_separate_connections(self):
        with ProgressStore(self.db) as main, ProgressStore(self.db) as child:
            stale = main.apply_patch({"notes": {"main": "a"}})["revision"]
            child.apply_patch({"book": {"page-1": {"label": "child"}}}, stale)
            main.apply_patch({"notes": {"pane": "b"}}, stale)
            self.assertEqual(main.load()["state"], {"notes": {"main": "a", "pane": "b"}, "book": {"page-1": {"label": "child"}}})

    def test_highlight_array_delta_stale_add_remove(self):
        with ProgressStore(self.db) as store:
            base = store.apply_patch({"readingHighlights": {"__llu_array_delta__": {"remove": [], "upsert": [{"id": "a", "quote": "A"}]}}})
            stale = base["revision"]
            store.apply_patch({"readingHighlights": {"__llu_array_delta__": {"remove": [], "upsert": [{"id": "b", "quote": "B"}]} }}, stale)
            result = store.apply_patch({"readingHighlights": {"__llu_array_delta__": {"remove": ["a"], "upsert": []}}}, stale)
            self.assertEqual(result["state"]["readingHighlights"], [{"id": "b", "quote": "B"}])

    def test_invalid_patch_rolls_back(self):
        with ProgressStore(self.db) as store:
            before = store.apply_patch({"notes": {"x": "safe"}})
            with self.assertRaises(ValueError):
                store.apply_patch({"highlights": {"__llu_array_delta__": {"remove": ["x"], "upsert": [{"quote": "missing id"}]}}})
            after = store.load()
            self.assertEqual(after["state"], before["state"])
            self.assertEqual(after["revision"], before["revision"])

    def test_import_backup_and_export(self):
        with ProgressStore(self.db) as store:
            store.apply_patch({"old": True})
            store.import_state({"new": [1, 2, 3]})
            self.assertEqual(store.export(), {"new": [1, 2, 3]})
            target = Path(self.temp.name) / "export.json"
            store.export(target)
            self.assertEqual(json.loads(target.read_text()), {"new": [1, 2, 3]})
            backups = list((self.db.parent / "backups").glob("progress-*.sqlite3"))
            self.assertTrue(backups)


if __name__ == "__main__":
    unittest.main()
