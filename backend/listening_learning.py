"""Original, device-read listening exercises and account-isolated attempts."""
from datetime import datetime, timezone
import hashlib
import json
import re
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEVELS = {'english': ['B1', 'B2', 'C1'], 'korean': ['1', '2']}
MAX_AUDIO_BYTES = 10 * 1024 * 1024


def init(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS listening_attempts(
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
        language TEXT NOT NULL, level TEXT NOT NULL, exercise_id TEXT NOT NULL,
        revision TEXT NOT NULL, snapshot TEXT NOT NULL, answers TEXT NOT NULL,
        score INTEGER NOT NULL, total INTEGER NOT NULL, submitted_at TEXT NOT NULL,
        seen_before_submission INTEGER NOT NULL DEFAULT 0, revealed_at TEXT);
    CREATE INDEX IF NOT EXISTS listening_attempt_owner ON listening_attempts(user_id,language,id);
    CREATE TABLE IF NOT EXISTS listening_reveals(
        user_id INTEGER NOT NULL, exercise_id TEXT NOT NULL, revision TEXT NOT NULL,
        revealed_at TEXT NOT NULL, PRIMARY KEY(user_id,exercise_id,revision));
    ''')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def bank():
    data = json.loads((ROOT / 'listening_bank.json').read_text(encoding='utf-8'))
    items = data['items']
    if not isinstance(items, list) or len({x['id'] for x in items}) != len(items):
        raise RuntimeError('Invalid listening bank')
    for item in items:
        if item['language'] not in LEVELS or item['level'] not in LEVELS[item['language']]:
            raise RuntimeError('Invalid listening level')
        if (not item['script'] or not item['translation'] or len(item['questions']) != 3
                or len({q['id'] for q in item['questions']}) != 3
                or any(len(q['options']) != 4 or type(q['correct']) is not int
                       or not 0 <= q['correct'] < 4 for q in item['questions'])):
            raise RuntimeError('Invalid listening question')
    return data


def revision(item):
    return hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def language(query):
    value = query.get('language', 'english')
    if value not in LEVELS:
        raise ValueError('请选择英语或韩语听力')
    return value


def positive(value, label):
    if isinstance(value, bool):
        raise ValueError(label + '有误')
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(label + '有误') from None
    if str(result) != str(value) or not 1 <= result <= 1000000:
        raise ValueError(label + '有误')
    return result


def review(item):
    return {'transcript': item['script'], 'translation': item['translation'],
            'questions': [{'id': q['id'], 'correct': q['correct'],
                           'explanation': q['explanation']} for q in item['questions']]}


def audio_identity(exercise_id, rev):
    if (not isinstance(exercise_id, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,159}', exercise_id) is None
            or not isinstance(rev, str) or re.fullmatch(r'[0-9a-f]{64}', rev) is None):
        raise ValueError('听力音频编号或版本无效')


def audio_file(c, exercise_id, rev):
    """Resolve only bounded WAV files beside this connection's database."""
    audio_identity(exercise_id, rev)
    if c is None:
        return None
    filename = next((row[2] for row in c.execute('PRAGMA database_list') if row[1] == 'main'), '')
    if not filename:
        return None
    try:
        parent = Path(filename).resolve(strict=True).parent
        root = (parent / 'listening_audio').resolve(strict=True)
        path = (root / (exercise_id + '-' + rev[:16] + '.wav')).resolve(strict=True)
        if not root.is_relative_to(parent) or path.parent != root or not path.is_file():
            return None
        size = path.stat().st_size
        if not 44 <= size <= MAX_AUDIO_BYTES:
            return None
        with wave.open(str(path), 'rb') as audio:
            frames, rate = audio.getnframes(), audio.getframerate()
            if (audio.getcomptype() != 'NONE' or audio.getnchannels() not in (1, 2)
                    or audio.getsampwidth() not in (1, 2, 3, 4) or not 1 <= rate <= 192000
                    or frames <= 0 or frames * audio.getnchannels() * audio.getsampwidth() > size):
                return None
            duration = frames / rate
        return path, size, round(duration, 3)
    except (OSError, ValueError, EOFError, wave.Error):
        return None


def audio_descriptor(item, c=None):
    rev = revision(item)
    file = audio_file(c, item['id'], rev)
    if file is not None:
        return {'type': 'generated_wav', 'url': '/listening/audio?exercise_id=' + item['id'] + '&revision=' + rev,
                'duration_seconds': file[2]}
    return {'type': 'browser_synthesis', 'locale': 'ko-KR' if item['language'] == 'korean' else 'en-GB'}


def read_audio(c, uid, query):
    """Authorize current material or an account-owned immutable old snapshot."""
    if not isinstance(query, dict) or set(query) != {'exercise_id', 'revision'}:
        raise ValueError('请只提供听力音频编号和版本')
    exercise_id, rev = query['exercise_id'], query['revision']
    audio_identity(exercise_id, rev)
    item = next((x for x in bank()['items'] if x['id'] == exercise_id and revision(x) == rev), None)
    if item is None:
        row = c.execute('SELECT snapshot FROM listening_attempts WHERE user_id=? AND exercise_id=? AND revision=? '
                        'ORDER BY id DESC LIMIT 1', (uid, exercise_id, rev)).fetchone()
        if row is not None:
            item = json.loads(row['snapshot'])
        if item is None or item.get('id') != exercise_id or revision(item) != rev:
            raise FileNotFoundError('听力音频不存在')
    file = audio_file(c, exercise_id, rev)
    if file is None:
        raise FileNotFoundError('听力音频暂不可用')
    with file[0].open('rb') as stream:
        body = stream.read(MAX_AUDIO_BYTES + 1)
    if len(body) != file[1] or len(body) > MAX_AUDIO_BYTES:
        raise FileNotFoundError('听力音频暂不可用')
    return body


def public(item, c=None):
    # Synthesis needs the public script. It stays hidden in the normal UI, not
    # encrypted: this is practice, not a secure examination environment.
    return {key: item[key] for key in ('id', 'language', 'level', 'title', 'type', 'knowledge_points')} | {
        'revision': revision(item), 'tts_text': item['script'],
        'audio': audio_descriptor(item, c),
        'questions': [{key: q[key] for key in ('id', 'prompt', 'options')} for q in item['questions']]}


def summary(row):
    item = json.loads(row['snapshot'])
    return {'attempt_id': row['id'], 'exercise_id': row['exercise_id'], 'title': item['title'],
            'language': row['language'], 'level': row['level'], 'type': item['type'],
            'score': row['score'], 'total': row['total'], 'submitted_at': row['submitted_at'],
            'seen_before_submission': bool(row['seen_before_submission']),
            'revealed_at': row['revealed_at']}


def result(row):
    return summary(row) | {'answers': json.loads(row['answers'])}


def history(c, uid, query):
    lang = language(query)
    page = positive(query.get('page', '1'), '页码')
    size = positive(query.get('page_size', '20'), '每页数量')
    if size not in (20, 50):
        raise ValueError('每页数量应为20或50')
    total = c.execute('SELECT count(*) FROM listening_attempts WHERE user_id=? AND language=?',
                      (uid, lang)).fetchone()[0]
    rows = c.execute('SELECT * FROM listening_attempts WHERE user_id=? AND language=? '
                     'ORDER BY id DESC LIMIT ? OFFSET ?', (uid, lang, size, (page-1)*size)).fetchall()
    return {'mode': 'history', 'language': lang, 'items': [summary(r) for r in rows],
            'page': page, 'page_size': size, 'total': total, 'has_more': page*size < total}


def get(c, uid, query):
    if query.get('mode') == 'history':
        return history(c, uid, query)
    if 'attempt_id' in query:
        aid = positive(query['attempt_id'], '练习记录')
        row = c.execute('SELECT * FROM listening_attempts WHERE id=? AND user_id=?', (aid, uid)).fetchone()
        if row is None:
            raise ValueError('练习记录不存在')
        item = json.loads(row['snapshot'])
        return {'exercise': public(item, c), 'saved': result(row), 'review': review(item), 'history_attempt': True}
    data = bank()
    lang = language(query)
    level = query.get('level', '1' if lang == 'korean' else 'B2')
    if level not in LEVELS[lang]:
        raise ValueError('当前韩语听力仅开放初级1—2册；英语可选B1、B2、C1')
    available = [x for x in data['items'] if x['language'] == lang and x['level'] == level]
    item = next((x for x in available if x['id'] == query.get('exercise_id')), None)
    if query.get('exercise_id') and item is None:
        raise ValueError('当前级别中没有这段听力')
    item = item or available[0]
    rev = revision(item)
    old = c.execute('SELECT * FROM listening_attempts WHERE user_id=? AND exercise_id=? AND revision=? '
                    'ORDER BY id DESC LIMIT 1', (uid, item['id'], rev)).fetchone()
    seen = c.execute('SELECT revealed_at FROM listening_reveals WHERE user_id=? AND exercise_id=? AND revision=?',
                     (uid, item['id'], rev)).fetchone()
    count = c.execute('SELECT count(*) FROM listening_attempts WHERE user_id=? AND exercise_id=?',
                      (uid, item['id'])).fetchone()[0]
    return {'language': lang, 'level': level, 'levels': LEVELS[lang], 'notice': data['notice'],
            'catalog': [{key: x[key] for key in ('id', 'title', 'type')} for x in available],
            'exercise': public(item, c), 'saved': result(old) if old else None,
            'attempt_count': count, 'revealed_at': seen['revealed_at'] if seen else None,
            'review': review(item) if old or seen else None}


def resolve(data, allowed):
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError('请只提交听力题目、版本和答案')
    item = next((x for x in bank()['items'] if x['id'] == data.get('exercise_id')), None)
    if item is None:
        raise ValueError('听力题目不存在')
    rev = revision(item)
    if data.get('revision') != rev:
        raise ValueError('题目已更新，请先刷新本段听力后重新作答')
    return item, rev


def submit(c, uid, data):
    item, rev = resolve(data, {'exercise_id', 'revision', 'answers'})
    answers = data.get('answers')
    if (not isinstance(answers, dict) or set(answers) != {q['id'] for q in item['questions']}
            or any(type(value) is not int or not 0 <= value < 4 for value in answers.values())):
        raise ValueError('请完成全部3道单选题后提交')
    seen = c.execute('SELECT revealed_at FROM listening_reveals WHERE user_id=? AND exercise_id=? AND revision=?',
                     (uid, item['id'], rev)).fetchone()
    score = sum(answers[q['id']] == q['correct'] for q in item['questions'])
    when = now()
    cur = c.execute('INSERT INTO listening_attempts(user_id,language,level,exercise_id,revision,snapshot,answers,'
                    'score,total,submitted_at,seen_before_submission,revealed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (uid, item['language'], item['level'], item['id'], rev,
                     json.dumps(item, ensure_ascii=False), json.dumps(answers), score, len(item['questions']),
                     when, int(bool(seen)), when))
    # The response includes the correction. Later attempts are consequently
    # labelled as having seen the explanation, without altering this attempt.
    c.execute('INSERT INTO listening_reveals(user_id,exercise_id,revision,revealed_at) VALUES(?,?,?,?) '
              'ON CONFLICT(user_id,exercise_id,revision) DO NOTHING', (uid, item['id'], rev, when))
    row = c.execute('SELECT * FROM listening_attempts WHERE id=? AND user_id=?', (cur.lastrowid, uid)).fetchone()
    return {'saved': result(row), 'review': review(item), 'exercise': public(item, c)}


def reveal(c, uid, data):
    item, rev = resolve(data, {'exercise_id', 'revision'})
    when = now()
    c.execute('INSERT INTO listening_reveals(user_id,exercise_id,revision,revealed_at) VALUES(?,?,?,?) '
              'ON CONFLICT(user_id,exercise_id,revision) DO NOTHING', (uid, item['id'], rev, when))
    c.execute('UPDATE listening_attempts SET revealed_at=COALESCE(revealed_at,?) '
              'WHERE user_id=? AND exercise_id=? AND revision=? AND id=(SELECT max(id) FROM listening_attempts '
              'WHERE user_id=? AND exercise_id=? AND revision=?)',
              (when, uid, item['id'], rev, uid, item['id'], rev))
    return {'review': review(item), 'revealed_at': when}
