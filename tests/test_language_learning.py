"""Language questions, cross-device persistence and course progress use real HTTP."""
from pathlib import Path
import json
import unittest
from unittest.mock import patch

import test_task_timing as timing

app = timing.app
learning = app.language_learning


class LanguageLearningTests(unittest.TestCase):
    req = timing.TaskTimingTests.req

    def setUp(self):
        timing.TaskTimingTests.setUp(self)
        self.resources = Path(self.temp.name) / 'language-fixtures'
        self.resources.mkdir()
        self.resource_patch = patch.object(learning, 'ROOT', self.resources)
        self.resource_patch.start()
        points = [{
            'id': f'en-b2-{i}', 'knowledge_point': f'Point {i}', 'reference': 'Original test question',
            'explanation': 'A fixture grammar explanation.', 'hint': 'Use the named form.',
            'checklist': ['Check the meaning and grammar.'],
            'examples': [{'target': f'Could I ask question {i}, version {j}?', 'zh': f'测试题{i}版本{j}'} for j in range(3)],
        } for i in range(12)]
        self.write('language_english.json', {'notice': 'Original practice.', 'levels': {'B2': {'label': 'B2', 'points': points}, 'B1': {'label': 'B1', 'points': points}}})
        self.write('language_korean.json', {'notice': 'Verified textbook notes.', 'levels': {'1': {'label': '第1册', 'points': [{**point, 'id': point['id'].replace('en-b2', 'ko-1')} for point in points]}}})
        self.write('language_courses.json', {'courses': [
            {'key': 'language-test-english', 'category': 'english', 'title': 'English test course',
             'url': 'https://example.com/english', 'description': 'Original curriculum fixture.',
             'lessons': [{'id': 'external-audio', 'title': 'Listen', 'section': 'Listening', 'format': '音频', 'url': 'https://example.com/audio'},
                         {'id': 'external-reading', 'title': 'Read', 'section': 'Reading', 'format': '图文', 'url': 'https://example.com/reading'}]},
            {'key': 'language-test-korean', 'category': 'korean', 'title': 'Korean portal',
             'url': 'https://example.com/korean', 'description': 'An external portal.', 'source': 'user', 'lessons': []},
        ]})
        with app.conn() as c:
            learning.seed_courses(c, 1)

    def tearDown(self):
        # Stop the HTTP server before restoring module resource paths.
        try:
            timing.TaskTimingTests.tearDown(self)
        finally:
            self.resource_patch.stop()

    def write(self, name, value):
        (self.resources / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def daily(self, day='2026-11-02', direction='mixed', token='owner', language='english', level='B2'):
        status, result = self.req(f'/language-practice?language={language}&level={level}&day={day}&direction={direction}', token=token)
        self.assertEqual(status, 200, result)
        return result

    def payload(self, item, **values):
        return {'language': item['language'], 'level': item['level'], 'day': item['day'],
                'direction': item['practice_direction'], 'exercise_id': item['id'], **values}

    def test_refresh_keeps_questions_and_next_day_uses_different_pairs(self):
        first = self.daily()
        self.assertEqual(len(first['items']), 5)
        self.assertEqual(first, self.daily())
        following = self.daily('2026-11-03')
        first_pairs = {(item['knowledge_point'], item['source_text'], item['model_answer']) for item in first['items']}
        second_pairs = {(item['knowledge_point'], item['source_text'], item['model_answer']) for item in following['items']}
        self.assertTrue(first_pairs.isdisjoint(second_pairs))
        self.assertEqual(self.req('/state')[1]['tasks'], [])

    def test_answers_progress_and_review_survive_new_requests_and_are_private(self):
        item = self.daily()['items'][0]
        payload = self.payload(item, answer='My own translated sentence.', completed=True, review=True)
        status, saved = self.req('/language-practice', 'PATCH', payload)
        self.assertEqual(status, 200, saved)
        reloaded = self.daily()['items'][0]
        self.assertEqual(reloaded['answer'], payload['answer'])
        self.assertTrue(reloaded['completed'] and reloaded['review'])
        self.assertEqual(self.daily()['summary']['completed'], 1)
        reviews = self.req('/language-practice/review?language=english&level=B2')[1]
        self.assertEqual([row['id'] for row in reviews['items']], [item['id']])
        other = self.daily(token=self.other_token)['items'][0]
        self.assertEqual(other['answer'], '')
        self.assertFalse(other['completed'] or other['review'])
        self.assertEqual(self.req('/language-practice/review?language=english&level=B2', token=self.other_token)[1]['items'], [])
        self.assertEqual(self.req('/language-practice?language=english&level=B2&day=2026-11-02', token=None)[0], 401)

    def test_daily_assignment_and_review_preserve_authored_question_after_bank_changes(self):
        before = self.daily()
        item = before['items'][0]
        self.assertEqual(self.req('/language-practice', 'PATCH', self.payload(item, answer='Stored answer', review=True))[0], 200)
        self.write('language_english.json', {'levels': {'B2': {'label': 'Updated metadata', 'points': []}}})
        after = self.daily()
        self.assertEqual(before['items'], [{**value, 'answer': '', 'review': False, 'updated_at': None} if value['id']==item['id'] else value for value in after['items']])
        review = self.req('/language-practice/review?language=english&level=B2')[1]['items'][0]
        self.assertEqual(review['source_text'], item['source_text'])
        self.assertEqual(review['model_answer'], item['model_answer'])
        self.assertEqual(review['practice_direction'], 'mixed')

    def test_direction_selection_reverses_the_same_meaning(self):
        target = self.daily(direction='to_target')['items'][0]
        chinese = self.daily(direction='to_zh')['items'][0]
        self.assertEqual(target['source_text'], chinese['model_answer'])
        self.assertEqual(target['model_answer'], chinese['source_text'])
        self.assertEqual(target['direction'], 'to_target')
        self.assertEqual(chinese['direction'], 'to_zh')

    def test_invalid_scope_and_empty_completion_do_not_overwrite_saved_translation(self):
        item = self.daily()['items'][0]
        valid = self.payload(item, answer='Keep this answer.', review=True)
        self.assertEqual(self.req('/language-practice', 'PATCH', valid)[0], 200)
        for override in ({'exercise_id':'forged-question'}, {'level':'C2'}, {'language':'spanish'},
                         {'day':'2026-02-30'}, {'direction':'sideways'}, {'answer':[]},
                         {'answer':'x'*4001}, {'completed':'true'}, {'review':1},
                         {'answer':'   ', 'completed':True}, {'user_id':2}):
            with self.subTest(override=override):
                self.assertEqual(self.req('/language-practice', 'PATCH', {**valid, **override})[0], 400)
        self.assertEqual(self.daily()['items'][0]['answer'], 'Keep this answer.')

    def test_partial_review_update_preserves_translation_and_can_clear_review(self):
        item = self.daily()['items'][0]
        self.req('/language-practice', 'PATCH', self.payload(item, answer='Saved translation.', completed=True, review=True))
        status, result = self.req('/language-practice', 'PATCH', self.payload(item, review=False))
        self.assertEqual(status, 200, result)
        self.assertEqual(result['item']['answer'], 'Saved translation.')
        self.assertTrue(result['item']['completed'])
        self.assertEqual(self.req('/language-practice/review?language=english&level=B2')[1]['items'], [])

    def test_translation_modes_have_separate_immutable_assignments_and_answers(self):
        mixed = self.daily()['items']
        original = next(item for item in mixed if item['direction']=='to_target')
        self.req('/language-practice', 'PATCH', self.payload(original, answer='Old mixed answer', completed=True, review=True))
        content = json.loads((self.resources/'language_english.json').read_text(encoding='utf-8'))
        for point in content['levels']['B2']['points']:
            for example in point['examples']:
                example['target'] += ' Revised question.'
        self.write('language_english.json', content)
        target = next(item for item in self.daily(direction='to_target')['items'] if item['id']==original['id'])
        self.assertNotEqual(target['model_answer'], original['model_answer'])
        self.assertEqual(target['answer'], '')
        self.assertFalse(target['completed'])
        self.req('/language-practice', 'PATCH', self.payload(target, answer='New fixed-mode answer', review=True))
        reloaded = next(item for item in self.daily()['items'] if item['id']==original['id'])
        self.assertEqual((reloaded['model_answer'],reloaded['answer']), (original['model_answer'],'Old mixed answer'))
        reviews = self.req('/language-practice/review?language=english&level=B2')[1]['items']
        self.assertEqual({item['practice_direction'] for item in reviews}, {'mixed','to_target'})
        self.assertEqual({item['answer'] for item in reviews}, {'Old mixed answer','New fixed-mode answer'})

    def test_level_and_direction_settings_sync_without_modifying_daily_tasks(self):
        status, result = self.req('/language-settings', 'PATCH', {'language':'english','level':'C1','direction':'to_target'})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.req('/state')[1]['language_settings']['english'], {'level':'C1','direction':'to_target'})
        self.assertEqual(self.req('/state', token=self.other_token)[1]['language_settings']['english']['level'], 'B2')
        self.assertEqual(self.req('/state')[1]['tasks'], [])
        self.assertEqual(self.req('/language-settings', 'PATCH', {'language':'korean','level':'7'})[0], 400)

    def test_external_audio_and_reading_progress_is_validated_and_owned(self):
        courses = self.req('/state')[1]['courses']
        english = next(item for item in courses if item['category']=='english')
        self.assertEqual((english['lesson_count'], english['trackable_count'], english['video_count']), (2, 2, 0))
        status, content = self.req(f"/courses/{english['id']}/lessons")
        self.assertEqual(status, 200, content)
        self.assertTrue(all(item['kind']=='external' and item['trackable'] for item in content['lessons']))
        for lesson in content['lessons']:
            url = f"/courses/{english['id']}/lessons/{lesson['id']}"
            self.assertEqual(self.req(url, 'PATCH', {'done':True}, token=self.other_token)[0], 404)
            self.assertEqual(self.req(url, 'PATCH', {'done':True})[0], 200)
            self.assertEqual(self.req(url, 'PATCH', {'done':True})[0], 200)
        english = next(item for item in self.req('/state')[1]['courses'] if item['id']==english['id'])
        self.assertEqual(english['completed_count'], 2)
        self.assertEqual(self.req(f"/courses/{english['id']}/lessons/unknown", 'PATCH', {'done':True})[0], 404)

    def test_bundle_seeding_preserves_user_edits_and_archiving(self):
        english = next(item for item in self.req('/state')[1]['courses'] if item['category']=='english')
        self.req(f"/courses/{english['id']}", 'PATCH', {'title':'My edited title', 'description':'My notes'})
        self.req(f"/courses/{english['id']}", 'DELETE')
        with app.conn() as c:
            learning.seed_courses(c, 1)
            learning.seed_courses(c, 1)
            row = c.execute('SELECT * FROM courses WHERE id=?', (english['id'],)).fetchone()
            self.assertEqual((row['title'], row['description'], row['archived']), ('My edited title','My notes',1))
            self.assertEqual(c.execute('SELECT count(*) FROM courses WHERE user_id=1').fetchone()[0], 2)

    def test_manual_courses_keep_a_valid_independent_category(self):
        status, result = self.req('/courses', 'POST', {'title':'My English', 'category':'english'})
        self.assertEqual(status, 201, result)
        cid = result['id']
        self.assertEqual(self.req(f'/courses/{cid}', 'PATCH', {'category':'korean'})[0], 200)
        row = next(item for item in self.req('/state')[1]['courses'] if item['id']==cid)
        self.assertEqual(row['category'], 'korean')
        self.assertEqual(self.req(f'/courses/{cid}', 'PATCH', {'category':'invalid'})[0], 400)


