"""Account-wide study-record reset preserves the account and course catalogue."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import unittest
from unittest.mock import patch

import test_task_timing as timing
import test_clear_tasks as task_fixtures

app = timing.app
DAY = '2026-11-16'
RECORD_TABLES = ('tasks', 'lesson_progress', 'rest_days', 'language_answers',
                 'language_daily_sets', 'listening_attempts', 'listening_reveals')


class ClearStudyTests(unittest.TestCase):
    setUp = timing.TaskTimingTests.setUp
    tearDown = timing.TaskTimingTests.tearDown
    req = timing.TaskTimingTests.req
    at = timing.TaskTimingTests.at
    create = task_fixtures.ClearTaskTests.create
    task_rows = task_fixtures.ClearTaskTests.task_rows
    table_rows = task_fixtures.ClearTaskTests.table_rows
    mixed_tasks = task_fixtures.ClearTaskTests.mixed_tasks

    def seed_records(self):
        task_fixtures.ClearTaskTests.seed_protected_records(self)
        with app.conn() as c:
            c.execute('CREATE TABLE password_reset_redemptions(token_hash TEXT PRIMARY KEY,user_id INTEGER,used_at TEXT)')
            for uid in (1, 2):
                c.execute('INSERT INTO password_reset_redemptions VALUES(?,?,?)',
                          ('synthetic-used-token-' + str(uid), uid, '2026-11-16T00:00:00Z'))
                c.execute("UPDATE users SET name=?,password='synthetic-hash',salt='synthetic-salt',avatar='synthetic-avatar' WHERE id=?",
                          ('Persistent profile ' + str(uid), uid))
                c.execute('INSERT INTO language_daily_sets(user_id,language,level,day,direction,questions) VALUES(?,?,?,?,?,?)',
                          (uid, 'english', 'B2', DAY, 'mixed', '[{"id":"protected-english-question"}]'))
                c.execute('INSERT INTO language_answers(user_id,language,level,day,exercise_id,practice_direction,answer,completed,review,updated_at,question) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (uid, 'english', 'B2', DAY, 'protected-english-question', 'mixed', 'Keep English answer', 1, 1,
                           '2026-11-16T01:00:00Z', '{}'))
                c.execute('INSERT INTO listening_attempts(user_id,language,level,exercise_id,revision,snapshot,answers,score,total,submitted_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (uid, 'english', 'B2', 'protected-english-listening', 'revision', '{}', '[0,1,2]', 2, 3,
                           '2026-11-16T01:00:00Z'))
                c.execute('INSERT INTO listening_reveals VALUES(?,?,?,?)',
                          (uid, 'protected-english-listening', 'revision', '2026-11-16T01:01:00Z'))
            for row in c.execute('SELECT rowid,answer FROM language_answers').fetchall():
                c.execute('UPDATE language_answers SET grading=?,manual_review=? WHERE rowid=?',
                          (json.dumps({'status': 'needs_review', 'graded_answer': row['answer']}),
                           json.dumps({'status': 'correct', 'reviewed_answer': row['answer']}), row['rowid']))

    def records(self, uid=1):
        with app.conn() as c:
            return {table: [tuple(row) for row in c.execute('SELECT * FROM ' + table + ' WHERE user_id=? ORDER BY rowid',
                                                           (uid,))] for table in RECORD_TABLES}

    def preview(self, token='owner'):
        status, result = self.req('/study/clear-preview', token=token)
        self.assertEqual(status, 200, result)
        self.assertEqual(set(result), {'total', 'tasks', 'lesson_progress', 'rest_days', 'language_answers',
                                      'language_daily_sets', 'listening_attempts', 'listening_reveals', 'snapshot'})
        tasks = result['tasks']
        self.assertEqual(set(tasks), {'total', 'unfinished', 'completed', 'archived', 'running'})
        self.assertEqual(tasks['total'], tasks['unfinished'] + tasks['completed'] + tasks['archived'])
        self.assertEqual(result['total'], tasks['total'] + sum(result[table] for table in RECORD_TABLES if table != 'tasks'))
        self.assertRegex(result['snapshot'], r'^[0-9a-f]{64}$')
        return result

    def clear(self, preview=None, token='owner'):
        preview = preview if preview is not None else self.preview(token)
        return self.req('/study', 'DELETE', {'confirmation': '清空全站学习记录', 'snapshot': preview['snapshot']}, token=token)

    def test_preview_covers_both_languages_all_dates_states_and_is_read_only(self):
        self.mixed_tasks()
        self.seed_records()
        before = self.table_rows()
        preview = self.preview()
        self.assertEqual(preview['tasks'], {'total': 6, 'unfinished': 3, 'completed': 1, 'archived': 2, 'running': 1})
        self.assertEqual({table: preview[table] for table in RECORD_TABLES if table != 'tasks'},
                         {'lesson_progress': 1, 'rest_days': 1, 'language_answers': 2,
                          'language_daily_sets': 2, 'listening_attempts': 2, 'listening_reveals': 2})
        self.assertEqual(preview['total'], 16)
        with self.at('2030-01-01T00:00:00Z') as clock:
            self.assertEqual(self.preview(), preview)
            self.assertEqual(clock.call_count, 0)
        with app.conn() as c:
            changes = c.total_changes
            self.assertEqual(app.study_clear_preview(c, 1), preview)
            self.assertEqual(c.total_changes, changes)
        self.assertEqual(self.table_rows(), before)

    def test_reset_clears_all_seven_owner_tables_and_keeps_all_preserved_and_other_owner_rows(self):
        self.mixed_tasks()
        self.create(token=self.other_token)
        self.seed_records()
        preserved = self.table_rows(exclude=(*RECORD_TABLES, 'task_id_state'))
        other = self.records(2)
        status, result = self.clear()
        self.assertEqual(status, 200, result)
        self.assertEqual(result, {'ok': True, 'deleted': 16, 'deleted_by_table':
                                 {'tasks': 6, 'lesson_progress': 1, 'rest_days': 1, 'language_answers': 2,
                                  'language_daily_sets': 2, 'listening_attempts': 2, 'listening_reveals': 2}})
        self.assertEqual(self.records(), {table: [] for table in RECORD_TABLES})
        self.assertEqual(self.records(2), other)
        self.assertEqual(self.table_rows(exclude=(*RECORD_TABLES, 'task_id_state')), preserved)
        self.assertEqual(self.req('/state')[1]['rest_days'], [])
        self.assertEqual(self.req('/state')[0], 200, 'The active session must remain usable')

    def test_zero_tasks_still_allows_resetting_language_and_course_progress(self):
        self.seed_records()
        preview = self.preview()
        self.assertEqual(preview['tasks']['total'], 0)
        self.assertEqual(preview['total'], 10)
        self.assertEqual(self.clear(preview)[1]['deleted'], 10)
        self.assertEqual(self.preview()['total'], 0)

    def test_authentication_exact_phrase_snapshot_and_body_are_required(self):
        self.create()
        self.seed_records()
        old = self.preview()
        valid = {'confirmation': '清空全站学习记录', 'snapshot': old['snapshot']}
        before = self.table_rows()
        self.assertEqual(self.req('/study/clear-preview', token=None)[0], 401)
        self.assertEqual(self.req('/study', 'DELETE', valid, token=None)[0], 401)
        for method in ('POST', 'PATCH', 'DELETE'):
            self.assertEqual(self.req('/study/clear-preview', method, valid)[0], 405)
        for method in ('GET', 'POST', 'PATCH'):
            self.assertEqual(self.req('/study', method, valid)[0], 405)
        for payload in (None, [], False, {}, {'snapshot': old['snapshot']},
                        {'confirmation': '清空全站学习记录'}, {**valid, 'confirmation': '清空全部任务'},
                        {**valid, 'confirmation': '清空全站学习记录 '}, {**valid, 'snapshot': 1},
                        {**valid, 'snapshot': 'invalid'}, {**valid, 'user_id': 2}, {**valid, 'scope': 'all'}):
            with self.subTest(payload=payload):
                self.assertEqual(self.req('/study', 'DELETE', payload)[0], 400)
                self.assertEqual(self.table_rows(), before)
        self.assertEqual(self.req('/study', 'DELETE', {**valid, 'snapshot': '0' * 64})[0], 409)
        self.assertEqual(self.table_rows(), before)

    def test_snapshots_bind_owner_and_are_distinct_from_task_only_scope(self):
        owner, other = self.preview(), self.preview(self.other_token)
        self.assertNotEqual(owner['snapshot'], other['snapshot'])
        self.assertEqual(self.clear(owner, token=self.other_token)[0], 409)
        task_only = self.req('/tasks/clear-preview')[1]
        self.assertNotEqual(owner['snapshot'], task_only['snapshot'])
        self.assertEqual(self.clear(task_only)[0], 409)

    def test_any_affected_table_content_edit_expires_snapshot_even_without_count_changes(self):
        self.create()
        self.seed_records()
        edits = {'tasks': ('note', 'Edited task note'),
                 'lesson_progress': ('completed_at', 999.0),
                 'rest_days': ('created_at', '2026-11-16T03:00:00Z'),
                 'language_answers': ('answer', 'Edited answer'),
                 'language_daily_sets': ('rotation', 9),
                 'listening_attempts': ('score', 3),
                 'listening_reveals': ('revealed_at', '2026-11-16T04:00:00Z')}
        for table, (field, value) in edits.items():
            with self.subTest(table=table):
                old = self.preview()
                with app.conn() as c:
                    c.execute('UPDATE ' + table + ' SET ' + field + '=? WHERE user_id=1', (value,))
                before = self.table_rows()
                status, result = self.clear(old)
                self.assertEqual(status, 409, result)
                self.assertIn('重新核对', result['error'])
                self.assertEqual(self.table_rows(), before)

    def test_new_record_from_another_device_requires_a_new_preview(self):
        self.seed_records()
        old = self.preview()
        self.create(day='2028-01-01')
        before = self.table_rows()
        self.assertEqual(self.clear(old)[0], 409)
        self.assertEqual(self.table_rows(), before)

    def test_preserved_account_catalogue_preferences_and_other_owner_edits_do_not_expire_preview(self):
        self.create()
        self.seed_records()
        old = self.preview()
        self.create(token=self.other_token)
        with app.conn() as c:
            c.execute("UPDATE users SET name='New profile',minutes=240,avatar='New avatar' WHERE id=1")
            c.execute("UPDATE courses SET description='New course content',archived=1 WHERE user_id=1")
            c.execute("UPDATE language_settings SET level='2',direction='to_target' WHERE user_id=1")
        preserved = self.table_rows(exclude=(*RECORD_TABLES, 'task_id_state'))
        other = self.records(2)
        self.assertEqual(self.preview()['snapshot'], old['snapshot'])
        self.assertEqual(self.clear(old)[0], 200)
        self.assertEqual(self.table_rows(exclude=(*RECORD_TABLES, 'task_id_state')), preserved)
        self.assertEqual(self.records(2), other)

    def test_failure_after_task_and_progress_deletes_rolls_back_every_table_and_watermark(self):
        self.mixed_tasks()
        self.seed_records()
        old = self.preview()
        with app.conn() as c:
            c.execute("CREATE TRIGGER reset_failure BEFORE DELETE ON language_answers WHEN OLD.user_id=1 BEGIN SELECT RAISE(ABORT,'Synthetic reset failure'); END")
        before = self.table_rows()
        self.assertEqual(self.clear(old)[0], 500)
        self.assertEqual(self.table_rows(), before)

    def test_waiting_reset_rechecks_committed_language_edit_before_deleting(self):
        self.seed_records()
        old = self.preview()
        entered = threading.Event()
        original = app.clear_study

        def observe(c, uid, data):
            entered.set()
            return original(c, uid, data)

        with app.conn() as writer, patch.object(app, 'clear_study', side_effect=observe):
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE language_answers SET answer='Other device committed answer' WHERE user_id=1")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.clear, old)
                self.assertTrue(entered.wait(2))
                writer.commit()
                status, result = pending.result(timeout=5)
        self.assertEqual(status, 409, result)
        self.assertEqual(len(self.records()['language_answers']), 2)

    def test_task_only_api_still_preserves_other_learning_records(self):
        self.create()
        self.seed_records()
        old = self.req('/tasks/clear-preview')[1]
        protected = self.records()
        status, result = self.req('/tasks', 'DELETE', {'confirmation': '清空全部任务', 'snapshot': old['snapshot']})
        self.assertEqual((status, result), (200, {'ok': True, 'deleted': 1}))
        self.assertEqual({table: rows for table, rows in self.records().items() if table != 'tasks'},
                         {table: rows for table, rows in protected.items() if table != 'tasks'})
        self.assertEqual(self.preview()['total'], 10)

    def test_empty_repeat_reset_and_restart_keep_no_records_and_preserve_preferences(self):
        self.create()
        self.seed_records()
        app.init()
        preserved = self.table_rows(exclude=(*RECORD_TABLES, 'task_id_state'))
        self.assertEqual(self.clear()[0], 200)
        empty = self.preview()
        self.assertEqual(empty['total'], 0)
        for _ in range(2):
            status, result = self.clear(empty)
            self.assertEqual(status, 200, result)
            self.assertEqual(result['deleted'], 0)
            self.assertEqual(result['deleted_by_table'], {table: 0 for table in RECORD_TABLES})
        app.init()
        self.assertEqual(self.preview()['total'], 0)
        self.assertEqual(self.table_rows(exclude=(*RECORD_TABLES, 'task_id_state')), preserved)

    def test_full_reset_retires_task_ids_and_new_tasks_and_practice_work(self):
        old_ids = [self.create(), self.create()]
        self.seed_records()
        self.assertEqual(self.clear()[0], 200)
        new_id = self.create(planned=None)
        self.assertGreater(new_id, max(old_ids))
        new_task = self.task_rows(1)
        for old in old_ids:
            self.assertEqual(self.req(f'/tasks/{old}', 'PATCH', {'done': True})[0], 404)
        self.assertEqual(self.task_rows(1), new_task)
        for path in ('/language-practice?language=korean&mode=history', '/listening?language=korean&mode=history'):
            status, result = self.req(path)
            self.assertEqual(status, 200, result)
            self.assertEqual(result['items'], [])
        status, daily = self.req('/language-practice?language=korean&level=1&day=' + DAY + '&direction=mixed')
        self.assertEqual(status, 200, daily)
        self.assertTrue(daily['items'])
        self.assertTrue(all(not item['answer'] and not item['completed'] and not item['review']
                            and not item.get('manual_review') for item in daily['items']))
        status, listening = self.req('/listening?language=korean&level=1')
        self.assertEqual(status, 200, listening)
        self.assertIsNone(listening['saved'])
        exercise = listening['exercise']
        status, submitted = self.req('/listening', 'POST', {'exercise_id': exercise['id'],
                                     'revision': exercise['revision'],
                                     'answers': {question['id']: 0 for question in exercise['questions']}})
        self.assertEqual(status, 200, submitted)
        self.assertEqual(self.req('/listening?language=korean&mode=history')[1]['total'], 1)


if __name__ == '__main__':
    unittest.main()
