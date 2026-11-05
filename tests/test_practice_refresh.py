"""Real HTTP checks for rotating finite exercises without losing saved history."""
from concurrent.futures import ThreadPoolExecutor
import json
import unittest

import test_language_learning as fixtures


class PracticeRefreshTests(unittest.TestCase):
    setUp = fixtures.LanguageLearningTests.setUp
    tearDown = fixtures.LanguageLearningTests.tearDown
    req = fixtures.LanguageLearningTests.req
    write = fixtures.LanguageLearningTests.write
    daily = fixtures.LanguageLearningTests.daily
    payload = fixtures.LanguageLearningTests.payload
    history = fixtures.LanguageLearningTests.history

    def selection(self, language='english', level='B2'):
        return {'language': language, 'level': level, 'day': '2026-11-05', 'direction': 'to_zh'}

    def next(self, selection=None, token='owner'):
        status, data = self.req('/language-practice/next', 'POST', selection or self.selection(), token)
        self.assertEqual(status, 200, data)
        return data

    def test_english_and_korean_groups_change_and_persist_across_devices(self):
        for language, level in [('english', 'B2'), ('korean', '1')]:
            with self.subTest(language=language):
                s = self.selection(language, level)
                before = self.daily(s['day'], s['direction'], language=language, level=level)
                after = self.next(s)
                self.assertTrue(after['changed'])
                self.assertEqual(len(after['items']), 5)
                self.assertFalse({i['id'] for i in before['items']} & {i['id'] for i in after['items']})
                self.assertEqual(after['items'], self.daily(s['day'], s['direction'], language=language, level=level)['items'])
                self.assertIn('题库有限', after['refresh_message'])

    def test_old_saved_snapshot_grading_and_manual_judgment_survive_rotation_and_edit(self):
        s = self.selection()
        old = self.daily(s['day'], s['direction'])['items'][0]
        code, saved = self.req('/language-practice', 'PATCH', self.payload(old, answer=old['model_answer'], grade=True))
        self.assertEqual(code, 200, saved)
        code, judged = self.req('/language-practice', 'PATCH', self.payload(old, manual_result='correct'))
        self.assertEqual(code, 200, judged)
        original = judged['item']
        self.next(s)
        history = self.history()['items']
        self.assertEqual(len(history), 1)
        for key in ['id', 'source_text', 'model_answer', 'answer', 'grading', 'manual_review']:
            self.assertEqual(history[0][key], original[key])
        code, edited = self.req('/language-practice', 'PATCH', self.payload(old, answer='我保存的另一种译法', grade=True))
        self.assertEqual(code, 200, edited)
        self.assertEqual(edited['item']['answer'], '我保存的另一种译法')
        self.assertIsNone(edited['item']['manual_review'])
        code, reviewed = self.req('/language-practice', 'PATCH', self.payload(old, manual_result='incorrect'))
        self.assertEqual(code, 200, reviewed)
        self.assertEqual(reviewed['item']['manual_review']['status'], 'incorrect')
        self.assertEqual(reviewed['item']['source_text'], old['source_text'])

    def test_rotation_and_history_snapshot_cannot_cross_accounts(self):
        s = self.selection()
        old = self.daily(s['day'], s['direction'])['items'][0]
        self.assertEqual(self.req('/language-practice', 'PATCH', self.payload(old, answer='owner answer'))[0], 200)
        self.next(s)
        other = self.daily(s['day'], s['direction'], token=self.other_token)
        self.assertEqual(other['items'][0]['id'], old['id'])
        self.next(s, token=self.other_token)
        self.assertEqual(self.req('/language-practice', 'PATCH', self.payload(old, answer='foreign edit'), self.other_token)[0], 400)
        self.assertEqual(self.history()['items'][0]['answer'], 'owner answer')
        self.assertEqual(self.history(token=self.other_token)['items'], [])
        self.assertEqual(self.req('/language-practice/next', 'POST', s, token='')[0], 401)

    def test_small_or_missing_bank_is_explicit_and_does_not_erase_answers(self):
        self.write('language_korean.json', {'levels': {'1': {'points': [{
            'id': 'single', 'knowledge_point': '은/는',
            'examples': [{'target': '저는 학생이에요.', 'zh': '我是学生。'}],
        }]}}})
        s = self.selection('korean', '1')
        old = self.daily(s['day'], s['direction'], language='korean', level='1')['items'][0]
        self.req('/language-practice', 'PATCH', self.payload(old, answer='我是学生。'))
        after = self.next(s)
        self.assertFalse(after['changed'])
        self.assertEqual(after['items'][0]['answer'], '我是学生。')
        self.assertIn('可用题目已覆盖', after['refresh_message'])
        absent = self.next(self.selection('korean', '2'))
        self.assertEqual(absent['items'], [])
        self.assertFalse(absent['changed'])

    def test_concurrent_devices_increment_cursor_atomically_and_invalid_requests_do_not(self):
        s = self.selection()
        self.daily(s['day'], s['direction'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.next(s), range(2)))
        self.assertNotEqual([i['id'] for i in results[0]['items']], [i['id'] for i in results[1]['items']])
        with fixtures.app.conn() as c:
            row = c.execute('SELECT rotation,questions FROM language_daily_sets WHERE user_id=1').fetchone()
            self.assertEqual(row['rotation'], 2)
            before = tuple(row)
        self.assertEqual(self.req('/language-practice/next', 'POST', {**s, 'unexpected': True})[0], 400)
        self.assertEqual(self.req('/language-practice/next', 'POST', {**s, 'language': []})[0], 400)
        with fixtures.app.conn() as c:
            self.assertEqual(tuple(c.execute('SELECT rotation,questions FROM language_daily_sets WHERE user_id=1').fetchone()), before)
        current = self.daily(s['day'], s['direction'])
        self.assertIn([i['id'] for i in current['items']], [[i['id'] for i in r['items']] for r in results])

    def test_legacy_daily_set_migration_preserves_original_columns(self):
        s = self.selection()
        before = self.daily(s['day'], s['direction'])
        with fixtures.app.conn() as c:
            row = tuple(c.execute('SELECT user_id,language,level,day,direction,questions FROM language_daily_sets').fetchone())
            c.execute('DROP TABLE language_daily_sets')
            c.execute('CREATE TABLE language_daily_sets(user_id INTEGER,language TEXT,level TEXT,day TEXT,direction TEXT,questions TEXT,PRIMARY KEY(user_id,language,level,day,direction))')
            c.execute('INSERT INTO language_daily_sets VALUES(?,?,?,?,?,?)', row)
            fixtures.learning.init(c)
            self.assertEqual(tuple(c.execute('SELECT user_id,language,level,day,direction,questions FROM language_daily_sets').fetchone()), row)
            self.assertEqual(c.execute('SELECT rotation FROM language_daily_sets').fetchone()['rotation'], 0)
        self.assertEqual(before['items'], self.daily(s['day'], s['direction'])['items'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
