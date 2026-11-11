"""Curated language resources and account-owned, persistent translation practice.

Question text is authored for this app. Textbook scans and extracted private
documents are never part of the deployed question bank.
"""
import json
import re
import unicodedata
from decimal import Decimal
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
            rotation INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id,language,level,day,direction));
        CREATE TABLE IF NOT EXISTS language_answers(
            user_id INTEGER NOT NULL, language TEXT NOT NULL, level TEXT NOT NULL,
            day TEXT NOT NULL, exercise_id TEXT NOT NULL, practice_direction TEXT NOT NULL,
            answer TEXT NOT NULL DEFAULT '', completed INTEGER NOT NULL DEFAULT 0,
            review INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
            question TEXT NOT NULL, grading TEXT NOT NULL DEFAULT '',
            manual_review TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(user_id,language,level,day,practice_direction,exercise_id));
        CREATE INDEX IF NOT EXISTS language_answer_history
            ON language_answers(user_id,language,updated_at DESC);
    ''')
    set_columns = {row['name'] for row in c.execute('PRAGMA table_info(language_daily_sets)')}
    if 'rotation' not in set_columns:
        c.execute('ALTER TABLE language_daily_sets ADD COLUMN rotation INTEGER NOT NULL DEFAULT 0')
    answer_columns = {row['name'] for row in c.execute('PRAGMA table_info(language_answers)')}
    if 'grading' not in answer_columns:
        c.execute("ALTER TABLE language_answers ADD COLUMN grading TEXT NOT NULL DEFAULT ''")
    if 'manual_review' not in answer_columns:
        c.execute("ALTER TABLE language_answers ADD COLUMN manual_review TEXT NOT NULL DEFAULT ''")
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
        lesson = dict(item, kind='external', size=0, extension='',
                      trackable=item.get('trackable', True))
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


def compose_questions(language, level, direction, day, rotation=0):
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
    offset = (ordinal - date(2026, 1, 1).toordinal() + rotation) * 5
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


def next_daily(c, uid, data):
    """Persist another finite-bank group, without rewriting saved answer snapshots."""
    if not isinstance(data, dict) or set(data) - {'language', 'level', 'direction', 'day'}:
        raise ValueError('换题参数无效')
    if any(not isinstance(value, str) for value in data.values()):
        raise ValueError('换题语言、级别、方向和日期应为文本')
    language, level, direction, day = validate_selection(data)
    old = daily_questions(c, uid, language, level, direction, day)
    args = (uid, language, level, day, direction)
    if old:
        # Acquire SQLite's write lock before reading the incremented cursor, so
        # concurrent devices never select from the same stale rotation value.
        c.execute('UPDATE language_daily_sets SET rotation=rotation+1 WHERE user_id=? AND language=? AND level=? AND day=? AND direction=?', args)
        rotation = c.execute('SELECT rotation FROM language_daily_sets WHERE user_id=? AND language=? AND level=? AND day=? AND direction=?', args).fetchone()['rotation']
        questions = compose_questions(language, level, direction, day, rotation)
        c.execute('UPDATE language_daily_sets SET questions=? WHERE user_id=? AND language=? AND level=? AND day=? AND direction=?',
                  (json.dumps(questions, ensure_ascii=False, separators=(',', ':')), *args))
    else:
        questions = []
    result = daily(c, uid, data)
    old_ids = {item['id'] for item in old}
    new_count = sum(item['id'] not in old_ids for item in questions)
    result['changed'] = bool(new_count)
    result['refresh_message'] = ('已切换下一组，新增 %s 道题。题库有限，会按顺序循环；已保存答案仍在「练习记录」。' % new_count
                                 if new_count else '当前级别可用题目已覆盖，本次已同步进度。题库有限，会循环复习；已保存答案不会清除。')
    return result


GRADING_ENGINE = 'reference-v1'
GRADING_LABELS = {'correct': '正确', 'incorrect': '需要修改', 'needs_review': '需复核'}
MANUAL_REVIEW_LABELS = {'correct': '我判对', 'incorrect': '我判错'}
_HAN = re.compile(r'[\u3400-\u9fff]')
_HANGUL = re.compile(r'[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]')
_LATIN = re.compile(r'[A-Za-z]')
_COUNTRIES = re.compile(
    r'中国|韩国|日本|美国|英国|法国|德国|중국|한국|일본|미국|영국|프랑스|독일|'
    r'\b(?:China|Korea|Japan|America|Britain|France|Germany|United States|United Kingdom)\b', re.I)
_COUNTRY_ALIASES = {
    'america': 'us', 'united states': 'us', '美国': 'us', '미국': 'us',
    'britain': 'uk', 'united kingdom': 'uk', '英国': 'uk', '영국': 'uk',
    'china': 'cn', '中国': 'cn', '중국': 'cn', 'korea': 'kr', '韩国': 'kr', '한국': 'kr',
    'japan': 'jp', '日本': 'jp', '일본': 'jp', 'france': 'fr', '法国': 'fr', '프랑스': 'fr',
    'germany': 'de', '德国': 'de', '독일': 'de',
}
_DAYS = re.compile(r'今天|昨天|明天|오늘|어제|내일|\b(?:today|yesterday|tomorrow)\b', re.I)
# Do not split decimals or interpret comma-separated numbers without context.
_NUMBERS = re.compile(r'(?<![A-Za-z0-9_.,])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_.,])')
_EN_CONTRACTIONS = {
    "i'm": 'i am', "you're": 'you are', "we're": 'we are', "they're": 'they are',
    "i'll": 'i will', "you'll": 'you will', "he'll": 'he will', "she'll": 'she will',
    "it'll": 'it will', "we'll": 'we will', "they'll": 'they will',
    "can't": 'can not', 'cannot': 'can not', "don't": 'do not', "doesn't": 'does not',
    "didn't": 'did not', "won't": 'will not', "isn't": 'is not', "aren't": 'are not',
    "wasn't": 'was not', "weren't": 'were not', "haven't": 'have not', "hasn't": 'has not',
    "hadn't": 'had not', "shouldn't": 'should not', "couldn't": 'could not',
    "wouldn't": 'would not', "mustn't": 'must not',
}
_EN_CONTRACTION_PATTERN = re.compile(
    r"\b(?:" + '|'.join(re.escape(value) for value in _EN_CONTRACTIONS) + r")\b", re.I)
# These are sentence-specific reviewed alternatives, not global synonym rules.
_REVIEWED_EQUIVALENTS = {
    ('korean', '은/는', '我来自中国'): ('我从中国来', '我是从中国来的', '我来自于中国'),
}


def normalise_grade_text(value, language):
    """Ignore presentation differences while preserving words and punctuation meaning."""
    text = unicodedata.normalize('NFKC', value).translate(str.maketrans({'’': "'", '‘': "'"}))
    text = ' '.join(text.split())
    # A comma already separates the words. Its surrounding layout spaces do
    # not change meaning; keep numeric commas and all other word spaces intact.
    text = re.sub(r'(?<=[^\W\d_])\s*,\s*(?=[^\W\d_])', ',', text)
    if language == 'zh':
        # A B / AB and 3 5 / 35 may be different codes or numbers even in Chinese.
        text = re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', text)
    # Question/exclamation marks, internal decimal dots, apostrophes and hyphens stay.
    return text.rstrip('.。').rstrip()


def _expand_english_contractions(text):
    def replace(match):
        value = _EN_CONTRACTIONS[match.group().lower()]
        return value[0].upper() + value[1:] if match.group()[0].isupper() else value
    return _EN_CONTRACTION_PATTERN.sub(replace, text)


def _single_slot_difference(reference, answer, pattern, numeric=False):
    left, right = list(pattern.finditer(reference)), list(pattern.finditer(answer))
    if len(left) != 1 or len(right) != 1:
        return None
    a, b = left[0], right[0]
    if reference[:a.start()] + '{slot}' + reference[a.end():] != answer[:b.start()] + '{slot}' + answer[b.end():]:
        return None
    left_value, right_value = a.group().casefold(), b.group().casefold()
    if pattern is _COUNTRIES:
        left_value = _COUNTRY_ALIASES.get(left_value, left_value)
        right_value = _COUNTRY_ALIASES.get(right_value, right_value)
    same = Decimal(a.group()) == Decimal(b.group()) if numeric else left_value == right_value
    return None if same else (a.group(), b.group())


def _positive_without_single_negation(text, language):
    """Return a positive counterpart only for deliberately narrow, unambiguous forms."""
    if language == 'en':
        text = _expand_english_contractions(text)
        if re.search(r'\b(?:not only|not just|no doubt|no wonder|no less|no more)\b', text, re.I):
            return None
        negatives = re.findall(r'\b(?:not|never|no|neither|nobody|nothing|without)\b', text, re.I)
        if len(negatives) != 1 or negatives[0].lower() not in ('not', 'never'):
            return None
        return ' '.join(re.sub(r'\b(?:not|never)\b', '', text, count=1, flags=re.I).split())
    if language == 'zh':
        if len(re.findall(r'[不没未无勿别]', text)) != 1:
            return None
        pairs = (('不是', '是'), ('没有', '有'), ('不会', '会'), ('不能', '能'),
                 ('不想', '想'), ('不去', '去'), ('不在', '在'), ('不知道', '知道'),
                 ('没去', '去'), ('不喜欢', '喜欢'))
        for negative, positive in pairs:
            if text.count(negative) == 1:
                return text.replace(negative, positive, 1)
        return None
    # Korean 안 can be a surname; predicate analysis needs reviewed sentence rules.
    return None


def grade_answer(question, answer, graded_at=None):
    """Conservative local reference checking; no external models or similarity thresholds."""
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError('请先写下答案，再点击批改')
    target = 'zh' if question['direction'] == 'to_zh' else 'ko' if question['language'] == 'korean' else 'en'
    reference = str(question.get('model_answer', ''))
    expected, actual = normalise_grade_text(reference, target), normalise_grade_text(answer, target)
    status, feedback = 'needs_review', []
    if expected and actual == expected:
        status, feedback = 'correct', ['与本题参考表达一致；已忽略不影响本题内容的排版差异。']
    elif (expected and target == 'en' and _expand_english_contractions(actual) == _expand_english_contractions(expected)
          and not re.search(r'\bnot (?:only|just)\b', _expand_english_contractions(expected), re.I)):
        status, feedback = 'correct', ['与参考表达等价，区别是常见且含义明确的英语缩略形式。']
    elif target == 'zh' and actual in _REVIEWED_EQUIVALENTS.get(
            (question['language'], question.get('knowledge_point', ''), expected), ()):
        status, feedback = 'correct', ['与本题经核对的另一种中文表达一致，意思与参考译文相同。']
    else:
        source = normalise_grade_text(str(question.get('source_text', '')), target)
        foreign_source = ((target == 'en' and (_HAN.search(source) or _HANGUL.search(source)))
                          or (target == 'ko' and (_HAN.search(source) or _LATIN.search(source)))
                          or (target == 'zh' and (_HANGUL.search(source) or _LATIN.search(source))))
        copied_source = bool(source) and foreign_source and actual == source and actual != expected
        wrong_script = ((target == 'ko' and _HANGUL.search(expected) and not _HANGUL.search(actual))
                        or (target == 'en' and _LATIN.search(expected) and not _LATIN.search(actual))
                        or (target == 'zh' and _HAN.search(expected) and not _HAN.search(actual)
                            and (_HANGUL.search(actual) or _LATIN.search(actual))))
        if copied_source:
            status, feedback = 'incorrect', ['答案与题目原文相同，尚未翻译成要求的目标语言。']
        elif wrong_script:
            language_name = {'zh': '中文', 'ko': '韩语', 'en': '英语'}[target]
            status, feedback = 'incorrect', [f'本题需要用{language_name}作答，当前答案没有对应语言的文字。']
        else:
            for pattern, label, numeric in ((_COUNTRIES, '国家或地区', False), (_DAYS, '时间', False), (_NUMBERS, '数值', True)):
                difference = _single_slot_difference(expected, actual, pattern, numeric)
                if difference:
                    before, after = (value if len(value) < 40 else value[:36] + '…' for value in difference)
                    status, feedback = 'incorrect', [f'其他文字相同，但{label}由参考的“{before}”变成了“{after}”，与题意不一致。']
                    break
            if status == 'needs_review':
                positive_ref = _positive_without_single_negation(expected, target)
                positive_answer = _positive_without_single_negation(actual, target)
                en_expected = _expand_english_contractions(expected) if target == 'en' else expected
                en_actual = _expand_english_contractions(actual) if target == 'en' else actual
                if (positive_ref is not None and positive_ref == en_actual) or (positive_answer is not None and positive_answer == en_expected):
                    status, feedback = 'incorrect', ['其他文字相同，但单一否定成分不同，表达的肯定或否定信息与参考不一致。']
    if status == 'needs_review':
        feedback = ['答案与参考表达有差异，基础核对暂不能确认对错；合理改写也可能得到此结果。',
                    f'请结合知识点「{question.get("knowledge_point", "本题表达")}」核对意思、语法及语体，或向老师确认。']
    elif status == 'incorrect':
        feedback.append('请对照下方参考表达修改后再次批改；完成与复习标记仍由你决定。')
    return {'status': status, 'label': GRADING_LABELS[status], 'feedback': feedback,
            'reference_answer': reference, 'graded_answer': answer,
            'graded_at': graded_at or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'engine': GRADING_ENGINE}


def _saved_grading(question, row):
    if not row or 'grading' not in row.keys() or not row['grading']:
        return None
    try:
        value = json.loads(row['grading'])
    except (ValueError, TypeError):
        return None
    if (not isinstance(value, dict) or value.get('engine') != GRADING_ENGINE
            or not isinstance(value.get('status'), str)
            or value.get('status') not in GRADING_LABELS
            or value.get('graded_answer') != row['answer']
            or value.get('reference_answer') != question.get('model_answer')
            or value.get('label') != GRADING_LABELS[value['status']]
            or not isinstance(value.get('graded_at'), str)
            or not isinstance(value.get('feedback'), list)
            or not value['feedback']
            or not all(isinstance(item, str) for item in value['feedback'])):
        return None
    return {key: value[key] for key in ('status', 'label', 'feedback', 'reference_answer', 'graded_answer', 'graded_at', 'engine')}


def _saved_manual_review(question, row, grading=None):
    if not row or 'manual_review' not in row.keys() or not row['manual_review']:
        return None
    if grading is None:
        grading = _saved_grading(question, row)
    if not grading or not row['answer'].strip():
        return None
    try:
        value = json.loads(row['manual_review'])
    except (ValueError, TypeError):
        return None
    if (not isinstance(value, dict) or not isinstance(value.get('status'), str)
            or value['status'] not in MANUAL_REVIEW_LABELS
            or value.get('label') != MANUAL_REVIEW_LABELS[value['status']]
            or value.get('reviewed_answer') != row['answer']
            or value.get('reference_answer') != question.get('model_answer')
            or not isinstance(value.get('reviewed_at'), str) or not value['reviewed_at']):
        return None
    return {key: value[key] for key in ('status', 'label', 'reviewed_at', 'reviewed_answer')}


def answer_item(question, row=None):
    result = dict(question)
    grading = _saved_grading(question, row)
    result.update(answer=row['answer'] if row else '', completed=bool(row['completed']) if row else False,
                  review=bool(row['review']) if row else False, updated_at=row['updated_at'] if row else None,
                  grading=grading, manual_review=_saved_manual_review(question, row, grading))
    return result


def daily(c, uid, data):
    if data.get('mode') == 'history':
        return history(c, uid, data)
    if data.get('mode', 'daily') != 'daily':
        raise ValueError('练习视图无效')
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


_ANSWER_WHITESPACE = ''.join(chr(code) for code in range(0x3001) if chr(code).isspace())


def history(c, uid, data):
    """Read saved attempts across dates/levels without composing or rewriting questions."""
    if not isinstance(data, dict) or set(data) - {'language', 'mode', 'level', 'page', 'page_size'}:
        raise ValueError('练习记录查询参数无效')
    language = data.get('language', 'english')
    if not isinstance(language, str) or language not in LANGUAGES:
        raise ValueError('请选择英语或韩语')
    if data.get('mode', 'history') != 'history':
        raise ValueError('练习记录视图无效')
    level = data.get('level')
    if level in (None, '', 'all'):
        level = None
    elif not isinstance(level, str) or level not in LANGUAGES[language]:
        raise ValueError('学习级别无效')
    try:
        if type(data.get('page', 1)) is bool or type(data.get('page_size', 20)) is bool:
            raise ValueError
        page, page_size = int(data.get('page', 1)), int(data.get('page_size', 20))
    except (ValueError, TypeError, OverflowError):
        raise ValueError('页码应为正整数，每页条数应为20或50')
    if str(data.get('page', page)).strip() != str(page) or str(data.get('page_size', page_size)).strip() != str(page_size):
        raise ValueError('页码应为正整数，每页条数应为20或50')
    if not 1 <= page <= 1000000 or page_size not in (20, 50):
        raise ValueError('页码应为1至1000000，每页可选20或50条')
    where = 'user_id=? AND language=? AND length(trim(answer,?))>0'
    parameters = [uid, language, _ANSWER_WHITESPACE]
    if level is not None:
        where += ' AND level=?'
        parameters.append(level)
    counts = c.execute('SELECT count(*),coalesce(sum(completed),0) FROM language_answers WHERE ' + where,
                       parameters).fetchone()
    rows = c.execute('SELECT * FROM language_answers WHERE ' + where
                     + ' ORDER BY updated_at DESC,day DESC,level,practice_direction,exercise_id LIMIT ? OFFSET ?',
                     parameters + [page_size + 1, (page - 1) * page_size]).fetchall()
    items = [answer_item(json.loads(row['question']), row) for row in rows[:page_size]]
    label = bank(language, level)[1].get('label', level) if level is not None else '所有级别'
    return {'language': language, 'mode': 'history', 'level': level or 'all', 'level_label': label,
            'items': items, 'total': counts[0], 'page': page, 'page_size': page_size,
            'has_more': len(rows) > page_size, 'summary': {'completed': counts[1], 'total': counts[0]}}


def save_answer(c, uid, data):
    allowed = {'language', 'level', 'day', 'direction', 'exercise_id', 'answer', 'completed', 'review', 'grade', 'manual_result'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError('练习数据无效')
    language, level, direction, day = validate_selection(data)
    exercise_id = data.get('exercise_id')
    if not isinstance(exercise_id, str) or len(exercise_id) > 160:
        raise ValueError('练习编号无效')
    old = c.execute('SELECT * FROM language_answers WHERE user_id=? AND language=? AND level=? AND day=? AND practice_direction=? AND exercise_id=?',
                    (uid, language, level, day, direction, exercise_id)).fetchone()
    answer = data.get('answer', old['answer'] if old else '')
    completed = data.get('completed', bool(old['completed']) if old else False)
    review = data.get('review', bool(old['review']) if old else False)
    should_grade = data.get('grade', False)
    has_manual_result = 'manual_result' in data
    manual_result = data.get('manual_result')
    if not isinstance(answer, str) or len(answer) > 4000:
        raise ValueError('每条译文最多 4000 字')
    if type(completed) is not bool or type(review) is not bool:
        raise ValueError('练习完成和复习标记应为 true 或 false')
    if type(should_grade) is not bool:
        raise ValueError('批改标记应为 true 或 false')
    if has_manual_result and manual_result is not None and (
            not isinstance(manual_result, str) or manual_result not in MANUAL_REVIEW_LABELS):
        raise ValueError('人工判断应为 correct、incorrect 或 null（撤销）')
    if completed and not answer.strip():
        raise ValueError('请先写下你的译文，再标记完成')
    if should_grade and not answer.strip():
        raise ValueError('请先写下答案，再点击批改')
    question = next((item for item in daily_questions(c, uid, language, level, direction, day)
                     if item['id'] == exercise_id), None)
    if question is None and old is not None:
        # A saved answer owns a server-authored snapshot even after its daily
        # group has been rotated. History edits must not depend on today's set.
        snapshot = json.loads(old['question'])
        if (isinstance(snapshot, dict) and snapshot.get('id') == exercise_id
                and snapshot.get('language') == language and snapshot.get('level') == level
                and snapshot.get('day') == day and snapshot.get('practice_direction') == direction):
            question = snapshot
    if question is None:
        raise ValueError('这道题不属于所选日期、级别或方向')
    if has_manual_result:
        if old is None or old['answer'] != answer:
            raise ValueError('请先保存并批改当前答案，再进行人工判断')
        if manual_result is not None and (not answer.strip() or _saved_grading(question, old) is None):
            raise ValueError('请先对当前已保存的答案进行基础批改，再标记对错')
    updated = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    grading = old['grading'] if old and old['answer'] == answer else ''
    manual_review = old['manual_review'] if old and old['answer'] == answer else ''
    if should_grade:
        grading = json.dumps(grade_answer(question, answer, updated), ensure_ascii=False, separators=(',', ':'))
    if has_manual_result:
        manual_review = '' if manual_result is None else json.dumps({
            'status': manual_result, 'label': MANUAL_REVIEW_LABELS[manual_result],
            'reviewed_at': updated, 'reviewed_answer': answer,
            'reference_answer': question.get('model_answer'),
        }, ensure_ascii=False, separators=(',', ':'))
    c.execute('''INSERT INTO language_answers(user_id,language,level,day,exercise_id,practice_direction,answer,completed,review,updated_at,question,grading,manual_review)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,language,level,day,practice_direction,exercise_id) DO UPDATE SET
                 practice_direction=excluded.practice_direction,answer=excluded.answer,completed=excluded.completed,
                 review=excluded.review,updated_at=excluded.updated_at,question=excluded.question,grading=excluded.grading,
                 manual_review=excluded.manual_review''',
              (uid, language, level, day, exercise_id, direction, answer, int(completed), int(review), updated,
               json.dumps(question, ensure_ascii=False, separators=(',', ':')), grading, manual_review))
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
