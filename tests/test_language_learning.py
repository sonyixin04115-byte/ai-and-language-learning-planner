"""Language questions, cross-device persistence and course progress use real HTTP."""
from pathlib import Path
import json
import sqlite3
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
             'instruction_language': '中文讲解', 'learning_role': 'main',
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

    def video_fixture(self):
        course = {
            'key': 'ai-test-video', 'category': 'ai', 'title': 'AI introductory video fixture',
            'url': 'https://example.com/ai-videos', 'description': 'A Chinese AI video course.',
            'source': 'supplement', 'instruction_language': '中文讲解', 'learning_role': 'main',
            'lessons': [{'id': f'ai-video-{i}', 'title': f'AI video {i}', 'section': 'AI introduction',
                         'format': '视频', 'unit_type': 'video', 'url': f'https://example.com/ai-video-{i}'}
                        for i in (1, 2)],
        }
        self.write('video_courses.json', {'version': 1, 'courses': [course]})
        return course

    def daily(self, day='2026-11-02', direction='mixed', token='owner', language='english', level='B2'):
        status, result = self.req(f'/language-practice?language={language}&level={level}&day={day}&direction={direction}', token=token)
        self.assertEqual(status, 200, result)
        return result

    def payload(self, item, **values):
        return {'language': item['language'], 'level': item['level'], 'day': item['day'],
                'direction': item['practice_direction'], 'exercise_id': item['id'], **values}

    def grading_item(self, day='2026-11-02', direction='to_zh', token='owner'):
        self.write('language_korean.json', {'levels': {'1': {'label': '第1册', 'points': [{
            'id': 'ko-grade', 'knowledge_point': '은/는',
            'examples': [{'target': '저는 중국에서 왔어요.', 'zh': '我来自中国。'}],
        }]}}})
        return self.daily(day, direction, token, 'korean', '1')['items'][0]

    def test_grading_persists_in_daily_and_review_without_changing_completion(self):
        item = self.grading_item()
        status, result = self.req('/language-practice', 'PATCH', self.payload(item, answer='我来自中国', grade=True))
        self.assertEqual(status, 200, result)
        saved = result['item']
        self.assertEqual(saved['grading']['status'], 'correct')
        self.assertEqual(saved['grading']['graded_answer'], '我来自中国')
        self.assertEqual(saved['grading']['engine'], 'reference-v1')
        self.assertTrue(saved['grading']['graded_at'].endswith('Z'))
        self.assertFalse(saved['completed'] or saved['review'])
        self.assertEqual(self.daily(direction='to_zh', language='korean', level='1')['items'][0]['grading'], saved['grading'])
        result = self.req('/language-practice', 'PATCH', self.payload(item, completed=True, review=True))[1]['item']
        self.assertEqual(result['grading'], saved['grading'])
        self.assertTrue(result['completed'] and result['review'])
        review = self.req('/language-practice/review?language=korean&level=1')[1]['items'][0]
        self.assertEqual(review['grading'], saved['grading'])
        self.assertIsNone(self.grading_item(token=self.other_token)['grading'])

    def test_any_raw_answer_change_invalidates_grading_even_if_meaning_matches(self):
        item = self.grading_item()
        self.req('/language-practice', 'PATCH', self.payload(item, answer='我来自中国', grade=True, review=True))
        result = self.req('/language-practice', 'PATCH', self.payload(item, answer='我来自中国。'))[1]['item']
        self.assertIsNone(result['grading'])
        self.assertTrue(result['review'])
        with app.conn() as c:
            self.assertEqual(c.execute('SELECT grading FROM language_answers WHERE user_id=1').fetchone()[0], '')
        result = self.req('/language-practice', 'PATCH', self.payload(item, grade=True))[1]['item']
        self.assertEqual(result['grading']['status'], 'correct')
        before = result['grading']
        result = self.req('/language-practice', 'PATCH', self.payload(item, grade=False, completed=True))[1]['item']
        self.assertEqual(result['grading'], before)

    def test_grading_uses_frozen_question_and_scope_not_client_supplied_reference(self):
        item = self.grading_item()
        first = self.req('/language-practice', 'PATCH', self.payload(item, answer='我来自中国', grade=True, review=True))[1]['item']
        self.write('language_korean.json', {'levels': {'1': {'points': [{
            'id': 'ko-grade', 'knowledge_point': 'Changed point',
            'examples': [{'target': '저는 한국에서 왔어요.', 'zh': '我来自韩国。'}],
        }]}}})
        result = self.req('/language-practice', 'PATCH', self.payload(item, answer='我来自中国', grade=True))[1]['item']
        self.assertEqual(result['grading']['reference_answer'], '我来自中国。')
        self.assertEqual(result['grading']['status'], 'correct')
        review = self.req('/language-practice/review?language=korean&level=1')[1]['items'][0]
        self.assertEqual((review['source_text'], review['grading']), (item['source_text'], result['grading']))
        for values in ({'model_answer': 'fake'}, {'reference_answer': 'fake'}, {'grading': first['grading']},
                       {'user_id': 2}, {'grade': 1}, {'grade': 'true'}, {'grade': None},
                       {'direction': 'to_target'}, {'level': '2'}, {'day': '2026-02-30'}, {'exercise_id': 'not-owned'}):
            with self.subTest(values=values):
                payload = {**self.payload(item, answer='我来自中国', grade=True), **values}
                self.assertEqual(self.req('/language-practice', 'PATCH', payload)[0], 400)
        self.assertEqual(self.daily(direction='to_zh', language='korean', level='1')['items'][0], result)

    def test_empty_grading_is_rejected_without_creating_answers_or_daily_sets(self):
        item = self.grading_item()
        self.req('/language-practice', 'PATCH', self.payload(item, answer='我来自中国', grade=True))
        with app.conn() as c:
            before = {table: [tuple(row) for row in c.execute('SELECT * FROM '+table)]
                      for table in ('language_answers', 'language_daily_sets')}
        status, error = self.req('/language-practice', 'PATCH', self.payload(item, day='2026-11-03', answer=' \n ', grade=True))
        self.assertEqual(status, 400)
        self.assertIn('先写', error['error'])
        with app.conn() as c:
            after = {table: [tuple(row) for row in c.execute('SELECT * FROM '+table)] for table in before}
        self.assertEqual(after, before)

    def test_grading_is_separate_between_accounts_dates_and_practice_modes(self):
        original = self.grading_item()
        saved = self.req('/language-practice', 'PATCH', self.payload(original, answer='我来自中国', grade=True))[1]['item']
        for day, direction, token in (('2026-11-03', 'to_zh', 'owner'), ('2026-11-02', 'mixed', 'owner'),
                                      ('2026-11-02', 'to_target', 'owner'),
                                      ('2026-11-02', 'to_zh', self.other_token)):
            with self.subTest(day=day, direction=direction, token=token):
                item = self.grading_item(day, direction, token)
                self.assertIsNone(item['grading'])
                response = self.req('/language-practice', 'PATCH', self.payload(item, answer=item['model_answer'], grade=True), token=token)
                self.assertEqual(response[0], 200, response)
                self.assertEqual(response[1]['item']['grading']['status'], 'correct')
                self.assertEqual(self.daily(direction='to_zh', language='korean', level='1')['items'][0], saved)

    def test_same_exercise_id_in_another_level_uses_its_own_reference_and_grading(self):
        original = self.grading_item()
        saved = self.req('/language-practice', 'PATCH', self.payload(original, answer='我来自中国', grade=True))[1]['item']
        bank = json.loads((self.resources/'language_korean.json').read_text(encoding='utf-8'))
        bank['levels']['2'] = {'label': '第2册', 'points': [{
            'id': 'ko-grade', 'knowledge_point': '은/는',
            'examples': [{'target': '저는 한국에서 왔어요.', 'zh': '我来自韩国。'}],
        }]}
        self.write('language_korean.json', bank)
        item = self.daily(direction='to_zh', language='korean', level='2')['items'][0]
        self.assertEqual(item['id'], original['id'])
        self.assertIsNone(item['grading'])
        result = self.req('/language-practice', 'PATCH', self.payload(item, answer='我来自韩国', grade=True))[1]['item']
        self.assertEqual((result['grading']['status'], result['grading']['reference_answer']), ('correct', '我来自韩国。'))
        self.assertEqual(self.daily(direction='to_zh', language='korean', level='1')['items'][0], saved)

    def test_corrupt_or_obsolete_grading_is_hidden_without_losing_the_answer(self):
        item = self.grading_item()
        saved = self.req('/language-practice', 'PATCH', self.payload(item, answer='我来自中国', grade=True))[1]['item']
        for value in ('not json', '[]', json.dumps({**saved['grading'], 'engine': 'unknown'}),
                      json.dumps({**saved['grading'], 'status': []}), json.dumps({**saved['grading'], 'status': {}}),
                      json.dumps({**saved['grading'], 'graded_answer': 'different'}),
                      json.dumps({**saved['grading'], 'reference_answer': 'different'})):
            with self.subTest(value=value):
                with app.conn() as c:
                    c.execute('UPDATE language_answers SET grading=? WHERE user_id=1', (value,))
                result = self.daily(direction='to_zh', language='korean', level='1')['items'][0]
                self.assertIsNone(result['grading'])
                self.assertEqual(result['answer'], '我来自中国')

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
        self.assertEqual((english['instruction_language'], english['learning_role']), ('中文讲解', 'main'))
        self.assertEqual((english['lesson_count'], english['trackable_count'], english['video_count']), (2, 2, 0))
        status, content = self.req(f"/courses/{english['id']}/lessons")
        self.assertEqual(status, 200, content)
        self.assertEqual((content['course']['instruction_language'], content['course']['learning_role']),
                         ('中文讲解', 'main'))
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

    def test_video_bundle_startup_preserves_existing_course_identity_notes_archiving_and_progress(self):
        self.assertFalse((self.resources / 'video_courses.json').exists())
        self.assertEqual([course['key'] for course in learning.course_catalog()],
                         ['language-test-english', 'language-test-korean'])
        english = next(item for item in self.req('/state')[1]['courses'] if item['category']=='english')
        self.assertEqual(self.req(f"/courses/{english['id']}/lessons/external-audio", 'PATCH', {'done':True})[0], 200)
        with app.conn() as c:
            language_rows = [dict(row) for row in c.execute(
                'SELECT * FROM courses WHERE user_id=1 ORDER BY id')]
        fixture = self.video_fixture()
        app.init()
        app.init()
        courses = self.req('/state')[1]['courses']
        video = next(item for item in courses if item['curriculum_key']==fixture['key'])
        self.assertEqual(sum(item['curriculum_key']==fixture['key'] for item in courses), 1)
        self.assertEqual(self.req(f"/courses/{video['id']}/lessons/ai-video-1", 'PATCH', {'done':True})[0], 200)
        self.assertEqual(self.req(f"/courses/{video['id']}", 'PATCH',
                                 {'title':'My AI title', 'description':'My AI learning notes'})[0], 200)
        self.assertEqual(self.req(f"/courses/{video['id']}", 'DELETE')[0], 200)
        with app.conn() as c:
            saved_video = dict(c.execute('SELECT * FROM courses WHERE id=?', (video['id'],)).fetchone())
            progress = [tuple(row) for row in c.execute('SELECT * FROM lesson_progress ORDER BY course_id,lesson_id')]
        app.init()
        app.init()
        with app.conn() as c:
            self.assertEqual(dict(c.execute('SELECT * FROM courses WHERE id=?', (video['id'],)).fetchone()), saved_video)
            self.assertEqual(saved_video['archived'], 1)
            self.assertEqual(c.execute('SELECT count(*) FROM courses WHERE user_id=1 AND curriculum_key=?',
                                       (fixture['key'],)).fetchone()[0], 1)
            self.assertEqual([dict(c.execute('SELECT * FROM courses WHERE id=?', (row['id'],)).fetchone())
                              for row in language_rows], language_rows)
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM lesson_progress ORDER BY course_id,lesson_id')], progress)
        self.assertNotIn(video['id'], {item['id'] for item in self.req('/state')[1]['courses']})

    def test_ai_external_video_lessons_and_completion_are_owned(self):
        fixture = self.video_fixture()
        with app.conn() as c:
            learning.seed_courses(c, 1)
        video = next(item for item in self.req('/state')[1]['courses'] if item['curriculum_key']==fixture['key'])
        cid = video['id']
        self.assertEqual((video['category'], video['instruction_language'], video['learning_role']),
                         ('ai', '中文讲解', 'main'))
        self.assertEqual((video['lesson_count'], video['trackable_count'], video['video_count']), (2, 2, 2))
        status, content = self.req(f'/courses/{cid}/lessons')
        self.assertEqual(status, 200, content)
        self.assertEqual([item['id'] for item in content['lessons']], ['ai-video-1', 'ai-video-2'])
        self.assertTrue(all(item['kind']=='external' and item['trackable'] and not item['done']
                            for item in content['lessons']))
        self.assertEqual(self.req(f'/courses/{cid}/lessons', token=self.other_token)[0], 404)
        self.assertEqual(self.req(f'/courses/{cid}/lessons/ai-video-1', 'PATCH', {'done':True}, token=self.other_token)[0], 404)
        self.assertEqual(self.req(f'/courses/{cid}/lessons/ai-video-1', 'PATCH', {'done':True}, token=None)[0], 401)
        for _ in range(2):
            status, result = self.req(f'/courses/{cid}/lessons/ai-video-1', 'PATCH', {'done':True})
            self.assertEqual(status, 200, result)
            self.assertEqual(result['completed_count'], 1)
        reloaded = self.req(f'/courses/{cid}/lessons')[1]
        self.assertEqual([item['done'] for item in reloaded['lessons']], [True, False])
        self.assertEqual(self.req(f'/courses/{cid}/lessons/unknown', 'PATCH', {'done':True})[0], 404)
        status, cleared = self.req(f'/courses/{cid}/lessons/ai-video-1', 'PATCH', {'done':False})
        self.assertEqual(status, 200, cleared)
        self.assertEqual(cleared['completed_count'], 0)

    def test_video_bundle_adopts_existing_url_without_duplicating_or_overwriting_personal_notes(self):
        fixture = self.video_fixture()
        status, manual = self.req('/courses', 'POST', {
            'title':'My AI course', 'url':fixture['url'], 'category':'ai', 'description':'My personal notes'})
        self.assertEqual(status, 201, manual)
        cid = manual['id']
        with app.conn() as c:
            learning.seed_courses(c, 1)
            learning.seed_courses(c, 1)
            matches = c.execute('SELECT * FROM courses WHERE user_id=1 AND url=?', (fixture['url'],)).fetchall()
            self.assertEqual(len(matches), 1)
            self.assertEqual((matches[0]['id'], matches[0]['title'], matches[0]['description'], matches[0]['curriculum_key']),
                             (cid, 'My AI course', 'My personal notes', fixture['key']))
        self.assertEqual(self.req(f'/courses/{cid}/lessons')[0], 200)

    def upgraded_catalog(self):
        catalog = json.loads((self.resources / 'language_courses.json').read_text(encoding='utf-8'))
        course = catalog['courses'][0]
        old = {field: course.get(field, 'supplement' if field=='source' else '')
               for field in ('title', 'url', 'description', 'source', 'category')}
        new = {'title': 'Updated Chinese-led course', 'url': 'https://example.com/updated-english',
               'description': 'Updated factory guidance.', 'source': 'user', 'category': 'korean'}
        course.update(new, previous_defaults=[old], instruction_language='英文原声，配中文导学', learning_role='practice')
        self.write('language_courses.json', catalog)
        return old, new

    def test_known_defaults_migrate_in_place_and_preserve_completed_lessons_and_ownership(self):
        english = next(item for item in self.req('/state')[1]['courses'] if item['category']=='english')
        cid = english['id']
        self.assertEqual(self.req(f'/courses/{cid}/lessons/external-audio', 'PATCH', {'done':True})[0], 200)
        with app.conn() as c:
            learning.seed_courses(c, 2)
            other_before = dict(c.execute('SELECT * FROM courses WHERE user_id=2 AND curriculum_key=?',
                                          ('language-test-english',)).fetchone())
            progress_before = [tuple(row) for row in c.execute('SELECT * FROM lesson_progress')]
        old, new = self.upgraded_catalog()
        with app.conn() as c:
            learning.seed_courses(c, 1)
            changes = c.total_changes
            learning.seed_courses(c, 1)
            self.assertEqual(c.total_changes, changes, 'A second seed must not perform a metadata write')
            self.assertEqual(dict(c.execute('SELECT * FROM courses WHERE user_id=2 AND curriculum_key=?',
                                           ('language-test-english',)).fetchone()), other_before)
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM lesson_progress')], progress_before)
            self.assertEqual(c.execute('SELECT count(*) FROM courses WHERE user_id=1').fetchone()[0], 2)
        refreshed = next(item for item in self.req('/state')[1]['courses'] if item['id']==cid)
        for field, value in new.items():
            self.assertEqual(refreshed[field], value, field)
        self.assertEqual((refreshed['curriculum_key'], refreshed['completed_count']), ('language-test-english', 1))
        self.assertEqual((refreshed['instruction_language'], refreshed['learning_role']),
                         ('英文原声，配中文导学', 'practice'))
        status, content = self.req(f'/courses/{cid}/lessons')
        self.assertEqual(status, 200, content)
        self.assertEqual([(row['id'], row['done']) for row in content['lessons']],
                         [('external-audio', True), ('external-reading', False)])

    def test_default_migration_preserves_each_custom_field_independently(self):
        cid = next(item['id'] for item in self.req('/state')[1]['courses'] if item['category']=='english')
        old, new = self.upgraded_catalog()
        custom = {'title': 'My custom course', 'url': 'https://example.com/my-own-resource',
                  'description': 'My personal learning notes', 'source': 'supplement', 'category': 'ai'}
        # The old source is supplement: use a distinct custom value and change
        # the catalogue's old/new source around it, so this also tests source.
        source_catalog = json.loads((self.resources / 'language_courses.json').read_text(encoding='utf-8'))
        for field in old:
            with self.subTest(field=field):
                current_old = dict(old)
                current_new = dict(new)
                if field == 'source':
                    current_old['source'] = 'user'
                    current_new['source'] = 'supplement'
                course = source_catalog['courses'][0]
                course.update(current_new, previous_defaults=[current_old])
                self.write('language_courses.json', source_catalog)
                with app.conn() as c:
                    c.execute('UPDATE courses SET '+','.join(name+'=?' for name in current_old)+' WHERE id=?',
                              tuple(current_old.values())+(cid,))
                self.assertEqual(self.req(f'/courses/{cid}', 'PATCH', {field:custom[field]})[0], 200)
                with app.conn() as c:
                    learning.seed_courses(c, 1)
                actual = next(item for item in self.req('/state')[1]['courses'] if item['id']==cid)
                self.assertEqual(actual[field], custom[field])
                for untouched in current_new.keys()-{field}:
                    self.assertEqual(actual[untouched], current_new[untouched], untouched)

    def test_known_default_migration_keeps_archived_course_and_all_learner_edits(self):
        cid = next(item['id'] for item in self.req('/state')[1]['courses'] if item['category']=='english')
        self.req(f'/courses/{cid}/lessons/external-audio', 'PATCH', {'done':True})
        self.req(f'/courses/{cid}', 'PATCH', {'title':'My edited title', 'description':'My own notes'})
        self.assertEqual(self.req(f'/courses/{cid}', 'DELETE')[0], 200)
        old, new = self.upgraded_catalog()
        with app.conn() as c:
            before = [tuple(row) for row in c.execute('SELECT * FROM lesson_progress')]
            learning.seed_courses(c, 1)
            learning.seed_courses(c, 1)
            row = c.execute('SELECT * FROM courses WHERE id=?', (cid,)).fetchone()
            self.assertEqual((row['title'], row['description'], row['archived']),
                             ('My edited title', 'My own notes', 1))
            self.assertEqual(row['url'], new['url'])
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM lesson_progress')], before)
            self.assertEqual(c.execute('SELECT count(*) FROM courses WHERE user_id=1').fetchone()[0], 2)
        self.assertNotIn(cid, [item['id'] for item in self.req('/state')[1]['courses']])
        self.assertEqual(self.req(f'/courses/{cid}/lessons')[0], 404)

    def test_new_video_main_keeps_dut_notes_archiving_identity_and_completion(self):
        published = json.loads((Path(__file__).resolve().parents[1] /
                                'backend/language_courses.json').read_text(encoding='utf-8-sig'))
        dut = next(course for course in published['courses'] if course['key']=='english-zh-ielts-dut')
        sun = next(course for course in published['courses'] if course['key']=='english-zh-ielts-sun-32')
        previous = {**dut, **dut['previous_defaults'][-1]}
        self.write('language_courses.json', {'courses': [previous]})
        with app.conn() as c:
            learning.seed_courses(c, 1)
        original = next(course for course in self.req('/state')[1]['courses']
                        if course['curriculum_key']==dut['key'])
        cid, lesson_id = original['id'], dut['lessons'][0]['id']
        self.assertEqual(self.req(f'/courses/{cid}/lessons/{lesson_id}', 'PATCH', {'done':True})[0], 200)
        self.assertEqual(self.req(f'/courses/{cid}', 'PATCH', {'description':'My existing DUT learning notes'})[0], 200)
        self.assertEqual(self.req(f'/courses/{cid}', 'DELETE')[0], 200)
        with app.conn() as c:
            completed_before = [tuple(row) for row in c.execute('SELECT * FROM lesson_progress')]
        self.write('language_courses.json', {'courses': [sun, dut]})
        with app.conn() as c:
            learning.seed_courses(c, 1)
            changes = c.total_changes
            learning.seed_courses(c, 1)
            self.assertEqual(c.total_changes, changes)
            retained = dict(c.execute('SELECT * FROM courses WHERE id=?', (cid,)).fetchone())
            self.assertEqual((retained['curriculum_key'], retained['archived'], retained['description']),
                             (dut['key'], 1, 'My existing DUT learning notes'))
            self.assertEqual(retained['title'], dut['title'])
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM lesson_progress')], completed_before)
            self.assertEqual(c.execute('SELECT count(*) FROM courses WHERE user_id=1 AND curriculum_key=?',
                                       (sun['key'],)).fetchone()[0], 1)
        current = next(course for course in self.req('/state')[1]['courses']
                       if course['curriculum_key']==sun['key'])
        self.assertEqual((current['learning_role'], current['video_count'], current['trackable_count']),
                         ('main', 32, 32))
        content = self.req(f"/courses/{current['id']}/lessons")[1]
        self.assertEqual([lesson['id'] for lesson in content['lessons']], [lesson['id'] for lesson in sun['lessons']])
        self.assertFalse(any(lesson['done'] for lesson in content['lessons']))
        self.assertEqual(self.req(f"/courses/{current['id']}/lessons", token=self.other_token)[0], 404)
        self.assertEqual(self.req(f'/courses/{cid}/lessons')[0], 404)

    def test_course_changes_without_known_previous_defaults_do_not_replace_saved_metadata(self):
        original = next(item for item in self.req('/state')[1]['courses'] if item['category']=='english')
        catalog = json.loads((self.resources / 'language_courses.json').read_text(encoding='utf-8'))
        catalog['courses'][0].update(title='New factory title', description='New factory description')
        self.write('language_courses.json', catalog)
        with app.conn() as c:
            learning.seed_courses(c, 1)
        saved = next(item for item in self.req('/state')[1]['courses'] if item['id']==original['id'])
        self.assertEqual((saved['title'], saved['description']), (original['title'], original['description']))

    def test_startup_migration_repeats_without_losing_course_identity_or_done_state(self):
        cid = next(item['id'] for item in self.req('/state')[1]['courses'] if item['category']=='english')
        self.assertEqual(self.req(f'/courses/{cid}/lessons/external-audio', 'PATCH', {'done':True})[0], 200)
        old, new = self.upgraded_catalog()
        app.init()
        app.init()
        status, state = self.req('/state')
        self.assertEqual(status, 200, state)
        migrated = next(item for item in state['courses'] if item['id']==cid)
        self.assertEqual((migrated['title'], migrated['completed_count']), (new['title'], 1))
        self.assertEqual(sum(item['curriculum_key']=='language-test-english' for item in state['courses']), 1)
        content = self.req(f'/courses/{cid}/lessons')[1]
        self.assertEqual(content['lessons'][0]['id'], 'external-audio')
        self.assertTrue(content['lessons'][0]['done'])

    def test_chinese_guides_merge_by_stable_id_without_changing_original_resource_or_progress(self):
        cid = next(item['id'] for item in self.req('/state')[1]['courses'] if item['category']=='english')
        status, before = self.req(f'/courses/{cid}/lessons')
        self.assertEqual(status, 200, before)
        self.req(f'/courses/{cid}/lessons/external-audio', 'PATCH', {'done':True})
        self.write('english_lesson_guides.json', {
            'version': 1, 'notice': 'Original Chinese study guidance.',
            'lessons': {'external-audio': {'zh_title':'听力课题', 'zh_guide':'先听大意，再核对原文并复述。',
                                          'id':'must-not-change', 'url':'https://example.com/must-not-replace'},
                        'unknown-id': {'zh_title':'Unrelated', 'zh_guide':'Never add a new lesson.'}},
        })
        status, after = self.req(f'/courses/{cid}/lessons')
        self.assertEqual(status, 200, after)
        self.assertEqual(len(after['lessons']), 2)
        for old, updated in zip(before['lessons'], after['lessons']):
            self.assertEqual((old['id'], old['url'], old['title']),
                             (updated['id'], updated['url'], updated['title']))
        audio, reading = after['lessons']
        self.assertEqual((audio['zh_title'], audio['zh_guide'], audio['done']),
                         ('听力课题', '先听大意，再核对原文并复述。', True))
        self.assertNotIn('zh_title', reading)
        self.assertEqual(after['course']['completed_count'], 1)
        self.write('english_lesson_guides.json', {'version':1, 'lessons':{
            'external-audio': {'zh_title':'更新后的中文课题', 'zh_guide':'更新后的中文导学。'}}})
        refreshed = self.req(f'/courses/{cid}/lessons')[1]['lessons'][0]
        self.assertEqual((refreshed['zh_title'], refreshed['zh_guide'], refreshed['done']),
                         ('更新后的中文课题', '更新后的中文导学。', True))

    def retire_fixture_english_course(self):
        catalog = json.loads((self.resources / 'language_courses.json').read_text(encoding='utf-8'))
        retired = catalog['courses'].pop(0)
        catalog['retired_course_keys'] = [retired['key'], 'not-previously-seeded']
        self.write('language_courses.json', catalog)
        return retired

    def test_retirement_hides_only_owned_system_bundle_and_preserves_notes_progress_and_manual_course(self):
        english = next(item for item in self.req('/state')[1]['courses'] if item['category']=='english')
        cid = english['id']
        self.assertEqual(self.req(f'/courses/{cid}/lessons/external-audio', 'PATCH', {'done':True})[0], 200)
        self.assertEqual(self.req(f'/courses/{cid}', 'PATCH',
                                 {'title':'My revised bundle', 'description':'Keep my personal notes.'})[0], 200)
        status, manual = self.req('/courses', 'POST', {
            'title':english['title'], 'url':english['url'], 'category':'english',
            'description':'My independently added same-name course.'})
        self.assertEqual(status, 201, manual)
        manual_id = manual['id']
        with app.conn() as c:
            learning.seed_courses(c, 2)
            original = dict(c.execute('SELECT * FROM courses WHERE id=?', (cid,)).fetchone())
            manual_before = dict(c.execute('SELECT * FROM courses WHERE id=?', (manual_id,)).fetchone())
            other_before = dict(c.execute('SELECT * FROM courses WHERE user_id=2 AND curriculum_key=?',
                                          ('language-test-english',)).fetchone())
            progress_before = [tuple(row) for row in c.execute('SELECT * FROM lesson_progress')]
        self.retire_fixture_english_course()
        with app.conn() as c:
            learning.seed_courses(c, 1)
            self.assertEqual(dict(c.execute('SELECT * FROM courses WHERE id=?', (cid,)).fetchone()),
                             {**original, 'archived':1})
            self.assertEqual(dict(c.execute('SELECT * FROM courses WHERE id=?', (manual_id,)).fetchone()), manual_before)
            self.assertEqual(dict(c.execute('SELECT * FROM courses WHERE user_id=2 AND curriculum_key=?',
                                           ('language-test-english',)).fetchone()), other_before)
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM lesson_progress')], progress_before)
        visible = {item['id'] for item in self.req('/state')[1]['courses']}
        self.assertNotIn(cid, visible)
        self.assertIn(manual_id, visible)
        self.assertEqual(self.req(f'/courses/{cid}/lessons')[0], 404)
        self.assertIn(other_before['id'], {item['id'] for item in self.req('/state', token=self.other_token)[1]['courses']})

    def test_retired_bundle_startup_is_idempotent_and_does_not_resurrect_archived_rows(self):
        cid = next(item['id'] for item in self.req('/state')[1]['courses'] if item['category']=='english')
        self.req(f'/courses/{cid}/lessons/external-reading', 'PATCH', {'done':True})
        self.retire_fixture_english_course()
        with app.conn() as c:
            learning.init(c)
            original = dict(c.execute('SELECT * FROM courses WHERE id=?', (cid,)).fetchone())
            progress = [tuple(row) for row in c.execute('SELECT * FROM lesson_progress')]
            changes = c.total_changes
            learning.init(c)
            self.assertEqual(c.total_changes, changes)
            self.assertEqual(dict(c.execute('SELECT * FROM courses WHERE id=?', (cid,)).fetchone()), original)
            self.assertEqual([tuple(row) for row in c.execute('SELECT * FROM lesson_progress')], progress)
            self.assertEqual(original['archived'], 1)
            self.assertEqual(c.execute('SELECT count(*) FROM courses WHERE user_id=1 AND curriculum_key=?',
                                       ('language-test-english',)).fetchone()[0], 1)
        self.assertNotIn(cid, {item['id'] for item in self.req('/state')[1]['courses']})

    def test_new_account_does_not_seed_retired_bundle(self):
        retired = self.retire_fixture_english_course()
        status, result = self.req('/register', 'POST', {
            'username':'retirement-fixture-new', 'password':'disposable-test-password', 'name':'Test'}, token=None)
        self.assertEqual(status, 200, result)
        with app.conn() as c:
            uid = c.execute('SELECT id FROM users WHERE username=?', ('retirement-fixture-new',)).fetchone()['id']
            rows = c.execute('SELECT curriculum_key,archived FROM courses WHERE user_id=?', (uid,)).fetchall()
            self.assertTrue(any(row['curriculum_key']=='language-test-korean' for row in rows))
            self.assertFalse(any(row['curriculum_key']==retired['key'] for row in rows))

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
        self.assertEqual(len(courses), 7)
        self.assertEqual(sum(course['category']=='english' for course in courses), 4)
        self.assertEqual(sum(course['category']=='korean' for course in courses), 2)
        self.assertEqual(sum(course['category']=='ai' for course in courses), 1)
        self.assertEqual(len({course['key'] for course in courses}), 7)
        units = [lesson for course in courses for lesson in course.get('lessons', [])]
        self.assertEqual(len(units), 233)
        self.assertEqual(len({lesson['id'] for lesson in units}), 233)
        original = [course for course in courses if course['key'] in ('english-system-b2','english-academic-openlearn')]
        original_units = [lesson for course in original for lesson in course['lessons']]
        self.assertEqual(len(original_units), 49)
        self.assertEqual(len({lesson['url'] for lesson in original_units}), 49)
        self.assertTrue(all(lesson['url'].startswith(('https://learnenglish.britishcouncil.org/', 'https://www.open.edu/')) for lesson in original_units))
        chinese = [course for course in courses if course['category']=='english' and course.get('learning_role') in ('main', 'ielts')]
        self.assertEqual(len(chinese), 1)
        self.assertTrue(all(course['instruction_language']=='中文讲解' for course in chinese))
        self.assertEqual(sorted(len(course['lessons']) for course in chinese), [32])
        for course in chinese:
            metadata = app.course_metadata({'curriculum_key':course['key']})
            self.assertEqual(metadata['video_count'], 32)
            self.assertEqual(metadata['unit_label'], '节')
        sun = chinese[0]
        self.assertEqual(sun['key'], 'english-zh-ielts-sun-32')
        self.assertEqual([lesson['url'] for lesson in sun['lessons']],
                         [f'https://www.bilibili.com/video/BV1TL411k7WK/?p={part}' for part in range(1,33)])
        self.assertEqual(len({lesson['section'] for lesson in sun['lessons']}), 8)
        self.assertTrue(all(lesson['unit_type']=='video' and lesson['format']=='视频' for lesson in sun['lessons']))
        self.assertEqual(sum(lesson['duration_seconds'] for lesson in sun['lessons']), 49749)
        dut = next(course for course in courses if course['key']=='english-zh-ielts-dut')
        self.assertEqual((dut['learning_role'],len(dut['lessons'])), ('reference',33))
        self.assertEqual(app.course_metadata({'curriculum_key':dut['key']})['video_count'], 0)
        self.assertEqual(set(learning.read_json('language_courses.json')['retired_course_keys']),
                         {'english-zh-phonetics-cug','english-zh-grammar-yingyutu','english-system-b1'})
        self.assertTrue(all(course['key'] not in learning.read_json('language_courses.json')['retired_course_keys'] for course in courses))
        topics = [lesson for lesson in units if lesson.get('unit_type') == 'topic']
        self.assertEqual(len(topics), 33)
        self.assertEqual(len({lesson['module_key'] for lesson in topics}), 4)
        self.assertTrue(all('目录课题' in lesson['format'] for lesson in topics))
        guides = learning.read_json('english_lesson_guides.json')['lessons']
        self.assertEqual(set(guides), {lesson['id'] for lesson in original_units})
        self.assertTrue(all(value['zh_title'] and value['zh_guide'] for value in guides.values()))
        ai = next(course for course in courses if course['category']=='ai')
        self.assertEqual(ai['key'], 'ai-intro-xmu-video')
        self.assertEqual(ai['instruction_language'], '中文讲解')
        self.assertEqual(len(ai['lessons']), 119)
        self.assertTrue(all(item['unit_type']=='video' and item['url'].startswith('https://www.bilibili.com/video/') for item in ai['lessons']))
        self.assertEqual(len({item['url'] for item in ai['lessons']}), 119)
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


