"""The course content surface uses our runtime and passive JSON only."""
import json
import re
import unittest

from StepStudy.course_reader import reader_html


class CourseReaderTests(unittest.TestCase):
    def test_composer_preserves_full_data_without_executable_source_html(self):
        data = {"pages": [{"id": "synthetic", "html": "</script><script>untrusted_sentinel()</script>"}],
                "objectives": [{"id": "objective-one", "text": "Verbatim test objective"}],
                "questions": [{"id": "question-one", "stem": "Synthetic question"}], "assets": {}}
        page, csp = reader_html(data, {"notes": [], "cards": []}, "test-token", view="objectives", scope="week8")
        text = page.decode()
        self.assertNotIn("<script>untrusted_sentinel()", text)
        match = re.search(r'const DATA=(.*?);</script>', text)
        self.assertEqual(json.loads(match.group(1)), data)
        self.assertIn('"apiBase":"/api/course"', text)
        self.assertIn('"view":"objectives"', text)
        self.assertIn('"scope":"week8"', text)
        self.assertIn('id="guide"', text)
        self.assertIn("StepCourseReady", text)
        self.assertIn("frame-ancestors 'self'", csp)
        nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
        inline_scripts = re.findall(r'<script([^>]*)>', text)
        self.assertTrue(all('src=' in attributes or f'nonce="{nonce}"' in attributes for attributes in inline_scripts))
        self.assertNotIn("unsafe-eval", csp)

    def test_navigation_parameters_are_allowlisted(self):
        for options in ({"view": "../private"}, {"subject": "renal"}, {"scope": "injected"}, {"target": "unknown"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                reader_html({}, {}, "token", **options)


if __name__ == "__main__":
    unittest.main()
