import json
import unittest
from pathlib import Path
from unittest.mock import patch

from StepStudy import grading
from StepStudy.grading import MODEL, grade_recall


GOOD = {
    "assessed": True, "score": 0.75, "confidence": 0.8,
    "feedback": "You described the cascade and its effect.",
    "missed_concepts": ["The source also notes membrane attack."],
    "citations": ["complement-system"],
}
SOURCES = [{"id": "complement-system", "title": "Complement system", "name": "MDWiki"}]
POINTS = ["The cascade promotes inflammation.", "The cascade attacks pathogen membranes."]


class GradingTests(unittest.TestCase):
    def setUp(self):
        self.status = patch("StepStudy.grading.provider_status", return_value={
            "available": True, "provider": "codex-cli", "model": MODEL, "reason": None})
        self.status.start()
        self.addCleanup(self.status.stop)

    def test_cli_discovery_prefers_compatible_desktop_bundle_over_stale_path_cli(self):
        bundled = Path("/Applications/ChatGPT.app") / grading._BUNDLED_CLI_RELATIVE
        with patch.object(Path, "is_file", autospec=True, side_effect=lambda path: path == bundled), \
                patch.object(grading.os, "access", return_value=True), \
                patch.object(grading.shutil, "which", return_value="stale-path-codex") as which:
            self.assertEqual(grading._codex_path(), str(bundled))
            which.assert_not_called()
        with patch.object(Path, "is_file", return_value=False), \
                patch.object(grading.shutil, "which", return_value="path-codex"):
            self.assertEqual(grading._codex_path(), "path-codex")

    def test_explicit_grade_uses_only_single_answer_and_bounded_reference(self):
        seen = []

        def transport(prompt, timeout):
            seen.append((prompt, timeout))
            return GOOD

        result = grade_recall("Explain complement.", "It is a cascade.", POINTS, SOURCES,
                              "Source says it promotes inflammation.", transport=transport)
        self.assertTrue(result["assessed"])
        self.assertEqual(result["score"], 0.75)
        self.assertEqual(result["status"], "graded")
        self.assertIn('"student_answer": "It is a cascade."', seen[0][0])
        self.assertIn("Source says it promotes inflammation.", seen[0][0])
        self.assertNotIn("Anki", seen[0][0])
        self.assertLessEqual(seen[0][1], 90)

    def test_hallucinated_citation_fails_closed(self):
        result = grade_recall("Q", "A", POINTS, SOURCES,
                              transport=lambda *_: {**GOOD, "citations": ["private-bank"]})
        self.assertFalse(result["assessed"])
        self.assertIsNone(result["score"])
        self.assertEqual(result["status"], "invalid_response")

    def test_malformed_and_out_of_range_scores_never_become_success(self):
        malformed = {**GOOD, "score": "0.8"}
        out_of_range = {**GOOD, "score": 1.1}
        boolean_score = {**GOOD, "score": True}
        no_citation = {**GOOD, "citations": []}
        for payload in (malformed, out_of_range, boolean_score, no_citation, {"score": 0.8}):
            with self.subTest(payload=payload):
                result = grade_recall("Q", "A", POINTS, SOURCES,
                                      transport=lambda *_: payload)
                self.assertFalse(result["assessed"])
                self.assertIsNone(result["score"])
                self.assertEqual(result["status"], "invalid_response")

    def test_timeout_is_not_a_score(self):
        def timeout(*_):
            raise TimeoutError()
        result = grade_recall("Q", "A", POINTS, SOURCES, transport=timeout)
        self.assertFalse(result["assessed"])
        self.assertIsNone(result["score"])
        self.assertEqual(result["status"], "timeout")

    def test_unavailable_provider_and_empty_answer_never_fake_a_grade(self):
        with patch("StepStudy.grading.provider_status", return_value={
                "available": False, "provider": "codex-cli", "model": MODEL,
                "reason": "Codex CLI is not signed in."}):
            result = grade_recall("Q", "A", POINTS, SOURCES, transport=lambda *_: GOOD)
        self.assertFalse(result["assessed"])
        self.assertIsNone(result["score"])
        self.assertEqual(result["status"], "unavailable")
        empty = grade_recall("Q", " ", POINTS, SOURCES, transport=lambda *_: GOOD)
        self.assertFalse(empty["assessed"])
        self.assertIsNone(empty["score"])

    def test_invalid_sources_and_rubric_fail_closed_without_calling_model(self):
        def unexpected(*_):
            self.fail("transport should not run")
        self.assertIsNone(grade_recall("Q", "A", [], SOURCES, transport=unexpected)["score"])
        self.assertIsNone(grade_recall("Q", "A", POINTS, [], transport=unexpected)["score"])

    def test_backend_errors_fail_closed(self):
        result = grade_recall("Q", "A", POINTS, SOURCES,
                              transport=lambda *_: (_ for _ in ()).throw(RuntimeError()))
        self.assertFalse(result["assessed"])
        self.assertIsNone(result["score"])
        self.assertEqual(result["status"], "unavailable")

    def test_fake_cli_process_is_registered_and_removed_after_response(self):
        seen = []
        class FakeProcess:
            pid = 987654

            def __init__(self, command, **_kwargs):
                self.command = command
                self.returncode = None
                seen.append((command, _kwargs))

            def communicate(self, _input=None, timeout=None):
                output_path = Path(self.command[self.command.index("--output-last-message") + 1])
                output_path.write_text(json.dumps(GOOD), encoding="utf-8")
                self.returncode = 0
                return b"", b""

            def poll(self):
                return self.returncode

        with patch.object(grading, "_SHUTTING_DOWN", False), \
                patch.object(grading, "_codex_path", return_value="fake-codex"), \
                patch.object(grading.subprocess, "Popen", side_effect=FakeProcess):
            result = grading._cli_transport("synthetic prompt", timeout=1)
        self.assertEqual(result, GOOD)
        self.assertEqual(grading._ACTIVE_PROCESSES, set())
        command, options = seen[0]
        self.assertEqual(command[:4], ["fake-codex", "--model", MODEL, "exec"])
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--ephemeral", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        configs = [command[index+1] for index, value in enumerate(command) if value == "--config"]
        self.assertIn('web_search="disabled"', configs)
        self.assertIn("project_doc_max_bytes=0", configs)
        disabled = {command[index+1] for index, value in enumerate(command) if value == "--disable"}
        self.assertTrue({"shell_tool", "unified_exec", "view_image", "apps", "plugins", "multi_agent", "memories"}.issubset(disabled))
        self.assertTrue(options["start_new_session"])
        self.assertEqual(options["stderr"], grading.subprocess.DEVNULL)
        self.assertFalse(Path(options["cwd"]).exists())

    def test_cli_timeout_kills_only_owned_process_group_and_removes_registration(self):
        class FakeProcess:
            pid = 987654
            returncode = None
            communications = 0
            def communicate(self, _input=None, timeout=None):
                self.communications += 1
                if timeout is not None:
                    raise grading.subprocess.TimeoutExpired("fake-codex", timeout)
                self.returncode = -9
                return b"", b""
            def poll(self): return self.returncode
        process = FakeProcess()
        with patch.object(grading, "_SHUTTING_DOWN", False), \
                patch.object(grading, "_codex_path", return_value="fake-codex"), \
                patch.object(grading.subprocess, "Popen", return_value=process), \
                patch.object(grading, "_kill_process") as kill:
            with self.assertRaises(TimeoutError): grading._cli_transport("synthetic prompt", timeout=1)
        kill.assert_called_once_with(process)
        self.assertEqual(process.communications, 2)
        self.assertEqual(grading._ACTIVE_PROCESSES, set())

    def test_failed_cli_exit_never_accepts_an_answer_file(self):
        class FakeProcess:
            pid = 987654
            def __init__(self, command, **_kwargs): self.command = command; self.returncode = None
            def communicate(self, _input=None, timeout=None):
                output = Path(self.command[self.command.index("--output-last-message") + 1])
                output.write_text(json.dumps(GOOD))
                self.returncode = 1
                return b"", b""
            def poll(self): return self.returncode
        with patch.object(grading, "_SHUTTING_DOWN", False), \
                patch.object(grading, "_codex_path", return_value="fake-codex"), \
                patch.object(grading.subprocess, "Popen", side_effect=FakeProcess):
            result = grade_recall("Synthetic question", "Synthetic answer", POINTS, SOURCES)
        self.assertFalse(result["assessed"])
        self.assertIsNone(result["score"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(grading._ACTIVE_PROCESSES, set())

    def test_shutdown_cancels_only_registered_fake_children(self):
        class FakeProcess:
            pid = 12345

        active, unrelated = FakeProcess(), FakeProcess()
        with grading._PROCESS_LOCK:
            grading._ACTIVE_PROCESSES.clear()
            grading._ACTIVE_PROCESSES.add(active)
        try:
            with patch.object(grading, "_kill_process") as kill:
                grading.cancel_active_grading()
            kill.assert_called_once_with(active)
            self.assertEqual(grading._ACTIVE_PROCESSES, {active})
            self.assertTrue(grading._SHUTTING_DOWN)
            self.assertNotIn(unrelated, grading._ACTIVE_PROCESSES)
        finally:
            with grading._PROCESS_LOCK:
                grading._ACTIVE_PROCESSES.clear()
            grading._SHUTTING_DOWN = False


if __name__ == "__main__":
    unittest.main()
