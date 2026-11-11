"""HTTP persistence, server-owned scoring, content and account boundaries."""
import copy
import json
import io
from pathlib import Path
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlencode
import wave
import unittest
from unittest.mock import patch

import test_task_timing as timing

app = timing.app
listening = app.listening_learning


class ListeningTests(unittest.TestCase):
    req = timing.TaskTimingTests.req

    def setUp(self):
        timing.TaskTimingTests.setUp(self)

    def tearDown(self):
        timing.TaskTimingTests.tearDown(self)

    def exercise(self, language='english', level=None, token='owner'):
        status, data = self.req('/listening?language='+language+(('&level='+level) if level else ''), token=token)
        self.assertEqual(status, 200, data)
        return data['exercise']

    def payload(self, exercise, wrong=False):
        item = next(x for x in listening.bank()['items'] if x['id'] == exercise['id'])
        answers = {q['id']: q['correct'] for q in item['questions']}
        if wrong:
            answers['q1'] = (answers['q1']+1) % 4
        return {'exercise_id': exercise['id'], 'revision': exercise['revision'], 'answers': answers}

    def submit(self, exercise, wrong=False, token='owner'):
        status, data = self.req('/listening', 'POST', self.payload(exercise, wrong), token=token)
        self.assertEqual(status, 200, data)
        return data

    def wav_fixture(self, item, frames=2000):
        root = app.DB.parent / 'listening_audio'
        root.mkdir(exist_ok=True)
        path = root / (item['id']+'-'+listening.revision(item)[:16]+'.wav')
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(b'\x01\x00' * frames)
        return path, path.read_bytes()

    def audio_request(self, url, method='GET', byte_range=None, token='owner'):
        if token == 'owner':
            token = self.token
        headers = {'Cookie':'session='+token} if token else {}
        if byte_range is not None:
            headers['Range'] = byte_range
        request = urllib.request.Request(self.base+url, method=method, headers=headers)
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, dict(response.headers), response.read()

    def test_generated_audio_is_preferred_and_http_bytes_head_and_ranges_are_exact(self):
        exercise = self.exercise()
        self.assertEqual(exercise['audio']['type'], 'browser_synthesis')
        item = next(x for x in listening.bank()['items'] if x['id']==exercise['id'])
        path, body = self.wav_fixture(item)
        exercise = self.exercise()
        audio = exercise['audio']
        self.assertEqual(audio['type'], 'generated_wav')
        self.assertEqual(audio['duration_seconds'], .25)
        self.assertIn('revision='+listening.revision(item), audio['url'])
        status, headers, received = self.audio_request(audio['url'])
        self.assertEqual((status, received), (200, body))
        self.assertEqual(headers['Content-Type'], 'audio/wav')
        self.assertEqual(headers['Content-Length'], str(len(body)))
        self.assertEqual(headers['Accept-Ranges'], 'bytes')
        self.assertEqual(self.audio_request(audio['url'], token=None)[0], 401)
        # All signed-in users can hear current public practice material.
        self.assertEqual(self.audio_request(audio['url'], token=self.other_token)[2], body)
        status, headers, received = self.audio_request(audio['url'], method='HEAD')
        self.assertEqual((status, headers['Content-Length'], received), (200, str(len(body)), b''))
        for byte_range, expected, bounds in [('bytes=0-15',body[:16],f'bytes 0-15/{len(body)}'),
                                             ('bytes=-8',body[-8:],f'bytes {len(body)-8}-{len(body)-1}/{len(body)}'),
                                             ('bytes=44-',body[44:],f'bytes 44-{len(body)-1}/{len(body)}')]:
            with self.subTest(byte_range=byte_range):
                status, headers, received = self.audio_request(audio['url'], byte_range=byte_range)
                self.assertEqual((status, received), (206, expected))
                self.assertEqual(headers['Content-Range'], bounds)
                self.assertEqual(headers['Content-Length'], str(len(expected)))
        status, headers, received = self.audio_request(audio['url'], method='HEAD', byte_range='bytes=0-15')
        self.assertEqual((status, headers['Content-Length'], received), (206, '16', b''))
        for value in ('bytes=999999-', 'bytes=2-1', 'bytes=0-1,3-4', 'invalid'):
            status, headers, received = self.audio_request(audio['url'], byte_range=value)
            self.assertEqual((status, received), (416, b''))
            self.assertEqual(headers['Content-Range'], 'bytes */'+str(len(body)))
        self.assertEqual(self.audio_request(audio['url'], method='POST')[0], 405)

    def test_audio_accepts_only_bound_safe_identity_and_valid_bounded_wav(self):
        exercise = self.exercise()
        item = next(x for x in listening.bank()['items'] if x['id']==exercise['id'])
        path, body = self.wav_fixture(item)
        url = self.exercise()['audio']['url']
        for query in ({'exercise_id':'../study','revision':exercise['revision']},
                      {'exercise_id':item['id']+'%2fother','revision':exercise['revision']},
                      {'exercise_id':item['id'],'revision':'../private'},
                      {'exercise_id':item['id'],'revision':exercise['revision'],'path':str(path)},
                      {'exercise_id':item['id'],'revision':exercise['revision'],'attempt_id':'1'},
                      {'exercise_id':item['id']}):
            with self.subTest(query=query):
                self.assertEqual(self.audio_request('/listening/audio?'+urlencode(query))[0], 400)
        unknown = '/listening/audio?'+urlencode({'exercise_id':item['id'],'revision':'0'*64})
        self.assertEqual(self.audio_request(unknown)[0], 404)
        for value in (b'not a wave', b'X' * (listening.MAX_AUDIO_BYTES+1)):
            path.write_bytes(value)
            self.assertEqual(self.exercise()['audio']['type'], 'browser_synthesis')
            self.assertEqual(self.audio_request(url)[0], 404)
        path.unlink()
        self.assertEqual(self.exercise()['audio']['type'], 'browser_synthesis')
        self.assertEqual(self.audio_request(url)[0], 404)
        outside = app.DB.parent/'outside.wav'
        outside.write_bytes(body)
        try:
            path.symlink_to(outside)
        except OSError:
            pass  # Windows may forbid symlink creation; path traversal checks above always run.
        else:
            self.assertEqual(self.exercise()['audio']['type'], 'browser_synthesis')
            self.assertEqual(self.audio_request(url)[0], 404)

    def test_historical_audio_keeps_versioned_bytes_and_is_only_available_to_snapshot_owner(self):
        exercise = self.exercise()
        original = next(x for x in listening.bank()['items'] if x['id']==exercise['id'])
        old_path, old_bytes = self.wav_fixture(original)
        saved = self.submit(exercise)
        aid = saved['saved']['attempt_id']
        old_url = saved['exercise']['audio']['url']
        with app.conn() as c:
            before = [tuple(row) for row in c.execute('SELECT * FROM listening_attempts')]
        changed_bank = listening.bank()
        changed = next(x for x in changed_bank['items'] if x['id']==exercise['id'])
        changed['script'] += ' This is a new revision.'
        new_path, new_bytes = self.wav_fixture(changed, frames=3000)
        self.assertNotEqual(old_path, new_path)
        with patch.object(listening, 'bank', return_value=changed_bank):
            current = self.exercise()
            self.assertNotEqual(current['audio']['url'], old_url)
            self.assertEqual(self.audio_request(current['audio']['url'])[2], new_bytes)
            status, old = self.req('/listening?attempt_id='+str(aid))
            self.assertEqual(status, 200, old)
            self.assertEqual(old['exercise']['audio']['url'], old_url)
            self.assertEqual(old['exercise']['tts_text'], exercise['tts_text'])
            self.assertEqual(self.audio_request(old_url)[2], old_bytes)
            self.assertEqual(self.audio_request(old_url, token=self.other_token)[0], 404)
        with app.conn() as c:
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM listening_attempts')], before)
        self.assertEqual(old_path.read_bytes(), old_bytes)

    def test_first_get_is_readonly_and_correct_options_are_not_exposed(self):
        self.assertEqual(self.req('/listening', token=None)[0], 401)
        with app.conn() as c:
            before = c.execute('SELECT count(*) FROM language_daily_sets').fetchone()[0]
        status, data = self.req('/listening?language=english')
        self.assertEqual(status, 200, data)
        self.assertEqual(data['level'], 'B2')
        self.assertIsNone(data['review'])
        self.assertIsNone(data['saved'])
        self.assertIn('tts_text', data['exercise'])
        self.assertNotIn('script', data['exercise'])
        self.assertNotIn('translation', data['exercise'])
        for question in data['exercise']['questions']:
            self.assertEqual(set(question), {'id', 'prompt', 'options'})
        with app.conn() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM listening_attempts').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT count(*) FROM listening_reveals').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT count(*) FROM language_daily_sets').fetchone()[0], before)

    def test_korean_public_questions_include_learning_translations_but_not_answers(self):
        exercise = self.exercise('korean')
        item = next(x for x in listening.bank()['items'] if x['id'] == exercise['id'])
        for question, original in zip(exercise['questions'], item['questions']):
            self.assertEqual(set(question), {'id', 'prompt', 'options', 'prompt_zh', 'options_zh'})
            self.assertEqual(question['prompt_zh'], original['prompt_zh'])
            self.assertEqual(question['options_zh'], original['options_zh'])
            self.assertNotIn('correct', question)
            self.assertNotIn('explanation', question)
            self.assertTrue(listening.korean_question_text(question['prompt'], prompt=True))
            self.assertTrue(all(listening.korean_question_text(option) for option in question['options']))
        saved = self.submit(exercise)
        self.assertEqual(saved['saved']['score'], 3)
        self.assertEqual(saved['exercise']['questions'], exercise['questions'])
        self.assertEqual([q['correct'] for q in saved['review']['questions']],
                         [q['correct'] for q in item['questions']])

    def test_korean_question_translation_upgrade_retains_old_snapshot_and_audio(self):
        current_bank = listening.bank()
        current_item = next(x for x in current_bank['items'] if x['id'] == 'korean-1-listen-01')
        legacy_bank = copy.deepcopy(current_bank)
        legacy_item = next(x for x in legacy_bank['items'] if x['id'] == current_item['id'])
        for question in legacy_item['questions']:
            question['prompt'] = question.pop('prompt_zh')
            question['options'] = question.pop('options_zh')
        old_path, old_bytes = self.wav_fixture(legacy_item)
        with patch.object(listening, 'bank', return_value=legacy_bank):
            exercise = self.exercise('korean')
            self.assertTrue(all(set(q) == {'id', 'prompt', 'options'} for q in exercise['questions']))
            saved = self.submit(exercise)
        with app.conn() as c:
            before = [tuple(row) for row in c.execute('SELECT * FROM listening_attempts')]
        new_path = old_path.with_name(current_item['id'] + '-' + listening.revision(current_item)[:16] + '.wav')
        self.assertNotEqual(new_path, old_path)
        # The recording itself is unchanged: a new content revision can reuse its bytes.
        try:
            new_path.hardlink_to(old_path)
        except OSError:
            new_path.write_bytes(old_bytes)
        current = self.exercise('korean')
        self.assertNotEqual(current['revision'], exercise['revision'])
        self.assertEqual(current['audio']['type'], 'generated_wav')
        self.assertEqual(self.audio_request(current['audio']['url'])[2], old_bytes)
        self.assertEqual(self.req('/listening', 'POST', self.payload(exercise))[0], 400)
        status, history = self.req('/listening?attempt_id=' + str(saved['saved']['attempt_id']))
        self.assertEqual(status, 200, history)
        self.assertEqual(history['exercise']['questions'], exercise['questions'])
        self.assertEqual(history['saved']['score'], 3)
        self.assertEqual(history['exercise']['audio']['url'], saved['exercise']['audio']['url'])
        self.assertEqual(self.audio_request(history['exercise']['audio']['url'])[2], old_bytes)
        self.assertEqual(self.audio_request(history['exercise']['audio']['url'], token=self.other_token)[0], 404)
        with app.conn() as c:
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM listening_attempts')], before)
        self.assertEqual(old_path.read_bytes(), old_bytes)

    def test_every_submission_keeps_immutable_attempt_and_server_score(self):
        exercise = self.exercise()
        first = self.submit(exercise)
        second = self.submit(exercise, wrong=True)
        self.assertEqual((first['saved']['score'], second['saved']['score']), (3, 2))
        self.assertNotEqual(first['saved']['attempt_id'], second['saved']['attempt_id'])
        self.assertFalse(first['saved']['seen_before_submission'])
        self.assertTrue(second['saved']['seen_before_submission'])
        self.assertTrue(first['saved']['revealed_at'])
        status, saved = self.req('/listening?attempt_id='+str(first['saved']['attempt_id']))
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved['saved']['score'], 3)
        self.assertEqual(saved['saved']['answers'], first['saved']['answers'])
        self.assertIn('translation', saved['review'])
        current = self.req('/listening?language=english')[1]
        self.assertEqual(current['attempt_count'], 2)
        self.assertEqual(current['saved']['attempt_id'], second['saved']['attempt_id'])
        with app.conn() as c:
            app.listening_learning.init(c)
            self.assertEqual(c.execute('SELECT count(*) FROM listening_attempts').fetchone()[0], 2)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_invalid_answers_and_forged_scores_do_not_write(self):
        exercise = self.exercise()
        valid = self.payload(exercise)
        invalid = [
            {**valid, 'score': 3}, {**valid, 'user_id': 2}, {**valid, 'snapshot': {}},
            {**valid, 'revision': 'forged'}, {**valid, 'exercise_id': '../../private'},
            {**valid, 'answers': {'q1': 0}}, {**valid, 'answers': []},
            {**valid, 'answers': {**valid['answers'], 'q1': True}},
            {**valid, 'answers': {**valid['answers'], 'q1': '1'}},
            {**valid, 'answers': {**valid['answers'], 'q1': 4}},
            {**valid, 'answers': {**valid['answers'], 'extra': 0}},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.req('/listening', 'POST', payload)[0], 400)
        with app.conn() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM listening_attempts').fetchone()[0], 0)
        self.assertEqual(self.req('/listening', 'PATCH', valid)[0], 405)

    def test_reveal_is_saved_and_account_isolated_without_faking_attempt(self):
        exercise = self.exercise('korean')
        payload = {'exercise_id': exercise['id'], 'revision': exercise['revision']}
        status, reveal = self.req('/listening/reveal', 'POST', payload)
        self.assertEqual(status, 200, reveal)
        self.assertIn('transcript', reveal['review'])
        with app.conn() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM listening_attempts').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT count(*) FROM listening_reveals').fetchone()[0], 1)
        other = self.req('/listening?language=korean', token=self.other_token)[1]
        self.assertIsNone(other['review'])
        self.assertIsNone(other['revealed_at'])
        self.assertTrue(self.submit(exercise)['saved']['seen_before_submission'])
        self.assertFalse(self.submit(exercise, token=self.other_token)['saved']['seen_before_submission'])

    def test_history_and_attempt_ids_never_cross_accounts(self):
        saved = self.submit(self.exercise())['saved']
        aid = saved['attempt_id']
        self.assertEqual(self.req('/listening?attempt_id='+str(aid), token=self.other_token)[0], 400)
        other = self.req('/listening?language=english&mode=history', token=self.other_token)[1]
        self.assertEqual((other['total'], other['items']), (0, []))
        own = self.req('/listening?language=english&mode=history')[1]
        self.assertEqual(own['items'][0]['attempt_id'], aid)
        self.assertNotIn('answers', own['items'][0])
        self.assertEqual(self.req('/listening?language=korean&mode=history')[1]['total'], 0)

    def test_history_pagination_retains_all_attempts(self):
        exercise = self.exercise()
        for i in range(21):
            self.submit(exercise, wrong=bool(i % 2))
        page1 = self.req('/listening?language=english&mode=history')[1]
        page2 = self.req('/listening?language=english&mode=history&page=2')[1]
        self.assertEqual((page1['total'], len(page1['items']), page1['has_more']), (21, 20, True))
        self.assertEqual((page2['total'], len(page2['items']), page2['has_more']), (21, 1, False))
        self.assertFalse({x['attempt_id'] for x in page1['items']} & {x['attempt_id'] for x in page2['items']})
        for query in ['page=0', 'page=-1', 'page=abc', 'page=1.5', 'page_size=100', 'language=chinese']:
            self.assertEqual(self.req('/listening?mode=history&'+query)[0], 400)

    def test_content_updates_do_not_rewrite_past_questions_or_grades(self):
        exercise = self.exercise()
        saved = self.submit(exercise)
        bank = listening.bank()
        changed = next(x for x in bank['items'] if x['id'] == exercise['id'])
        changed['script'] = 'A new version with a different script.'
        changed['questions'][0]['correct'] = (changed['questions'][0]['correct']+1) % 4
        with patch.object(listening, 'bank', return_value=bank):
            self.assertEqual(self.req('/listening', 'POST', self.payload(exercise))[0], 400)
            current = self.req('/listening?language=english')[1]
            self.assertIsNone(current['saved'])
            old = self.req('/listening?attempt_id='+str(saved['saved']['attempt_id']))[1]
            self.assertEqual(old['saved']['score'], 3)
            self.assertEqual(old['review']['transcript'], exercise['tts_text'])

    def test_korean_starts_beginner_and_unsupported_levels_fail(self):
        self.assertEqual(self.exercise('korean')['level'], '1')
        self.assertEqual(self.exercise('korean', '2')['level'], '2')
        self.assertEqual(self.req('/listening?language=korean&level=3')[0], 400)
        self.assertEqual(self.req('/listening?language=english&level=A1')[0], 400)
        self.assertEqual(self.req('/listening?language=english&level=B2&exercise_id=korean-1-listen-01')[0], 400)

    def test_recording_profile_is_public_but_clients_cannot_replace_it(self):
        exercise = self.exercise('korean')
        item = next(x for x in listening.bank()['items'] if x['id'] == exercise['id'])
        self.assertEqual(exercise['audio_profile'], item['audio_profile'])
        self.assertEqual(exercise['tts_text'], item['script'])
        self.assertEqual(exercise['audio_profile']['mode'], 'dialogue')
        for question, original in zip(exercise['questions'], item['questions']):
            expected = {'id', 'prompt', 'options'}
            if 'prompt_zh' in original:
                expected |= {'prompt_zh', 'options_zh'}
            self.assertEqual(set(question), expected)
        payload = self.payload(exercise)
        forged = {'audio_profile': item['audio_profile'], 'voice': 'untrusted-voice',
                  'turns': [], 'tts_text': 'A client cannot change the recording.'}
        for key, value in forged.items():
            with self.subTest(key=key):
                self.assertEqual(self.req('/listening', 'POST', {**payload, key: value})[0], 400)
                self.assertEqual(self.req('/listening/reveal', 'POST', {
                    'exercise_id': exercise['id'], 'revision': exercise['revision'], key: value})[0], 400)
        with app.conn() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM listening_attempts').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT count(*) FROM listening_reveals').fetchone()[0], 0)

    def test_profile_only_recording_change_keeps_historical_profile_and_audio(self):
        exercise = self.exercise('korean')
        original = next(x for x in listening.bank()['items'] if x['id'] == exercise['id'])
        old_path, old_bytes = self.wav_fixture(original)
        saved = self.submit(exercise)
        old_url = saved['exercise']['audio']['url']
        aid = saved['saved']['attempt_id']
        with app.conn() as c:
            before = [tuple(row) for row in c.execute('SELECT * FROM listening_attempts')]
        changed_bank = listening.bank()
        changed = next(x for x in changed_bank['items'] if x['id'] == exercise['id'])
        changed['audio_profile']['turn_pause_ms'] += 100
        listening.validate_audio_profile(changed)
        self.assertEqual(changed['script'], original['script'])
        self.assertNotEqual(listening.revision(changed), exercise['revision'])
        new_path, new_bytes = self.wav_fixture(changed, frames=3000)
        self.assertNotEqual(old_path, new_path)
        with patch.object(listening, 'bank', return_value=changed_bank):
            status, current = self.req('/listening?language=korean')
            self.assertEqual(status, 200, current)
            self.assertIsNone(current['saved'])
            self.assertEqual(current['attempt_count'], 1)
            self.assertEqual(current['exercise']['audio_profile'], changed['audio_profile'])
            self.assertEqual(self.audio_request(current['exercise']['audio']['url'])[2], new_bytes)
            # A cached client must reload the new audio identity before submitting.
            self.assertEqual(self.req('/listening', 'POST', self.payload(exercise))[0], 400)
            status, historical = self.req('/listening?attempt_id=' + str(aid))
            self.assertEqual(status, 200, historical)
            self.assertEqual(historical['exercise']['audio_profile'], original['audio_profile'])
            self.assertEqual(historical['saved']['score'], 3)
            self.assertEqual(historical['exercise']['audio']['url'], old_url)
            self.assertEqual(self.audio_request(old_url)[2], old_bytes)
            self.assertEqual(self.audio_request(old_url, token=self.other_token)[0], 404)
        with app.conn() as c:
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM listening_attempts')], before)
        self.assertEqual(old_path.read_bytes(), old_bytes)

    def test_legacy_snapshot_without_role_metadata_remains_readable_after_upgrade(self):
        upgraded_bank = listening.bank()
        legacy_bank = copy.deepcopy(upgraded_bank)
        original = next(x for x in legacy_bank['items'] if x['id'] == 'korean-1-listen-01')
        original.pop('audio_profile')
        listening.validate_audio_profile(original)
        old_path, old_bytes = self.wav_fixture(original)
        with patch.object(listening, 'bank', return_value=legacy_bank):
            legacy = self.exercise('korean')
            self.assertIsNone(legacy.get('audio_profile'))
            saved = self.submit(legacy)
        aid = saved['saved']['attempt_id']
        old_url = saved['exercise']['audio']['url']
        with app.conn() as c:
            before = [tuple(row) for row in c.execute('SELECT * FROM listening_attempts')]
        current = self.exercise('korean')
        self.assertNotEqual(current['revision'], legacy['revision'])
        self.assertEqual(current['audio_profile']['mode'], 'dialogue')
        status, historical = self.req('/listening?attempt_id=' + str(aid))
        self.assertEqual(status, 200, historical)
        self.assertIsNone(historical['exercise'].get('audio_profile'))
        self.assertEqual(historical['exercise']['tts_text'], original['script'])
        self.assertEqual(historical['saved']['score'], 3)
        self.assertEqual(historical['exercise']['audio']['url'], old_url)
        self.assertEqual(self.audio_request(old_url)[2], old_bytes)
        self.assertEqual(self.audio_request(old_url, token=self.other_token)[0], 404)
        with app.conn() as c:
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM listening_attempts')], before)
        self.assertEqual(old_path.read_bytes(), old_bytes)


