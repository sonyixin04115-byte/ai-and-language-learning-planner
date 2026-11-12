"""Optional estimates and clocks use disposable schemas and real HTTP handlers."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import test_task_timing as timing

app = timing.app
DAY = '2026-11-01'


class OptionalTaskTimingTests(unittest.TestCase):
    setUp = timing.TaskTimingTests.setUp
    tearDown = timing.TaskTimingTests.tearDown
    req = timing.TaskTimingTests.req
    task = timing.TaskTimingTests.task
    at = timing.TaskTimingTests.at

    def create(self, **fields):
        before = {row['id'] for row in self.req('/state')[1]['tasks']}
        status, result = self.req('/tasks', 'POST', {'title': '自主安排', 'day': DAY, **fields})
        self.assertEqual(status, 201, result)
        created = [row['id'] for row in self.req('/state')[1]['tasks'] if row['id'] not in before]
        self.assertEqual(len(created), 1)
        return created[0]

    def raw(self, tid):
        with app.conn() as c:
            return dict(c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone())

    def assert_clock_empty(self, tid):
        row = self.task(tid)
        self.assertEqual((row['started_at'], row['finished_at'], row['actual'], row['pause_intervals']),
                         (None, None, 0, []))
        return row

    def test_estimates_can_be_omitted_null_or_blank_for_timed_and_untimed_tasks(self):
        for enabled in (False, True):
            for estimate in ({}, {'planned': None}, {'planned': ''}, {'planned': '  '}):
                with self.subTest(enabled=enabled, estimate=estimate):
                    tid = self.create(timing_enabled=enabled, **estimate)
                    row = self.assert_clock_empty(tid)
                    self.assertIsNone(row['planned'])
                    self.assertIs(row['timing_enabled'], enabled)
        legacy = self.create(planned=30)
        self.assertIs(self.task(legacy)['timing_enabled'], True, 'An omitted flag keeps old API behavior')

    def test_estimate_validation_is_strict_and_failed_create_or_patch_is_atomic(self):
        tid = self.create(timing_enabled=False)
        for value in (True, False, 0, -1, 601, 1.5, 30.0, '3.5', '1e2', '0', '601', [], {}):
            with self.subTest(value=value):
                before = self.req('/state')[1]['tasks']
                self.assertEqual(self.req('/tasks', 'POST',
                    {'title': '不能写入', 'day': DAY, 'timing_enabled': False, 'planned': value})[0], 400)
                self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'planned': value})[0], 400)
                self.assertEqual(self.req('/state')[1]['tasks'], before)
        for value, expected in ((1, 1), (600, 600), ('30', 30), (' 45 ', 45), (None, None), ('', None)):
            self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'planned': value})[0], 200)
            self.assertEqual(self.task(tid)['planned'], expected)
        self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'title': '只改标题'})[0], 200)
        self.assertIsNone(self.task(tid)['planned'])

    def test_untimed_todo_can_be_edited_completed_and_reopened_without_clock_records(self):
        tid = self.create(timing_enabled=False, done=True, note='自己的规划')
        for done in (False, True, False):
            self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH',
                                     {'done': done, 'title': '读资料', 'note': '只完成待办'})[0], 200)
            row = self.assert_clock_empty(tid)
            self.assertEqual(row['done'], int(done))
            self.assertIs(row['timing_enabled'], False)
            self.assertIsNone(row['planned'])
        app.init()
        self.assertIs(self.assert_clock_empty(tid)['timing_enabled'], False)

    def test_untimed_todo_rejects_timer_actions_and_injected_time_without_mutation(self):
        tid = self.create(timing_enabled=False)
        before = self.raw(tid)
        invalid = [{'action': action} for action in ('start', 'pause', 'resume')]
        invalid += [{'started_at': '2026-11-01T01:00:00Z'}, {'finished_at': '2026-11-01T02:00:00Z'},
                    {'actual': 1}, {'actual': '2'}, {'actual': True}, {'pause_history': '[]'},
                    {'pause_intervals': []}, {'paused_at': None}]
        for fields in invalid:
            with self.subTest(fields=fields):
                self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', fields)[0], 400)
                self.assertEqual(self.raw(tid), before)
        for fields in ({'started_at': '2026-11-01T01:00:00Z'}, {'actual': 1}, {'pause_intervals': []}):
            self.assertEqual(self.req('/tasks', 'POST',
                {'title': '无效普通待办', 'day': DAY, 'timing_enabled': False, **fields})[0], 400)
        self.assertEqual(len(self.req('/state')[1]['tasks']), 1)
        self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH',
                                 {'started_at': None, 'finished_at': '', 'actual': 0})[0], 200)
        self.assert_clock_empty(tid)

    def test_timer_flag_rejects_nonboolean_inputs_and_mixed_timer_actions(self):
        tid = self.create(timing_enabled=False)
        for value in (None, 0, 1, '', 'false', [], {}):
            with self.subTest(value=value):
                before = self.raw(tid)
                self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'timing_enabled': value})[0], 400)
                self.assertEqual(self.req('/tasks', 'POST',
                    {'title': '错误开关', 'day': DAY, 'timing_enabled': value})[0], 400)
                self.assertEqual(self.raw(tid), before)
        self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH',
                                 {'action': 'start', 'timing_enabled': True})[0], 400)
        self.assertIs(self.task(tid)['timing_enabled'], False)

    def test_enabling_a_clock_needs_no_estimate_and_pause_time_is_excluded(self):
        tid = self.create(timing_enabled=False)
        self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'timing_enabled': True})[0], 200)
        self.assertIsNone(self.task(tid)['planned'])
        for moment, action in [('01:00:00', 'start'), ('01:01:00', 'pause'), ('01:05:00', 'resume')]:
            with self.at('2026-11-01T' + moment + '+00:00'):
                self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'action': action})[0], 200)
        with self.at('2026-11-01T01:06:00+00:00'):
            self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'done': True})[0], 200)
        row = self.task(tid)
        self.assertEqual(row['actual'], 2)
        self.assertIs(row['timing_enabled'], True)
        self.assertIsNone(row['planned'])
        self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'done': False})[0], 200)
        self.assertEqual(self.task(tid)['actual'], 2)

    def test_clock_records_require_explicit_reset_before_disabling(self):
        tid = self.create(note='保留个人笔记')
        with self.at('2026-11-01T01:00:00+00:00'):
            self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'action': 'start'})[0], 200)
        before = self.raw(tid)
        for fields in ({'timing_enabled': False},
                       {'timing_enabled': False, 'started_at': None, 'finished_at': None, 'actual': 0}):
            status, result = self.req(f'/tasks/{tid}', 'PATCH', fields)
            self.assertEqual(status, 400, result)
            self.assertIn('清零', result['error'])
            self.assertEqual(self.raw(tid), before)
        self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'action': 'reset'})[0], 200)
        self.assertIs(self.assert_clock_empty(tid)['timing_enabled'], True)
        self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'timing_enabled': False})[0], 200)
        row = self.assert_clock_empty(tid)
        self.assertIs(row['timing_enabled'], False)
        self.assertEqual(row['note'], '保留个人笔记')
        self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'action': 'reset'})[0], 200)
        self.assertIs(self.task(tid)['timing_enabled'], False, 'Reset must not enable a disabled clock')

    def test_legacy_minutes_zero_duration_and_pause_only_traces_cannot_be_erased_by_disabling(self):
        traces = [{'actual': 15},
                  {'started_at': '2026-11-01T01:00:00Z', 'finished_at': '2026-11-01T01:00:00Z'},
                  {'pause_history': json.dumps([{'started_at': '2026-11-01T01:00:00Z',
                                                'finished_at': '2026-11-01T01:01:00Z'}])},
                  {'pause_history': 'invalid-legacy-clock'}]
        for fields in traces:
            with self.subTest(fields=fields):
                tid = self.create()
                with app.conn() as c:
                    c.execute('UPDATE tasks SET ' + ','.join(key + '=?' for key in fields) + ' WHERE id=?',
                              (*fields.values(), tid))
                before = self.raw(tid)
                status, result = self.req(f'/tasks/{tid}', 'PATCH', {'timing_enabled': False})
                self.assertEqual(status, 400, result)
                self.assertIn('清零', result['error'])
                self.assertEqual(self.raw(tid), before)
                self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'action': 'reset'})[0], 200)
                self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'timing_enabled': False})[0], 200)
                self.assert_clock_empty(tid)

    def test_clean_clock_can_be_disabled_without_erasing_planning_or_completion(self):
        tid = self.create(planned=45, done=True, note='保留')
        self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'timing_enabled': False})[0], 200)
        row = self.assert_clock_empty(tid)
        self.assertEqual((row['planned'], row['done'], row['note']), (45, 1, '保留'))
        self.assertIs(row['timing_enabled'], False)

    def test_untimed_tasks_survive_rest_day_and_future_reschedule_without_clock_changes(self):
        todo = self.create(timing_enabled=False)
        running = self.create(started_at='2026-11-01T01:00:00Z')
        before = self.raw(todo)
        with self.at('2026-11-01T01:10:00+00:00'):
            status, result = self.req('/days/rest', 'POST', {'day': DAY, 'rest': True})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['paused_tasks'], 1)
        self.assertEqual(self.raw(todo), before)
        self.assertEqual(len(self.task(running)['pause_intervals']), 1)
        target = '2026-12-16'
        self.assertEqual(self.req('/days/rest', 'POST', {'day': target, 'rest': True})[0], 200)
        status, result = self.req(f'/tasks/{todo}', 'PATCH', {'action': 'reschedule', 'day': target})
        self.assertEqual(status, 200, result)
        self.assertFalse(result['paused_on_move'])
        moved = self.raw(todo)
        self.assertEqual({key: value for key, value in moved.items() if key != 'day'},
                         {key: value for key, value in before.items() if key != 'day'})
        self.assertEqual(moved['day'], target)
        self.assertIs(self.assert_clock_empty(todo)['timing_enabled'], False)

    def test_owner_isolation_covers_switch_reset_completion_and_reschedule(self):
        tid = self.create(timing_enabled=False)
        before = self.raw(tid)
        for fields in ({'timing_enabled': True}, {'planned': 20}, {'done': True}, {'action': 'reset'},
                       {'action': 'reschedule', 'day': '2026-11-10'}):
            with self.subTest(fields=fields):
                self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', fields, token=self.other_token)[0], 404)
                self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', fields, token=None)[0], 401)
        self.assertEqual(self.req('/state', token=self.other_token)[1]['tasks'], [])
        self.assertEqual(self.raw(tid), before)

    def test_disabling_a_legacy_template_clock_counts_as_a_personal_change(self):
        title = app.WEEKS[0][1][0]
        kept = self.create(title=title, category='学习', planned=72, timing_enabled=False)
        untouched = self.create(title=title, category='学习', planned=72)
        with app.conn() as c:
            c.execute("UPDATE tasks SET created_source='template' WHERE id IN (?,?)", (kept, untouched))
        before = self.raw(kept)
        status, result = self.req('/tasks/archive-template', 'POST')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['archived'], 1)
        self.assertEqual(self.raw(kept), before)
        self.assertEqual(self.raw(untouched)['archived'], 1)


class OptionalTaskMigrationTests(unittest.TestCase):
    def test_older_task_schema_adds_enabled_flag_without_rewriting_legacy_rows(self):
        for has_clock in (False, True):
            with self.subTest(has_clock=has_clock), tempfile.TemporaryDirectory() as temp:
                database = Path(temp) / 'legacy-schema.db'
                with closing(sqlite3.connect(database)) as c, c:
                    c.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,name TEXT,password TEXT,salt TEXT,minutes INTEGER DEFAULT 180,start TEXT)')
                    c.execute("INSERT INTO users(id,username,name,start) VALUES(1,'legacy-one','旧用户','2026-11-01'),(2,'legacy-two','另一用户','2026-11-01')")
                    extra = ",archived INTEGER DEFAULT 0,created_source TEXT DEFAULT 'manual',started_at TEXT,finished_at TEXT,pause_history TEXT NOT NULL DEFAULT '[]'" if has_clock else ''
                    c.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY,user_id INTEGER,day TEXT,title TEXT,category TEXT,planned INTEGER,actual INTEGER DEFAULT 0,done INTEGER DEFAULT 0,note TEXT DEFAULT ''" + extra + ')')
                    c.execute("INSERT INTO tasks(id,user_id,day,title,category,planned,actual,done,note) VALUES(11,1,'2026-11-01','旧任务','学习',NULL,35,1,'旧笔记'),(12,2,'2026-11-02','别人的旧任务','生活',30,0,0,'')")
                    if has_clock:
                        c.execute("UPDATE tasks SET started_at='2020-01-01T01:00:00Z',pause_history='[]',archived=1 WHERE id=11")
                    c.row_factory = sqlite3.Row
                    original = [dict(row) for row in c.execute('SELECT * FROM tasks ORDER BY id')]
                with patch.object(app, 'DB', database):
                    app.init()
                    app.init()
                    with app.conn() as c:
                        columns = {row['name']: dict(row) for row in c.execute('PRAGMA table_info(tasks)')}
                        migrated = [dict(row) for row in c.execute('SELECT * FROM tasks ORDER BY id')]
                self.assertEqual(len(migrated), len(original))
                self.assertEqual((columns['timing_enabled']['notnull'], str(columns['timing_enabled']['dflt_value'])), (1, '1'))
                for before, after in zip(original, migrated):
                    self.assertEqual({key: after[key] for key in before}, before)
                    self.assertEqual(after['timing_enabled'], 1)
                    self.assertIn('pause_history', after)


if __name__ == '__main__':
    unittest.main()
