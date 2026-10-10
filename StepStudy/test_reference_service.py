import json
import tempfile
import unittest
from pathlib import Path
from StepStudy.reference_service import ReferenceService


class ReferenceServiceTests(unittest.TestCase):
    def test_existing_backend_is_not_launched_or_stopped(self):
        launches = []
        service = ReferenceService(Path("unused"), probe=lambda: True, spawn=lambda *a, **kw: launches.append(a))
        self.assertTrue(service.start())
        service.close()
        self.assertEqual(launches, [])

    def test_configured_backend_launch_is_bounded_and_uses_fixed_port(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            interpreter = root / "python"
            interpreter.write_text("synthetic")
            interpreter.chmod(0o700)
            script = root / "semantic_search.py"
            script.write_text("synthetic")
            (root / "reference-service.json").write_text(json.dumps({"python_path": str(interpreter), "script_path": str(script), "reference_root": str(root)}))
            launches = []
            class Process:
                stopped = False
                def poll(self): return None
                def terminate(self): self.stopped = True
            child = Process()
            def spawn(*args, **kwargs):
                launches.append((args, kwargs))
                return child
            service = ReferenceService(root, probe=lambda: False, spawn=spawn)
            service.start(); service.start()
            self.assertEqual(len(launches), 1)
            self.assertEqual(launches[0][0][0][-4:], ["--port", "8768", "--reference-root", str(root.resolve())])
            self.assertTrue(launches[0][1]["start_new_session"])
            service.close()
            self.assertTrue(child.stopped)

    def test_invalid_private_configuration_never_launches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "reference-service.json").write_text('{"script_path":"arbitrary.py"}')
            launches = []
            service = ReferenceService(root, probe=lambda: False, spawn=lambda *a, **kw: launches.append(a))
            service.start()
            self.assertEqual(launches, [])


if __name__ == "__main__": unittest.main()