class ListeningContentTests(unittest.TestCase):
    def test_live_question_metadata_is_validated_without_loosening_numeric_choices(self):
        original = json.loads((listening.ROOT / 'listening_bank.json').read_text(encoding='utf-8'))
        # Exercise the file-reading boundary, rather than bypassing validation with bank().
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'listening_bank.json'
            path.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
            with patch.object(listening, 'ROOT', root):
                self.assertEqual(len(listening.bank()['items']), 20)
                invalid = [({'prompt_zh': None}), ({'prompt_zh': ' '}), ({'options_zh': 'not a list'}),
                           ({'options_zh': ['一', '二', '三']}), ({'options_zh': ['一', '二', '三', None]}),
                           ({'options_zh': ['一', '二', '三', ' ']}), ({'prompt': '请听对话并选择。'}),
                           ({'prompt': 'Which option is correct?'}), ({'prompt': '1?'}),
                           ({'options': ['中文', '둘', '셋', '넷']}),
                           ({'options': ['The bus leaves at 10', '둘', '셋', '넷']})]
                for change in invalid:
                    data = copy.deepcopy(original)
                    item = next(x for x in data['items'] if x['language'] == 'korean')
                    item['questions'][0].update(change)
                    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                    with self.subTest(change=change), self.assertRaises(RuntimeError):
                        listening.bank()
                for missing in ('prompt_zh', 'options_zh'):
                    data = copy.deepcopy(original)
                    next(x for x in data['items'] if x['language'] == 'korean')['questions'][0].pop(missing)
                    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                    with self.subTest(missing=missing), self.assertRaises(RuntimeError):
                        listening.bank()
        question = {'id': 'numeric', 'prompt': '몇 시입니까?', 'options': ['10:00', '10:30', '11:00', '11:30'],
                    'correct': 1, 'explanation': '时刻保持题目含义。', 'prompt_zh': '几点？',
                    'options_zh': ['十点', '十点半', '十一点', '十一点半']}
        listening.validate_question(question, 'korean')
        question.pop('prompt_zh')
        question.pop('options_zh')
        listening.validate_question(question, 'korean')
        question['prompt_zh'] = 'Incomplete translation pair'
        with self.assertRaises(RuntimeError):
            listening.validate_question(question, 'english')

    def test_question_and_learning_translation_changes_produce_distinct_content_revisions(self):
        original = next(x for x in listening.bank()['items'] if x['language'] == 'korean')
        prompt_changed = copy.deepcopy(original)
        prompt_changed['questions'][0]['prompt'] += ' 다시 고르십시오.'
        translation_changed = copy.deepcopy(original)
        translation_changed['questions'][0]['prompt_zh'] += '（学习辅助）'
        self.assertNotEqual(listening.revision(original), listening.revision(prompt_changed))
        self.assertNotEqual(listening.revision(original), listening.revision(translation_changed))
        self.assertEqual(prompt_changed['script'], original['script'])
        self.assertEqual(prompt_changed['audio_profile'], original['audio_profile'])

    def test_versioned_roles_cover_every_stable_exercise_without_reading_labels(self):
        items = listening.bank()['items']
        expected_ids = {f'korean-{level}-listen-{index:02d}'
                        for level in ('1', '2') for index in range(1, 8)}
        expected_ids |= {f'english-{level}-listen-{index:02d}'
                         for level in ('B1', 'B2', 'C1') for index in (1, 2)}
        self.assertEqual({x['id'] for x in items}, expected_ids)
        counts = {'korean': {'dialogue': 0, 'narration': 0},
                  'english': {'dialogue': 0, 'narration': 0}}
        for item in items:
            with self.subTest(exercise=item['id']):
                profile = item['audio_profile']
                listening.validate_audio_profile(item)
                self.assertEqual(profile['version'], 1)
                expected_locale = 'ko-KR' if item['language'] == 'korean' else 'en-US'
                self.assertEqual(profile['locale'], expected_locale)
                speakers = {x['id']: x for x in profile['speakers']}
                self.assertEqual(len(speakers), len(profile['speakers']))
                self.assertTrue(all(x['voice'].startswith(expected_locale + '-') for x in speakers.values()))
                self.assertTrue(profile['instruction'].strip())
                turns = profile['turns']
                self.assertTrue(all(turn['text'].strip() for turn in turns))
                counts[item['language']][profile['mode']] += 1
                if profile['mode'] == 'dialogue':
                    self.assertEqual({x['gender'] for x in speakers.values()}, {'male', 'female'})
                    self.assertEqual({x['speaker'] for x in turns}, set(speakers))
                    reconstructed = '\n'.join(speakers[x['speaker']]['label'] + ': ' + x['text'] for x in turns)
                    for turn in turns:
                        self.assertFalse(any(turn['text'].startswith(x['label'] + ':')
                                             for x in speakers.values()))
                else:
                    self.assertEqual((len(speakers), len(turns)), (1, 1))
                    reconstructed = turns[0]['text']
                self.assertEqual(reconstructed, item['script'])
        self.assertEqual(counts, {'korean': {'dialogue': 8, 'narration': 6},
                                  'english': {'dialogue': 3, 'narration': 3}})

    def test_audio_profile_rejects_mismatched_roles_text_locale_and_schema(self):
        original = next(x for x in listening.bank()['items']
                        if x['language'] == 'korean' and x['audio_profile']['mode'] == 'dialogue')
        invalid = []

        def changed(label, mutate):
            item = copy.deepcopy(original)
            mutate(item['audio_profile'])
            invalid.append((label, item))

        for key, value in [('version', True), ('version', 2), ('style', 'english_academic'),
                           ('locale', 'en-US'), ('mode', 'unrecognized'), ('instruction', ''),
                           ('turn_pause_ms', -1), ('intro_pause_ms', True), ('ending_pause_ms', 10001)]:
            changed(key + '=' + repr(value), lambda p, key=key, value=value: p.update({key: value}))
        changed('unexpected key', lambda p: p.update({'client_voice': 'untrusted'}))
        changed('missing turns', lambda p: p.pop('turns'))
        changed('duplicate speaker', lambda p: p['speakers'][1].update({'id': p['speakers'][0]['id']}))
        changed('same gender pair', lambda p: p['speakers'][1].update({
            'gender': p['speakers'][0]['gender'], 'voice': p['speakers'][0]['voice']}))
        changed('wrong locale voice', lambda p: p['speakers'][0].update({'voice': 'en-US-JennyNeural'}))
        changed('unknown speaker', lambda p: p['turns'][0].update({'speaker': 'unknown'}))
        changed('changed spoken content', lambda p: p['turns'][0].update({'text': 'Different content.'}))
        changed('role label accidentally spoken', lambda p: p['turns'][0].update({
            'text': p['speakers'][0]['label'] + ': ' + p['turns'][0]['text']}))
        changed('dialogue disguised as narration', lambda p: p.update({'mode': 'narration'}))
        for label, item in invalid:
            with self.subTest(case=label):
                with self.assertRaises((RuntimeError, ValueError)):
                    listening.validate_audio_profile(item)

    def test_original_bank_has_all_required_types_and_valid_book_points(self):
        data = listening.bank()
        self.assertEqual(len(data['items']), 20)
        self.assertEqual(sum(len(x['questions']) for x in data['items']), 60)
        korean = json.loads((listening.ROOT / 'language_korean.json').read_text(encoding='utf-8'))
        ids = {x['id'] for lev in korean['levels'].values() for x in lev['points']}
        for level in ('1', '2'):
            items = [x for x in data['items'] if x['language'] == 'korean' and x['level'] == level]
            self.assertEqual(len(items), 7)
            self.assertEqual({kind: sum(x['type'] == kind for x in items) for kind in ('短对话', '长对话', '简单文章')},
                             {'短对话': 2, '长对话': 2, '简单文章': 3})
            for item in items:
                self.assertTrue(item['knowledge_points'])
                self.assertTrue(all(p['id'] in ids for p in item['knowledge_points']))
        for item in data['items']:
            for q in item['questions']:
                self.assertEqual(len(set(q['options'])), 4)
                self.assertTrue(q['explanation'])
            if item['language'] == 'english' and item['level'] == 'B2':
                self.assertTrue(80 <= len(item['script'].split()) <= 150)


if __name__ == '__main__':
    unittest.main()
