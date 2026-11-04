"""Curated language resources and account-owned, persistent translation practice.

Question text is authored for this app. Textbook scans and extracted private
documents are never part of the deployed question bank.
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LANGUAGES = {'english': ('A1', 'A2', 'B1', 'B2', 'C1'), 'korean': ('1', '2', '3', '4', '5', '6')}
DIRECTIONS = ('mixed', 'to_target', 'to_zh')
DEFAULTS = {'english': {'level': 'B2', 'direction': 'mixed'}, 'korean': {'level': '1', 'direction': 'mixed'}}
_resource_cache = {}


def read_json(name):
    path = ROOT / name
    if not path.is_file():
        return {}
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _resource_cache:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if len(_resource_cache) > 8:
            _resource_cache.clear()
        _resource_cache[key] = value
    return _resource_cache[key]


def course_catalog():
    return (read_json('language_courses.json').get('courses', [])
            + read_json('video_courses.json').get('courses', []))


def init(c):
    columns = {row['name'] for row in c.execute('PRAGMA table_info(courses)')}
    if 'category' not in columns:
        c.execute("ALTER TABLE courses ADD COLUMN category TEXT NOT NULL DEFAULT 'ai'")
    if 'curriculum_key' not in columns:
        c.execute('ALTER TABLE courses ADD COLUMN curriculum_key TEXT')
    c.executescript('''
        CREATE UNIQUE INDEX IF NOT EXISTS language_course_identity
            ON courses(user_id,curriculum_key) WHERE curriculum_key IS NOT NULL;
        CREATE TABLE IF NOT EXISTS language_settings(
            user_id INTEGER NOT NULL, language TEXT NOT NULL, level TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'mixed', PRIMARY KEY(user_id,language));
        CREATE TABLE IF NOT EXISTS language_daily_sets(
            user_id INTEGER NOT NULL, language TEXT NOT NULL, level TEXT NOT NULL,
            day TEXT NOT NULL, direction TEXT NOT NULL, questions TEXT NOT NULL,
            PRIMARY KEY(user_id,language,level,day,direction));
        CREATE TABLE IF NOT EXISTS language_answers(
            user_id INTEGER NOT NULL, language TEXT NOT NULL, level TEXT NOT NULL,
            day TEXT NOT NULL, exercise_id TEXT NOT NULL, practice_direction TEXT NOT NULL,
            answer TEXT NOT NULL DEFAULT '', completed INTEGER NOT NULL DEFAULT 0,
            review INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
            question TEXT NOT NULL,
            PRIMARY KEY(user_id,language,level,day,practice_direction,exercise_id));
    ''')
    for row in c.execute('SELECT id FROM users').fetchall():
        seed_courses(c, row['id'])


def seed_courses(c, uid):
    """Seed bundles and refresh only field values from known old defaults."""
    retired = set(read_json('language_courses.json').get('retired_course_keys', []))
    for key in retired:
        # A retired system bundle is hidden, never deleted. Keep its learner
        # notes and stable progress identities, including previously edited rows.
        c.execute('UPDATE courses SET archived=1 WHERE user_id=? AND curriculum_key=? AND archived=0',
                  (uid, key))
    for course in course_catalog():
        key = course['key']
        if key in retired:
            continue
        saved = c.execute('SELECT * FROM courses WHERE user_id=? AND curriculum_key=?', (uid, key)).fetchone()
        if saved:
            # A changed factory title must not prevent an untouched description
            # from being refreshed, or overwrite a learner's own description.
            # Never migrate identities, archive flags or lesson progress here.
            updates = {}
            previous = course.get('previous_defaults', [])
            for field in ('title', 'url', 'description', 'source', 'category'):
                current = course.get(field, 'supplement' if field == 'source' else '')
                if saved[field] != current and any(
                    isinstance(old, dict) and field in old and saved[field] == old[field]
                    for old in previous
                ):
                    updates[field] = current
            if updates:
                c.execute('UPDATE courses SET ' + ','.join(field + '=?' for field in updates)
                          + ' WHERE id=? AND user_id=?',
                          tuple(updates.values()) + (saved['id'], uid))
            continue
        existing = c.execute('SELECT id FROM courses WHERE user_id=? AND url=? ORDER BY id LIMIT 1',
                             (uid, course['url'])).fetchone()
        if existing:
            c.execute('UPDATE courses SET category=?,curriculum_key=? WHERE id=? AND user_id=?',
                      (course['category'], key, existing['id'], uid))
        else:
            c.execute('INSERT INTO courses(user_id,title,url,description,source,category,curriculum_key) VALUES(?,?,?,?,?,?,?)',
                      (uid, course['title'], course['url'], course['description'],
                       course.get('source', 'supplement'), course['category'], key))


def category(data, old=None):
    value = data.get('category', dict(old or {}).get('category', 'ai'))
    if value not in ('ai', 'english', 'korean'):
        raise ValueError('课程分类应为人工智能与其他、英语或韩语')
    return value


def external_lessons(course):
    key = course.get('curriculum_key')
    if not key:
        return None
    entry = next((item for item in course_catalog() if item['key'] == key), None)
    if entry is None:
        return None
    guides = (read_json('english_lesson_guides.json').get('lessons', {})
              if entry.get('category') == 'english' else {})
    lessons = []
    for item in entry.get('lessons', []):
        lesson = dict(item, kind='external', size=0, extension='', trackable=True)
        guide = guides.get(item['id'], {})
        # Explanations supplement the original resource. Its stable identity,
        # URL, title and recorded completion continue to refer to that resource.
        for field in ('zh_title', 'zh_guide'):
            value = guide.get(field)
            if isinstance(value, str) and value.strip():
                lesson[field] = value
        lessons.append(lesson)
    return lessons


def trackable(lesson):
    return lesson.get('trackable', lesson['kind'] == 'video') is True


def settings(c, uid):
    result = {language: dict(value) for language, value in DEFAULTS.items()}
    for row in c.execute('SELECT language,level,direction FROM language_settings WHERE user_id=?', (uid,)):
        if row['language'] in LANGUAGES and row['level'] in LANGUAGES[row['language']] and row['direction'] in DIRECTIONS:
            result[row['language']] = {'level': row['level'], 'direction': row['direction']}
    return result


def validate_selection(data, with_day=True):
    language = data.get('language', 'english')
    if language not in LANGUAGES:
        raise ValueError('请选择英语或韩语')
    level = data.get('level', DEFAULTS[language]['level'])
    if not isinstance(level, str) or level not in LANGUAGES[language]:
        raise ValueError('学习级别无效')
    direction = data.get('direction', 'mixed')
    if direction not in DIRECTIONS:
        raise ValueError('翻译方向无效')
    if not with_day:
        return language, level, direction
    day = data.get('day', '')
    if not isinstance(day, str) or len(day) != 10 or date.fromisoformat(day).isoformat() != day:
        raise ValueError('请选择有效练习日期')
    return language, level, direction, day


def save_settings(c, uid, data):
    if not isinstance(data, dict) or set(data) - {'language', 'level', 'direction'}:
        raise ValueError('语言设置数据无效')
    language, level, direction = validate_selection(data, False)
    c.execute('INSERT INTO language_settings(user_id,language,level,direction) VALUES(?,?,?,?) '
              'ON CONFLICT(user_id,language) DO UPDATE SET level=excluded.level,direction=excluded.direction',
              (uid, language, level, direction))
    return {'ok': True, 'language_settings': settings(c, uid)}


def bank(language, level):
    content = read_json('language_' + language + '.json')
    selected = content.get('levels', {}).get(level, {})
    return content, selected


def compose_questions(language, level, direction, day):
    content, selected = bank(language, level)
    points = selected.get('points', [])
    # Interleave knowledge points before variants. Consecutive days draw the
    # next five sentence pairs; no pair repeats until the bank has cycled.
    pairs = []
    for variant in range(max((len(point.get('examples', [])) for point in points), default=0)):
        for point in points:
            if variant < len(point.get('examples', [])):
                pairs.append((point, variant, point['examples'][variant]))
    if not pairs:
        return []
    ordinal = date.fromisoformat(day).toordinal()
    offset = (ordinal - date(2026, 1, 1).toordinal()) * 5
    result = []
    target_name = '韩语' if language == 'korean' else '英语'
    for position in range(min(5, len(pairs))):
        point, variant, example = pairs[(offset + position) % len(pairs)]
        actual_direction = ('to_target' if (ordinal + position) % 2 == 0 else 'to_zh') if direction == 'mixed' else direction
        identity = f"{point['id']}:{variant}:{actual_direction}"
        result.append({
            'id': identity, 'language': language, 'level': level, 'day': day,
            'practice_direction': direction, 'direction': actual_direction,
            'knowledge_point': point['knowledge_point'], 'reference': point.get('reference', ''),
            'explanation': point.get('explanation', ''), 'hint': point.get('hint', ''),
            'checklist': list(point.get('checklist', [])),
            'prompt': (f'请运用「{point["knowledge_point"]}」把下面中文翻译成{target_name}。'
                       if actual_direction == 'to_target' else f'请把下面{target_name}翻译成中文，注意「{point["knowledge_point"]}」的含义。'),
            'source_text': example['zh'] if actual_direction == 'to_target' else example['target'],
            'model_answer': example['target'] if actual_direction == 'to_target' else example['zh'],
        })
    return result


def daily_questions(c, uid, language, level, direction, day):
    args = (uid, language, level, day, direction)
    saved = c.execute('SELECT questions FROM language_daily_sets WHERE user_id=? AND language=? AND level=? AND day=? AND direction=?', args).fetchone()
    if saved:
        return json.loads(saved['questions'])
    questions = compose_questions(language, level, direction, day)
    # Do not freeze a missing textbook index as an empty assignment forever.
    if questions:
        c.execute('INSERT INTO language_daily_sets(user_id,language,level,day,direction,questions) VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING',
                  args + (json.dumps(questions, ensure_ascii=False, separators=(',', ':')),))
        saved = c.execute('SELECT questions FROM language_daily_sets WHERE user_id=? AND language=? AND level=? AND day=? AND direction=?', args).fetchone()
        return json.loads(saved['questions'])
    return []


def answer_item(question, row=None):
    result = dict(question)
    result.update(answer=row['answer'] if row else '', completed=bool(row['completed']) if row else False,
                  review=bool(row['review']) if row else False, updated_at=row['updated_at'] if row else None)
    return result


def daily(c, uid, data):
    language, level, direction, day = validate_selection(data)
    content, selected = bank(language, level)
    questions = daily_questions(c, uid, language, level, direction, day)
    answers = {row['exercise_id']: row for row in c.execute(
        'SELECT * FROM language_answers WHERE user_id=? AND language=? AND level=? AND day=? AND practice_direction=?',
        (uid, language, level, day, direction))}
    items = [answer_item(question, answers.get(question['id'])) for question in questions]
    return {'language': language, 'level': level, 'direction': direction, 'day': day,
            'level_label': selected.get('label', level),
            'notice': '\n'.join(value for value in (content.get('notice'), selected.get('notice')) if value),
            'items': items, 'knowledge_count': len(selected.get('points', [])),
            'sentence_count': sum(len(point.get('examples', [])) for point in selected.get('points', [])),
            'summary': {'completed': sum(item['completed'] for item in items), 'total': len(items),
                        'review_count': c.execute('SELECT count(*) FROM language_answers WHERE user_id=? AND language=? AND level=? AND review=1',
                                                  (uid, language, level)).fetchone()[0]}}


def save_answer(c, uid, data):
    allowed = {'language', 'level', 'day', 'direction', 'exercise_id', 'answer', 'completed', 'review'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError('练习数据无效')
    language, level, direction, day = validate_selection(data)
    exercise_id = data.get('exercise_id')
    if not isinstance(exercise_id, str) or len(exercise_id) > 160:
        raise ValueError('练习编号无效')
    question = next((item for item in daily_questions(c, uid, language, level, direction, day)
                     if item['id'] == exercise_id), None)
    if question is None:
        raise ValueError('这道题不属于所选日期、级别或方向')
    old = c.execute('SELECT * FROM language_answers WHERE user_id=? AND language=? AND level=? AND day=? AND practice_direction=? AND exercise_id=?',
                    (uid, language, level, day, direction, exercise_id)).fetchone()
    answer = data.get('answer', old['answer'] if old else '')
    completed = data.get('completed', bool(old['completed']) if old else False)
    review = data.get('review', bool(old['review']) if old else False)
    if not isinstance(answer, str) or len(answer) > 4000:
        raise ValueError('每条译文最多 4000 字')
    if type(completed) is not bool or type(review) is not bool:
        raise ValueError('练习完成和复习标记应为 true 或 false')
    if completed and not answer.strip():
        raise ValueError('请先写下你的译文，再标记完成')
    updated = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    c.execute('''INSERT INTO language_answers(user_id,language,level,day,exercise_id,practice_direction,answer,completed,review,updated_at,question)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,language,level,day,practice_direction,exercise_id) DO UPDATE SET
                 practice_direction=excluded.practice_direction,answer=excluded.answer,completed=excluded.completed,
                 review=excluded.review,updated_at=excluded.updated_at,question=excluded.question''',
              (uid, language, level, day, exercise_id, direction, answer, int(completed), int(review), updated,
               json.dumps(question, ensure_ascii=False, separators=(',', ':'))))
    row = c.execute('SELECT * FROM language_answers WHERE user_id=? AND language=? AND level=? AND day=? AND practice_direction=? AND exercise_id=?',
                    (uid, language, level, day, direction, exercise_id)).fetchone()
    return {'ok': True, 'item': answer_item(question, row)}


def reviews(c, uid, data):
    language, level, direction = validate_selection(data, False)
    rows = c.execute('SELECT * FROM language_answers WHERE user_id=? AND language=? AND level=? AND review=1 ORDER BY updated_at DESC,exercise_id LIMIT 100',
                     (uid, language, level)).fetchall()
    items = [answer_item(json.loads(row['question']), row) for row in rows]
    return {'language': language, 'level': level, 'items': items,
            'total': c.execute('SELECT count(*) FROM language_answers WHERE user_id=? AND language=? AND level=? AND review=1',
                              (uid, language, level)).fetchone()[0]}
