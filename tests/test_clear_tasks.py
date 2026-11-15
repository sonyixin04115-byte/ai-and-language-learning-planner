"""Explicit, account-scoped clearing with snapshot and stale-task-ID protection."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import test_task_timing as timing

app = timing.app
DAY = '2026-11-16'


class ClearTaskTests(unittest.TestCase):
    setUp = timing.TaskTimingTests.setUp
    tearDown = timing.TaskTimingTests.tearDown
    req = timing.TaskTimingTests.req
    at = timing.TaskTimingTests.at

    def create(self, token='owner', **fields):
        before = {row['id'] for row in self.req('/state', token=token)[1]['tasks']}
        status, result = self.req('/tasks', 'POST', {'title': 'Synthetic clear fixture', 'day': DAY,
                                                   'timing_enabled': False, **fields}, token=token)
        self.assertEqual(status, 201, result)
        new = [row['id'] for row in self.req('/state', token=token)[1]['tasks'] if row['id'] not in before]
        self.assertEqual(len(new), 1)
        return new[0]

    def preview(self, token='owner'):
        status, result = self.req('/tasks/clear-preview', token=token)
        self.assertEqual(status, 200, result)
        self.assertEqual(set(result), {'total', 'unfinished', 'completed', 'archived', 'running', 'snapshot'})
        self.assertEqual(result['total'], result['unfinished'] + result['completed'] + result['archived'])
        self.assertRegex(result['snapshot'], r'^[0-9a-f]{64}$')
        return result

    def clear(self, preview=None, token='owner'):
        preview = preview if preview is not None else self.preview(token)
        return self.req('/tasks', 'DELETE', {'confirmation': '清空全部任务',
                                          'snapshot': preview['snapshot']}, token=token)

    def task_rows(self, uid=None):
        with app.conn() as c:
            if uid is None:
                return [dict(row) for row in c.execute('SELECT * FROM tasks ORDER BY id')]
            return [dict(row) for row in c.execute('SELECT * FROM tasks WHERE user_id=? ORDER BY id', (uid,))]

    def table_rows(self, exclude=()):
        with app.conn() as c:
            names = [row['name'] for row in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {name: [tuple(row) for row in c.execute('SELECT * FROM "' + name + '" ORDER BY rowid')]
                    for name in names if name not in exclude}

    def seed_protected_records(self):
        with app.conn() as c:
            for uid in (1, 2):
                cid = 200 + uid
                c.execute('INSERT INTO courses(id,user_id,title,description) VALUES(?,?,?,?)',
                          (cid, uid, 'Protected course', 'Protected course notes'))
                c.execute('INSERT INTO lesson_progress VALUES(?,?,?,?)', (uid, cid, 'kept-video', 123456.0))
                c.execute('INSERT INTO language_settings VALUES(?,?,?,?)', (uid, 'korean', '1', 'mixed'))
                c.execute('INSERT INTO language_daily_sets(user_id,language,level,day,direction,questions) VALUES(?,?,?,?,?,?)',
                          (uid, 'korean', '1', DAY, 'mixed', '[{"id":"protected-question"}]'))
                c.execute('INSERT INTO language_answers(user_id,language,level,day,exercise_id,practice_direction,answer,completed,review,updated_at,question) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (uid, 'korean', '1', DAY, 'protected-question', 'mixed', 'Keep this answer', 1, 1,
                           '2026-11-16T01:00:00Z', '{}'))
                c.execute('INSERT INTO listening_attempts(user_id,language,level,exercise_id,revision,snapshot,answers,score,total,submitted_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (uid, 'korean', '1', 'protected-listening', 'revision', '{}', '[0,1,2]', 2, 3,
                           '2026-11-16T01:00:00Z'))
                c.execute('INSERT INTO listening_reveals VALUES(?,?,?,?)',
                          (uid, 'protected-listening', 'revision', '2026-11-16T01:01:00Z'))
                c.execute('INSERT INTO rest_days VALUES(?,?,?)', (uid, DAY, '2026-11-16T01:00:00Z'))
                c.execute('INSERT INTO course_defaults VALUES(?)', (uid,))

    def mixed_tasks(self):
        plain = self.create(day='2020-01-01', planned=None)
        completed = self.create(day='2027-01-01', done=True)
        running = self.create(timing_enabled=True, started_at='2026-11-16T01:00:00Z')
        paused = self.create(timing_enabled=True, started_at='2026-11-16T01:00:00Z')
        with self.at('2026-11-16T01:02:00Z'):
            self.assertEqual(self.req(f'/tasks/{paused}', 'PATCH', {'action': 'pause'})[0], 200)
        archived = self.create(day='2019-01-01')
        archived_done = self.create(timing_enabled=True, actual=12, done=True)
        with app.conn() as c:
            c.execute('UPDATE tasks SET archived=1 WHERE id IN (?,?)', (archived, archived_done))
        return [plain, completed, running, paused, archived, archived_done]

    def test_preview_is_read_only_and_counts_all_dates_with_disjoint_archive_totals(self):
        self.mixed_tasks()
        self.create(token=self.other_token)
        before = self.table_rows()
        first = self.preview()
        self.assertEqual({key: value for key, value in first.items() if key != 'snapshot'},
                         {'total': 6, 'unfinished': 3, 'completed': 1, 'archived': 2, 'running': 1})
        with self.at('2030-01-01T00:00:00Z') as clock:
            self.assertEqual(self.preview(), first)
            self.assertEqual(clock.call_count, 0, 'Persisted content, not elapsed wall time, defines the snapshot')
        self.assertEqual(self.table_rows(), before)
        with app.conn() as c:
            changes = c.total_changes
            self.assertEqual(app.task_clear_preview(c, 1), first)
            self.assertEqual(c.total_changes, changes)

    def test_clear_removes_only_all_owned_tasks_and_preserves_every_other_table(self):
        self.mixed_tasks()
        self.create(token=self.other_token, title='Other account task')
        self.seed_protected_records()
        protected = self.table_rows(exclude=('tasks', 'task_id_state'))
        other_tasks = self.task_rows(2)
        status, result = self.clear()
        self.assertEqual((status, result), (200, {'ok': True, 'deleted': 6}))
        self.assertEqual(self.task_rows(1), [])
        self.assertEqual(self.task_rows(2), other_tasks)
        self.assertEqual(self.table_rows(exclude=('tasks', 'task_id_state')), protected)
        self.assertEqual(self.req('/state')[1]['rest_days'], [DAY])
        self.assertEqual(self.req('/state')[1]['archived_task_count'], 0)

    def test_authentication_methods_and_exact_confirmation_are_required(self):
        self.create()
        preview = self.preview()
        before = self.table_rows()
        valid = {'confirmation': '清空全部任务', 'snapshot': preview['snapshot']}
        self.assertEqual(self.req('/tasks/clear-preview', token=None)[0], 401)
        self.assertEqual(self.req('/tasks', 'DELETE', valid, token=None)[0], 401)
        for method in ('POST', 'PATCH', 'DELETE'):
            self.assertEqual(self.req('/tasks/clear-preview', method, valid)[0], 405)
        invalid = [None, [], '清空全部任务', False, {}, {'confirmation': '清空全部任务'},
                   {'snapshot': preview['snapshot']}, {**valid, 'confirmation': '清空'},
                   {**valid, 'confirmation': ' 清空全部任务'}, {**valid, 'snapshot': None},
                   {**valid, 'snapshot': 'invalid'}, {**valid, 'snapshot': 'z' * 64},
                   {**valid, 'user_id': 2}, {**valid, 'day': DAY}]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.req('/tasks', 'DELETE', payload)[0], 400)
                self.assertEqual(self.table_rows(), before)
        self.assertEqual(self.req('/tasks', 'DELETE', {**valid, 'snapshot': '0' * 64})[0], 409)
        self.assertEqual(self.table_rows(), before)

    def test_snapshot_is_owner_bound_even_when_both_accounts_have_no_tasks(self):
        first, second = self.preview(), self.preview(self.other_token)
        self.assertNotEqual(first['snapshot'], second['snapshot'])
        self.assertEqual(self.clear(first, token=self.other_token)[0], 409)
        self.assertEqual(self.task_rows(), [])

    def test_task_edits_and_new_tasks_expire_old_snapshot_without_deletion(self):
        tid = self.create()
        for fields in ({'title': 'Edited elsewhere'}, {'note': 'New notes'}, {'planned': 25},
                       {'done': True}, {'done': False}, {'timing_enabled': True}):
            with self.subTest(fields=fields):
                old = self.preview()
                self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', fields)[0], 200)
                before = self.task_rows()
                status, result = self.clear(old)
                self.assertEqual(status, 409, result)
                self.assertIn('重新核对', result['error'])
                self.assertEqual(self.task_rows(), before)
        old = self.preview()
        self.create(day='2028-01-01')
        before = self.task_rows()
        self.assertEqual(self.clear(old)[0], 409)
        self.assertEqual(self.task_rows(), before)

    def test_clock_transitions_archive_and_single_delete_change_snapshot(self):
        tid = self.create(timing_enabled=True)
        for at, action in [('01:00:00', 'start'), ('01:02:00', 'pause'), ('01:03:00', 'resume')]:
            old = self.preview()
            with self.at('2026-11-16T' + at + 'Z'):
                self.assertEqual(self.req(f'/tasks/{tid}', 'PATCH', {'action': action})[0], 200)
            self.assertEqual(self.clear(old)[0], 409)
        old = self.preview()
        with app.conn() as c:
            c.execute('UPDATE tasks SET archived=1 WHERE id=?', (tid,))
        self.assertEqual(self.clear(old)[0], 409)
        other = self.create()
        old = self.preview()
        self.assertEqual(self.req(f'/tasks/{other}', 'DELETE')[0], 200)
        self.assertEqual(self.clear(old)[0], 409)
        self.assertEqual(len(self.task_rows(1)), 1)

    def test_unrelated_changes_do_not_expire_snapshot_and_are_preserved(self):
        self.create()
        self.seed_protected_records()
        old = self.preview()
        self.create(token=self.other_token)
        with app.conn() as c:
            c.execute("UPDATE language_answers SET answer='New protected answer' WHERE user_id=1")
            c.execute("UPDATE courses SET description='New protected notes' WHERE user_id=1")
            c.execute("INSERT INTO rest_days VALUES(1,'2027-01-01','2026-11-16T02:00:00Z')")
        protected = self.table_rows(exclude=('tasks', 'task_id_state'))
        other_tasks = self.task_rows(2)
        self.assertEqual(self.preview()['snapshot'], old['snapshot'])
        self.assertEqual(self.clear(old), (200, {'ok': True, 'deleted': 1}))
        self.assertEqual(self.table_rows(exclude=('tasks', 'task_id_state')), protected)
        self.assertEqual(self.task_rows(2), other_tasks)

    def test_repeated_clearing_empty_tasks_is_safe_and_does_not_backup(self):
        self.create()
        self.assertEqual(self.clear(), (200, {'ok': True, 'deleted': 1}))
        empty = self.preview()
        self.assertEqual({k: v for k, v in empty.items() if k != 'snapshot'},
                         {'total': 0, 'unfinished': 0, 'completed': 0, 'archived': 0, 'running': 0})
        files = {p.name for p in Path(self.temp.name).iterdir()}
        for _ in range(2):
            self.assertEqual(self.clear(empty), (200, {'ok': True, 'deleted': 0}))
        self.assertEqual({p.name for p in Path(self.temp.name).iterdir()}, files)
        self.assertEqual(self.task_rows(), [])

    def test_cleared_ids_cannot_be_reused_or_target_new_tasks_after_restart(self):
        old_ids = [self.create(), self.create()]
        self.assertEqual(self.clear()[1]['deleted'], 2)
        app.init()
        app.init()
        new_id = self.create(title='New ordinary task', planned=None)
        self.assertGreater(new_id, max(old_ids))
        new_task = self.task_rows(1)
        for old in old_ids:
            for method, payload in [('PATCH', {'title': 'Stale device edit'}), ('PATCH', {'done': True}),
                                    ('PATCH', {'action': 'start'}), ('DELETE', None)]:
                self.assertEqual(self.req(f'/tasks/{old}', method, payload)[0], 404)
        self.assertEqual(self.task_rows(1), new_task)
        self.assertEqual(new_task[0]['timing_enabled'], 0)
        self.assertIsNone(new_task[0]['planned'])

    def test_late_legacy_ids_are_retired_by_bulk_and_single_delete(self):
        for highest, method in [(4000, 'bulk'), (8000, 'single')]:
            with app.conn() as c:
                c.execute("INSERT INTO tasks(id,user_id,day,title,category,planned) VALUES(?,1,?,'Imported legacy task','学习',NULL)",
                          (highest, DAY))
            if method == 'bulk':
                self.assertEqual(self.clear()[0], 200)
            else:
                self.assertEqual(self.req(f'/tasks/{highest}', 'DELETE')[0], 200)
            new_id = self.create()
            self.assertGreater(new_id, highest)

    def test_old_template_seed_and_manual_creation_share_nonreused_ids(self):
        first = self.create()
        self.assertEqual(self.clear()[0], 200)
        with app.conn() as c:
            app.seed(c, 1, date.fromisoformat(DAY), 180)
            seeded = [row['id'] for row in c.execute('SELECT id FROM tasks WHERE user_id=1 ORDER BY id')]
        self.assertEqual(len(seeded), 252)
        self.assertGreater(min(seeded), first)
        self.assertEqual(len(set(seeded)), 252)
        self.assertEqual(self.clear()[1]['deleted'], 252)
        self.assertGreater(self.create(), max(seeded))

    def test_dirty_pause_metadata_is_counted_conservatively_without_writes(self):
        tid = self.create(timing_enabled=True, started_at='2026-11-16T01:00:00Z')
        with app.conn() as c:
            c.execute("UPDATE tasks SET pause_history='bad-legacy-json' WHERE id=?", (tid,))
        before = self.task_rows()
        preview = self.preview()
        self.assertEqual((preview['total'], preview['unfinished'], preview['running']), (1, 1, 1))
        self.assertEqual(self.task_rows(), before)
        self.assertEqual(self.clear(preview), (200, {'ok': True, 'deleted': 1}))

    def test_a_failed_delete_rolls_back_tasks_and_internal_id_floor(self):
        self.create()
        second = self.create()
        old = self.preview()
        with app.conn() as c:
            c.execute(f"CREATE TRIGGER clear_failure BEFORE DELETE ON tasks WHEN OLD.id={second} BEGIN SELECT RAISE(ABORT,'Synthetic failure'); END")
        before = self.table_rows()
        self.assertEqual(self.clear(old)[0], 500)
        self.assertEqual(self.table_rows(), before)

    def test_delete_waits_for_writer_then_rechecks_the_committed_snapshot(self):
        tid = self.create()
        old = self.preview()
        attempted = threading.Event()
        original = app.clear_tasks

        def observe(c, uid, data):
            attempted.set()
            return original(c, uid, data)

        with app.conn() as writer, patch.object(app, 'clear_tasks', side_effect=observe):
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE tasks SET note='Committed other device edit' WHERE id=?", (tid,))
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.clear, old)
                self.assertTrue(attempted.wait(2))
                writer.commit()
                status, result = pending.result(timeout=5)
        self.assertEqual(status, 409, result)
        self.assertEqual(self.task_rows(1)[0]['note'], 'Committed other device edit')


class TaskIdMigrationTests(unittest.TestCase):
    def test_old_task_schema_adds_single_id_watermark_without_rebuilding_or_changing_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / 'legacy-task-identities.db'
            with closing(sqlite3.connect(database)) as c, c:
                c.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY,user_id INTEGER,day TEXT,title TEXT,category TEXT,planned INTEGER,actual INTEGER DEFAULT 0,done INTEGER DEFAULT 0,note TEXT DEFAULT '')")
                c.execute("INSERT INTO tasks VALUES(7,1,'2020-01-01','Legacy completed','学习',NULL,35,1,'Keep notes'),(900,2,'2026-11-16','Other owner','生活',30,0,0,'')")
                c.row_factory = sqlite3.Row
                original = [dict(row) for row in c.execute('SELECT * FROM tasks ORDER BY id')]
                original_rootpage = c.execute("SELECT rootpage FROM sqlite_master WHERE name='tasks'").fetchone()[0]
            with patch.object(app, 'DB', database):
                app.init()
                app.init()
                with app.conn() as c:
                    rows = [dict(row) for row in c.execute('SELECT * FROM tasks ORDER BY id')]
                    self.assertEqual(c.execute('SELECT singleton,high_water FROM task_id_state').fetchall()[0][:], (1, 900))
                    self.assertEqual(c.execute('SELECT count(*) FROM task_id_state').fetchone()[0], 1)
                    self.assertEqual(c.execute("SELECT rootpage FROM sqlite_master WHERE name='tasks'").fetchone()[0], original_rootpage)
                    for before, after in zip(original, rows):
                        self.assertEqual({key: after[key] for key in before}, before)
                        self.assertEqual(after['timing_enabled'], 1)
                    self.assertEqual(app.allocate_task_id(c), 901)
                app.init()
                with app.conn() as c:
                    self.assertEqual(app.allocate_task_id(c), 902)


if __name__ == '__main__':
    unittest.main()