class LanguageGradingRulesTests(unittest.TestCase):
    def question(self, reference='我来自中国。', *, language='korean', direction='to_zh',
                 source='저는 중국에서 왔어요.', point='은/는'):
        return {'model_answer': reference, 'language': language, 'direction': direction,
                'source_text': source, 'knowledge_point': point}

    def test_screenshot_and_reviewed_chinese_variants_are_correct(self):
        q = self.question()
        for answer in ('我来自中国', '我 来 自 中 国。', '我从中国来', '我是从中国来的'):
            with self.subTest(answer=answer):
                result = learning.grade_answer(q, answer)
                self.assertEqual(result['status'], 'correct')
                self.assertEqual(result['graded_answer'], answer)
                self.assertEqual(result['reference_answer'], q['model_answer'])
        q['knowledge_point'] = 'A different point'
        self.assertEqual(learning.grade_answer(q, '我是从中国来的')['status'], 'needs_review')

    def test_single_country_day_number_and_negation_changes_are_not_accepted(self):
        cases = [(self.question(), '我来自韩国', '国家'),
                 (self.question('我今天上课。'), '我明天上课。', '时间'),
                 (self.question('数值是3.5。'), '数值是35。', '数值'),
                 (self.question('我今天去学校。'), '我今天不去学校。', '否定'),
                 (self.question('I can attend the meeting.', language='english', direction='to_target', source='我可以参加会议。'),
                  'I cannot attend the meeting.', '否定')]
        for q, answer, reason in cases:
            with self.subTest(answer=answer):
                result = learning.grade_answer(q, answer)
                self.assertEqual(result['status'], 'incorrect')
                self.assertTrue(any(reason in text for text in result['feedback']))

    def test_script_and_copied_source_are_diagnosed_without_semantic_guessing(self):
        q = self.question('여기가 교수님 연구실입니까?', direction='to_target', source='这里是教授的研究室吗？', point='입니까?')
        for answer in ('这里是教授的研究室吗？', 'This is the professor office.'):
            with self.subTest(answer=answer):
                self.assertEqual(learning.grade_answer(q, answer)['status'], 'incorrect')
        self.assertEqual(learning.grade_answer(self.question(), '저는 중국에서 왔어요.')['status'], 'incorrect')
        with self.assertRaisesRegex(ValueError, '先写'):
            learning.grade_answer(q, ' \n ')

    def test_internal_punctuation_and_word_boundaries_are_not_erased(self):
        cases = [
            (self.question("We'll meet at 3.5.", language='english', direction='to_target'), 'Well meet at 35.'),
            (self.question('I will re-sign.', language='english', direction='to_target'), 'I will resign.'),
            (self.question('아버지가 방에 들어가요.', direction='to_target'), '아버지 가방에 들어가요.'),
            (self.question('代码是AB。'), '代码是A B。'),
            (self.question('代码是3 5。'), '代码是35。'),
            (self.question('저는 학생이에요.', direction='to_target'), '저는 학생이에요?'),
        ]
        for q, answer in cases:
            with self.subTest(answer=answer):
                self.assertEqual(learning.grade_answer(q, answer)['status'], 'needs_review')

    def test_ambiguous_scope_aliases_and_paraphrases_require_review(self):
        cases = [
            (self.question('The value is 3.5.', language='english', direction='to_target'), 'The value is 3.50.'),
            (self.question('I come from America.', language='english', direction='to_target'), 'I come from United States.'),
            (self.question('I come from Britain.', language='english', direction='to_target'), 'I come from United Kingdom.'),
            (self.question('I can not only read but also write.', language='english', direction='to_target'), 'I cannot only read but also write.'),
            (self.question('교수님은 친절해요.', direction='to_target'), '안 교수님은 친절해요.'),
            (self.question('我不是不想去。'), '我想去。'),
            (self.question(), '中国是我的家乡。'),
            (self.question('三点五。', source='3.5'), '3.5'),
        ]
        for q, answer in cases:
            with self.subTest(answer=answer):
                self.assertEqual(learning.grade_answer(q, answer)['status'], 'needs_review')
        q = self.question('We will meet tomorrow.', language='english', direction='to_target')
        self.assertEqual(learning.grade_answer(q, "We'll meet tomorrow.")['status'], 'correct')

    def test_existing_answer_table_migrates_idempotently_without_changing_original_fields(self):
        c = sqlite3.connect(':memory:')
        c.row_factory = sqlite3.Row
        c.executescript('''
            CREATE TABLE users(id INTEGER PRIMARY KEY);
            CREATE TABLE courses(id INTEGER PRIMARY KEY,user_id INTEGER,category TEXT,curriculum_key TEXT);
            CREATE TABLE language_answers(
                user_id INTEGER, language TEXT, level TEXT, day TEXT, exercise_id TEXT, practice_direction TEXT,
                answer TEXT, completed INTEGER, review INTEGER, updated_at TEXT, question TEXT,
                PRIMARY KEY(user_id,language,level,day,practice_direction,exercise_id));
            INSERT INTO language_answers VALUES(1,'korean','1','2026-11-05','old-question','mixed',
                '旧答案',1,1,'original-timestamp','{"model_answer":"旧参考"}');
        ''')
        before = dict(c.execute('SELECT * FROM language_answers').fetchone())
        learning.init(c)
        after = dict(c.execute('SELECT * FROM language_answers').fetchone())
        self.assertEqual({key: after[key] for key in before}, before)
        self.assertEqual(after['grading'], '')
        learning.init(c)
        self.assertEqual(dict(c.execute('SELECT * FROM language_answers').fetchone()), after)
        self.assertEqual(sum(row['name']=='grading' for row in c.execute('PRAGMA table_info(language_answers)')), 1)
        c.close()


if __name__ == '__main__':
    unittest.main()
