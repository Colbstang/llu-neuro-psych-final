"""Compose an in-app course reader from trusted code and passive private JSON.

The old course bundle is a data source only. Its HTML and scripts never become
the document served by this module.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = PROJECT / "CourseModule" if (PROJECT / "CourseModule").is_dir() else PROJECT
CSS_FILES = (
    "guide_style.css", "objective_style.css", "reading_tools.css",
    "continuous_style.css", "workspace_style.css", "book_highlights.css",
    "freehand.css", "study_practice.css", "anki_context.css",
    "objective_compact.css", "week8_style.css", "image_navigation.css",
    "semantic_search.css", "source_viewer.css", "reference_workspace.css",
    "StudyApp/study_app.css", "study_dashboard.css",
)
JS_FILES = (
    "guide_app.js", "study_scope.js", "reference_panel.js", "image_navigation.js",
    "book_highlights.js", "objective_ui.js", "workspace_views.js",
    "reading_tools.js", "freehand.js", "study_practice.js", "guide_skim.js",
    "anki_context.js", "semantic_search.js", "source_viewer.js",
    "reference_workspace.js", "StudyApp/study_app.js", "study_dashboard.js",
)
COURSE_VIEWS = frozenset({"guide", "objectives", "questions", "pathology", "drugs", "bugs"})


def _script_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def reader_html(data: dict, anki: dict, token: str, *, view="guide", subject="neuro", target="both", scope=None, source="bundle") -> tuple[bytes, str]:
    """Return only our own template/runtime, with a nonce-based script policy."""
    if view not in COURSE_VIEWS or subject not in {"neuro", "psychiatry"}:
        raise ValueError("Unknown course view or subject")
    if target not in {"both", "step", "in-house"} or scope not in {None, "all", "week8"}:
        raise ValueError("Unknown course scope")
    nonce = secrets.token_urlsafe(24)
    template = (SOURCE_ROOT / "guide_template.html").read_text(encoding="utf-8")
    css = "\n".join((SOURCE_ROOT / name).read_text(encoding="utf-8") for name in CSS_FILES)
    css += "\n" + (Path(__file__).with_name("course_embed.css")).read_text(encoding="utf-8")
    javascript = "\n".join((SOURCE_ROOT / name).read_text(encoding="utf-8") for name in JS_FILES)
    javascript += "\n" + (Path(__file__).with_name("course_embed.js")).read_text(encoding="utf-8")
    javascript += "\ninitReadingTools();startStudyApp().then(()=>window.StepCourseReady());"
    configuration = {"apiBase": "/api/course", "token": token, "ready": True}
    module = {"view": view, "subject": subject, "target": target, "scope": scope, "publicOnly": source == "public"}
    setup = f'<script nonce="{nonce}">window.STUDY_APP_CONFIG={_script_json(configuration)};window.STEP_COURSE_CONFIG={_script_json(module)};</script>'
    setup += '<script src="/terms/term_cards.js"></script><script src="/terms/selection_lookup.js"></script>'
    template = template.replace("</head>", setup + "</head>", 1)
    template = template.replace('<script>const DATA=__DATA__;</script>', f'<script nonce="{nonce}">const DATA=__DATA__;</script>')
    template = template.replace('<script src="anki_context_data.js"></script>', f'<script nonce="{nonce}">window.ANKI_CONTEXT={_script_json(anki)};</script>')
    template = template.replace('<script>__JS__</script>', f'<script nonce="{nonce}">__JS__</script>')
    page = template.replace("__CSS__", css).replace("__DATA__", _script_json(data)).replace("__JS__", javascript)
    policy = f"default-src 'self'; connect-src 'self'; img-src 'self' data: blob: https://upload.wikimedia.org https://mdwiki.org; media-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'nonce-{nonce}'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
    return page.encode("utf-8"), policy
