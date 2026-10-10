"""Discover only images already routed by the trusted course adapter."""
from html.parser import HTMLParser
from urllib.parse import unquote


class _Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "img" and values.get("src", "").startswith("/api/course/asset/"):
            self.images.append((unquote(values["src"].split("/api/course/asset/", 1)[1]), values.get("alt", "")))


def course_images(data, chapter_id=None):
    rows, seen = [], set()
    def visit(value, page):
        if isinstance(value, dict) and isinstance(value.get("src"), str) and value["src"].startswith("/api/course/asset/"):
            identity = unquote(value["src"].split("/api/course/asset/", 1)[1])
            key = (page["id"], identity)
            if key not in seen:
                seen.add(key)
                rows.append({"source": {"kind": "course_asset", "id": identity},
                             "title": value.get("caption") or value.get("source_label") or page.get("title", "Course figure"),
                             "chapter_id": page["id"], "chapter_title": page.get("title", ""),
                             "image_url": "/api/visual/image/course_asset/" + identity})
        elif isinstance(value, str):
            parser = _Images()
            parser.feed(value)
            for identity, alt in parser.images:
                key = (page["id"], identity)
                if key in seen: continue
                seen.add(key)
                rows.append({"source": {"kind": "course_asset", "id": identity},
                             "title": alt or page.get("title", "Course figure"),
                             "chapter_id": page["id"], "topic_ids": ["neuro", "psychiatry"],
                             "image_url": "/api/visual/image/course_asset/" + identity})
        elif isinstance(value, dict):
            for item in value.values(): visit(item, page)
        elif isinstance(value, list):
            for item in value: visit(item, page)
    for page in data.get("pages", []):
        if not chapter_id or page.get("id") == chapter_id:
            visit(page.get("blocks", []), page)
    return rows[:300]
