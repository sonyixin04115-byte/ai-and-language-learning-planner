"""HTTP persistence, server-owned scoring, content and account boundaries."""
import json
import io
from pathlib import Path
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


class ListeningContentTests(unittest.TestCase):
    def test_original_bank_has_all_required_types_and_valid_book_points(self):
        data = listening.bank()
        self.assertEqual(len(data['items']), 18)
        self.assertEqual(sum(len(x['questions']) for x in data['items']), 54)
        korean = json.loads((listening.ROOT / 'language_korean.json').read_text(encoding='utf-8'))
        ids = {x['id'] for lev in korean['levels'].values() for x in lev['points']}
        for level in ('1', '2'):
            items = [x for x in data['items'] if x['language'] == 'korean' and x['level'] == level]
            self.assertEqual(len(items), 6)
            self.assertEqual({kind: sum(x['type'] == kind for x in items) for kind in ('短对话', '长对话', '简单文章')},
                             {'短对话': 2, '长对话': 2, '简单文章': 2})
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