class PublishedLanguageContentTests(unittest.TestCase):
    def test_all_supported_levels_have_referenced_original_bilingual_questions(self):
        ids = set()
        for language, levels in learning.LANGUAGES.items():
            content = learning.read_json('language_' + language + '.json')
            self.assertEqual(set(content['levels']), set(levels))
            self.assertTrue(content['notice'])
            for level in levels:
                points = content['levels'][level]['points']
                self.assertGreaterEqual(len(points), 12)
                seen_sentences = set()
                for point in points:
                    self.assertNotIn(point['id'], ids)
                    ids.add(point['id'])
                    for key in ('knowledge_point', 'reference', 'explanation'):
                        self.assertTrue(point[key].strip())
                    self.assertTrue(point['examples'])
                    for example in point['examples']:
                        self.assertTrue(example['target'].strip())
                        self.assertTrue(example['zh'].strip())
                        self.assertNotIn(example['target'], seen_sentences)
                        seen_sentences.add(example['target'])
                    if language == 'korean':
                        self.assertIn('语法整理', point['reference'])
                        self.assertIn(level, point['reference'])
            raw = json.dumps(content, ensure_ascii=False)
            for forbidden in ('xwechat_files', 'BaiduNetdiskDownload', 'F:/Users/', 'F:\\Users\\', 'password='):
                self.assertNotIn(forbidden, raw)

    def test_complete_english_routes_and_two_user_korean_portals_are_not_duplicated(self):
        courses = learning.course_catalog()
        self.assertEqual(len(courses), 5)
        self.assertEqual(sum(course['category']=='english' for course in courses), 3)
        self.assertEqual(sum(course['category']=='korean' for course in courses), 2)
        self.assertEqual(len({course['key'] for course in courses}), 5)
        units = [lesson for course in courses for lesson in course.get('lessons', [])]
        self.assertEqual(len(units), 91)
        self.assertEqual(len({lesson['id'] for lesson in units}), 91)
        self.assertEqual(len({lesson['url'] for lesson in units}), 91)
        for lesson in units:
            self.assertTrue(lesson['url'].startswith(('https://learnenglish.britishcouncil.org/', 'https://www.open.edu/')))
        for course in courses:
            if course['category']=='korean':
                self.assertEqual(course['lessons'], [])
                self.assertEqual(course['source'], 'user')

    def test_daily_sentences_are_stable_and_change_on_consecutive_days_at_every_level(self):
        for language, levels in learning.LANGUAGES.items():
            for level in levels:
                first = learning.compose_questions(language, level, 'to_target', '2026-11-02')
                second = learning.compose_questions(language, level, 'to_target', '2026-11-03')
                self.assertEqual(len(first), 5)
                self.assertEqual(first, learning.compose_questions(language, level, 'to_target', '2026-11-02'))
                self.assertTrue({item['model_answer'] for item in first}.isdisjoint(item['model_answer'] for item in second))


if __name__ == '__main__':
    unittest.main()
