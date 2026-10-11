"""Private source-bound comparison quizzes. These reviews never submit Anki ratings."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

KINDS = frozenset({'drugs', 'bugs', 'pathology'})
MISSING = {'', 'notspecified', 'notspecifiedinlecture', 'notapplicable', 'none', 'na',
           'notabacterialgramclassification', 'notapplicablevirus', 'notapplicableparasite'}


def key(value: str) -> str:
    return re.sub(r'[^\w]+', '', unicodedata.normalize('NFKC', str(value)).casefold(), flags=re.UNICODE).replace('_', '')


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, value):
        self.parts.append(value)
    def handle_endtag(self, tag):
        if tag in {'p', 'li', 'div', 'br'}:
            self.parts.append(' ')


def source_text(value: Any) -> str:
    parser = _PlainText()
    parser.feed(str(value or ''))
    return re.sub(r'\s+', ' ', ''.join(parser.parts)).strip()


def values(items: Any) -> list[str]:
    result, seen = [], set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, str):
            continue
        text = source_text(item)
        normalized = key(text)
        if normalized not in MISSING and normalized not in seen:
            result.append(text)
            seen.add(normalized)
    return result


def quiz_catalog(data: dict[str, Any], kind: str) -> dict[str, Any]:
    """Use every populated table column as well as the reviewed structured traits.

    Whole source-cell choices keep facts that have not been split into traits,
    including microbiology/CSF details, inside the actual quiz.
    """
    if kind not in KINDS:
        raise ValueError('Unknown quiz kind')
    sheet = next((item for item in data.get('comparison_sheets', []) if item.get('id') == kind), {})
    config = data.get('review_games', {}).get(kind, {})
    categories = list(dict.fromkeys(config.get('categories', [])))
    source_categories = ['Source detail · ' + str(column) for column in sheet.get('columns', [])[1:]]
    categories.extend(category for category in source_categories if category not in categories)
    by_row = {record.get('row_index'): record for record in config.get('cases', [])}
    cases = []
    for index, row in enumerate(sheet.get('rows', [])):
        cells = row.get('cells', [])
        original = by_row.get(index, {})
        traits = {category: values(original.get('traits', {}).get(category, [])) for category in categories}
        for offset, category in enumerate(source_categories, 1):
            traits[category] = values([cells[offset]]) if len(cells) > offset else []
        record = {**original, 'id': original.get('id') or f'{kind}-{index}', 'name': original.get('name') or source_text(cells[0] if cells else ''),
                  'row_index': index, 'traits': traits, 'omitted': [category for category in categories if not traits[category]],
                  'source_categories': source_categories}
        record['source_hash'] = hashlib.sha256(json.dumps(traits, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if record['name']:
            cases.append(record)
    return {'kind': kind, 'categories': categories, 'cases': cases}


def _stamp(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def grade(record: dict, categories: list[str], selections: dict) -> dict:
    details = []
    for category in categories:
        expected = values(record.get('traits', {}).get(category, []))
        if not expected:
            continue
        picked = values(selections.get(category, []))
        expected_keys = {key(value) for value in expected}
        picked_keys = {key(value) for value in picked}
        missing = [value for value in expected if key(value) not in picked_keys]
        wrong = [value for value in picked if key(value) not in expected_keys]
        details.append({'category': category, 'expected': expected, 'selected': picked,
                        'missing': missing, 'wrong': wrong, 'correct': not missing and not wrong})
    correct = sum(item['correct'] for item in details)
    return {'correct': correct, 'total': len(details), 'perfect': bool(details) and correct == len(details),
            'details': details, 'omitted': [category for category in categories if not record.get('traits', {}).get(category)]}


class QuizStore:
    def __init__(self, db_path: str | Path, clock: Callable[[], datetime] | None = None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with self._db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS comparison_quiz_sessions (
                kind TEXT PRIMARY KEY, session_json TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS comparison_quiz_attempts (
                attempt_id TEXT PRIMARY KEY, kind TEXT NOT NULL, item_id TEXT NOT NULL,
                source_hash TEXT NOT NULL, session_id TEXT NOT NULL, grade_json TEXT NOT NULL,
                created_at TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS comparison_quiz_attempt_item
                ON comparison_quiz_attempts(kind,item_id,created_at);
              CREATE TABLE IF NOT EXISTS comparison_quiz_schedule (
                kind TEXT NOT NULL, item_id TEXT NOT NULL, category TEXT NOT NULL,
                source_hash TEXT NOT NULL, correct INTEGER NOT NULL, streak INTEGER NOT NULL,
                due_at TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                PRIMARY KEY(kind,item_id,category));
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def snapshot(self, catalog: dict, *, include_catalog: bool = True) -> dict:
        kind = catalog['kind']
        records = {record['id']: record for record in catalog['cases']}
        now = _stamp(self.clock())
        with self._db() as db:
            saved = db.execute('SELECT session_json FROM comparison_quiz_sessions WHERE kind=?', (kind,)).fetchone()
            session = json.loads(saved[0]) if saved else None
            schedules = [dict(row) for row in db.execute('SELECT * FROM comparison_quiz_schedule WHERE kind=?', (kind,))]
            attempt_count = db.execute('SELECT COUNT(*) FROM comparison_quiz_attempts WHERE kind=?', (kind,)).fetchone()[0]
        if session:
            valid = all(item in records and records[item]['source_hash'] == session['source_hashes'].get(item) for item in session['round_ids'])
            session['source_changed'] = not valid
        current = [row for row in schedules if row['item_id'] in records and records[row['item_id']]['source_hash'] == row['source_hash']]
        grouped = {}
        for row in current:
            grouped.setdefault(row['item_id'], []).append(row)
        items = []
        for record in catalog['cases']:
            populated = [category for category in catalog['categories'] if record['traits'].get(category)]
            tracked = grouped.get(record['id'], [])
            due = [row['category'] for row in tracked if row['due_at'] <= now]
            unseen = [category for category in populated if not any(row['category'] == category for row in tracked)]
            items.append({'id': record['id'], 'reviewed_categories': len(tracked), 'available_categories': len(populated),
                          'due_categories': due, 'unseen_categories': unseen,
                          'complete': bool(populated) and not unseen and all(row['correct'] for row in tracked)})
        result = {'kind': kind, 'session': session, 'items': items,
                'summary': {'entries': len(records), 'attempts': attempt_count,
                            'due': sum(bool(item['due_categories']) for item in items),
                            'unseen': sum(bool(item['unseen_categories']) for item in items),
                            'complete': sum(item['complete'] for item in items)}, 'anki_ratings': 0}
        if include_catalog:
            result['catalog'] = catalog
        return result

    def export_state(self) -> dict:
        """Versioned private backup, read from one consistent SQLite transaction."""
        with self._db() as db:
            db.execute('BEGIN')
            sessions = [dict(row) for row in db.execute('SELECT * FROM comparison_quiz_sessions ORDER BY kind')]
            attempts = [dict(row) for row in db.execute('SELECT * FROM comparison_quiz_attempts ORDER BY created_at,attempt_id')]
            schedule = [dict(row) for row in db.execute('SELECT * FROM comparison_quiz_schedule ORDER BY kind,item_id,category')]
        for row in sessions:
            row['session'] = json.loads(row.pop('session_json'))
        for row in attempts:
            row['grade'] = json.loads(row.pop('grade_json'))
        return {'format': 'step-study-comparison-quizzes', 'version': 1,
                'exported_at': _stamp(self.clock()), 'sessions': sessions,
                'attempts': attempts, 'schedule': schedule}

    @staticmethod
    def _select(catalog: dict, identifiers: Any, categories: Any) -> tuple[list[str], list[str]]:
        available = {record['id'] for record in catalog['cases']}
        if not isinstance(identifiers, list) or not identifiers or len(identifiers) > 500 or any(not isinstance(item, str) or item not in available for item in identifiers):
            raise ValueError('Choose known quiz entries')
        if not isinstance(categories, list) or not categories or any(not isinstance(item, str) or item not in catalog['categories'] for item in categories):
            raise ValueError('Choose known source characteristics')
        return list(dict.fromkeys(identifiers)), list(dict.fromkeys(categories))

    def action(self, catalog: dict, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError('Expected a quiz action')
        kind, action = catalog['kind'], payload.get('action')
        records = {record['id']: record for record in catalog['cases']}
        now_dt = self.clock()
        now = _stamp(now_dt)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT session_json FROM comparison_quiz_sessions WHERE kind=?', (kind,)).fetchone()
            session = json.loads(row[0]) if row else None
            if action == 'start':
                ids, categories = self._select(catalog, payload.get('round_ids'), payload.get('categories'))
                session = {'id': str(uuid.uuid4()), 'kind': kind, 'round_ids': ids, 'categories': categories,
                           'source_hashes': {item: records[item]['source_hash'] for item in ids},
                           'at': 0, 'category_at': 0, 'selections': {}, 'checked': False, 'grade': None,
                           'complete': False, 'paused': False, 'started_at': now, 'revision': 1}
            elif action in {'draft', 'check', 'next', 'pause', 'resume', 'position'}:
                if not session or payload.get('session_id') != session['id']:
                    raise ValueError('This round is no longer current. Reload the saved round.')
                if any(item not in records or records[item]['source_hash'] != session['source_hashes'].get(item) for item in session['round_ids']):
                    raise ValueError('The source entry changed. Start a new round to review the current source.')
                # Repeating the explicit check is idempotent, including after a lost response.
                if action == 'check' and session['checked']:
                    return self.snapshot(catalog, include_catalog=False)
                if payload.get('revision') != session['revision']:
                    raise ValueError('The saved round changed. Reload it before continuing.')
                if action in {'draft', 'check'}:
                    if session['checked'] or session['complete']:
                        raise ValueError('This entry has already been checked')
                    selections = payload.get('selections', {})
                    if not isinstance(selections, dict) or any(category not in session['categories'] or not isinstance(picked, list) or len(picked) > 100 or any(not isinstance(value, str) or len(value) > 10000 for value in picked) for category, picked in selections.items()):
                        raise ValueError('Invalid characteristic selections')
                    record = records[session['round_ids'][session['at']]]
                    # Accept only choices that actually occur in this source catalog.
                    for category, picked in selections.items():
                        allowed = {key(value) for item in records.values() for value in item['traits'].get(category, [])}
                        if any(key(value) not in allowed for value in picked):
                            raise ValueError('A choice is no longer in the source catalog')
                    session['selections'] = {category: values(picked) for category, picked in selections.items()}
                    category_at = payload.get('category_at', session['category_at'])
                    if isinstance(category_at, bool) or not isinstance(category_at, int) or not 0 <= category_at < len(session['categories']):
                        raise ValueError('Invalid characteristic position')
                    session['category_at'] = category_at
                    if action == 'check':
                        result = grade(record, session['categories'], session['selections'])
                        if not result['total']:
                            raise ValueError('This entry has no sourced answers in the chosen categories')
                        attempt_id = f"{session['id']}:{session['at']}"
                        db.execute('INSERT OR IGNORE INTO comparison_quiz_attempts VALUES(?,?,?,?,?,?,?)',
                                   (attempt_id, kind, record['id'], record['source_hash'], session['id'], json.dumps(result), now))
                        for detail in result['details']:
                            old = db.execute('SELECT streak,source_hash FROM comparison_quiz_schedule WHERE kind=? AND item_id=? AND category=?', (kind, record['id'], detail['category'])).fetchone()
                            streak = ((old['streak'] if old and old['source_hash'] == record['source_hash'] else 0) + 1) if detail['correct'] else 0
                            days = min(30, [1, 3, 7, 14, 30][min(streak, 4)]) if detail['correct'] else 1
                            db.execute('INSERT OR REPLACE INTO comparison_quiz_schedule VALUES(?,?,?,?,?,?,?,?)',
                                       (kind, record['id'], detail['category'], record['source_hash'], int(detail['correct']), streak, _stamp(now_dt + timedelta(days=days)), now))
                        session['checked'], session['grade'] = True, result
                elif action == 'position':
                    category_at = payload.get('category_at')
                    if isinstance(category_at, bool) or not isinstance(category_at, int) or not 0 <= category_at < len(session['categories']):
                        raise ValueError('Invalid characteristic position')
                    session['category_at'] = category_at
                elif action == 'next':
                    # Skipping retains unknown history; only Check creates an attempt.
                    if session['at'] + 1 == len(session['round_ids']):
                        session['complete'] = True
                    else:
                        session['at'] += 1
                        session['category_at'], session['selections'], session['checked'], session['grade'] = 0, {}, False, None
                elif action == 'pause':
                    session['paused'] = True
                elif action == 'resume':
                    session['paused'] = False
                session['revision'] += 1
            else:
                raise ValueError('Unknown quiz action')
            db.execute('INSERT OR REPLACE INTO comparison_quiz_sessions VALUES(?,?,?)', (kind, json.dumps(session), now))
        return self.snapshot(catalog, include_catalog=False)
