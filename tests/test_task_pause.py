"""Pause/resume clocks use isolated accounts, deterministic times, and real HTTP."""
from contextlib import closing
from pathlib import Path
import secrets
import sqlite3
import unittest
from unittest.mock import patch

import test_task_timing as timing

app=timing.app


class TaskPauseTests(unittest.TestCase):
    setUp=timing.TaskTimingTests.setUp
    tearDown=timing.TaskTimingTests.tearDown
    req=timing.TaskTimingTests.req
    create=timing.TaskTimingTests.create
    task=timing.TaskTimingTests.task
    at=timing.TaskTimingTests.at
    overlapping_updates=timing.TaskTimingTests.overlapping_updates

    def action(self,tid,action,when):
        with self.at(when):
            code,result=self.req(f'/tasks/{tid}','PATCH',{'action':action})
        self.assertEqual(code,200,result)
        return self.task(tid)

    def complete(self,tid,when):
        with self.at(when):
            code,result=self.req(f'/tasks/{tid}','PATCH',{'done':True})
        self.assertEqual(code,200,result)
        return self.task(tid)

    def test_pause_excludes_rest_and_repeated_actions_are_idempotent(self):
        tid=self.create()
        self.action(tid,'start','2026-11-02T00:00:00Z')
        paused=self.action(tid,'pause','2026-11-02T00:10:00Z')
        self.assertEqual(paused['pause_intervals'],[{'started_at':'2026-11-02T00:10:00Z','finished_at':None}])
        self.assertNotIn('pause_history',paused)
        self.assertEqual(self.action(tid,'pause','2026-11-02T00:20:00Z')['pause_intervals'],paused['pause_intervals'])
        # A late duplicate Start must not resume a task another device paused.
        self.assertEqual(self.action(tid,'start','2026-11-02T00:25:00Z')['pause_intervals'],paused['pause_intervals'])
        resumed=self.action(tid,'resume','2026-11-02T00:50:00Z')
        self.assertEqual(resumed['pause_intervals'][0]['finished_at'],'2026-11-02T00:50:00Z')
        self.assertEqual(self.action(tid,'resume','2026-11-02T01:00:00Z')['pause_intervals'],resumed['pause_intervals'])
        self.action(tid,'pause','2026-11-02T01:02:00Z')
        self.action(tid,'resume','2026-11-02T01:04:00Z')
        finished=self.complete(tid,'2026-11-02T01:05:00Z')
        self.assertEqual((finished['actual'],len(finished['pause_intervals'])),(23,2))

    def test_ten_minutes_then_long_rest_then_five_minutes_is_fifteen(self):
        tid=self.create()
        self.action(tid,'start','2026-11-02T00:00:00Z')
        self.action(tid,'pause','2026-11-02T00:10:00Z')
        self.action(tid,'resume','2026-11-02T02:10:00Z')
        finished=self.complete(tid,'2026-11-02T02:15:00Z')
        self.assertEqual(finished['actual'],15)
        self.assertEqual(finished['finished_at'],'2026-11-02T02:15:00Z')

    def test_fractional_timestamps_do_not_round_an_exact_ten_minutes_up(self):
        # This crosses a floating-point precision boundary during a long rest.
        # Active time is exactly 600 seconds despite fractional wall-clock time.
        tid=self.create()
        self.action(tid,'start','2026-11-02T00:00:00.123456Z')
        self.action(tid,'pause','2026-11-02T00:10:00.123456Z')
        finished=self.complete(tid,'2026-11-05T00:49:04.523456Z')
        self.assertEqual(finished['actual'],10)

    def test_completing_while_paused_freezes_active_time_even_across_days(self):
        tid=self.create()
        self.action(tid,'start','2026-11-02T00:00:00Z')
        self.action(tid,'pause','2026-11-02T00:12:30Z')
        finished=self.complete(tid,'2026-11-04T10:00:00Z')
        self.assertEqual(finished['actual'],13)
        self.assertEqual(finished['pause_intervals'][-1]['finished_at'],finished['finished_at'])
        start=app.task_time(finished['started_at'],'start');end=app.task_time(finished['finished_at'],'end')
        pauses=app.read_pause_history(finished['pause_intervals'])
        self.assertEqual((end-start).total_seconds()-app.paused_seconds(pauses,start,end),750)
        self.assertEqual(self.complete(tid,'2026-11-05T10:00:00Z'),finished)

    def test_manual_time_corrections_deduct_only_intersecting_pauses(self):
        tid=self.create()
        self.action(tid,'start','2026-11-02T00:00:00Z')
        self.action(tid,'pause','2026-11-02T00:10:00Z')
        self.action(tid,'resume','2026-11-02T00:30:00Z')
        original=self.complete(tid,'2026-11-02T00:40:00Z')
        self.assertEqual(original['actual'],20)
        for start,end,actual in [
            ('00:20:00','00:50:00',20),
            ('00:20:00','00:25:00',0),
            ('00:00:00','00:15:00',10),
            ('00:40:00','00:50:00',10),
        ]:
            with self.subTest(start=start,end=end):
                code,_=self.req(f'/tasks/{tid}','PATCH',{'started_at':'2026-11-02T'+start+'Z','finished_at':'2026-11-02T'+end+'Z','actual':999})
                self.assertEqual(code,200)
                saved=self.task(tid)
                self.assertEqual(saved['actual'],actual)
                self.assertEqual(saved['pause_intervals'],original['pause_intervals'])

    def test_manual_end_before_open_pause_closes_it_without_negative_duration(self):
        tid=self.create()
        self.action(tid,'start','2026-11-02T00:00:00Z')
        self.action(tid,'pause','2026-11-02T00:10:00Z')
        code,_=self.req(f'/tasks/{tid}','PATCH',{'finished_at':'2026-11-02T00:05:00Z','done':True})
        self.assertEqual(code,200)
        saved=self.task(tid)
        self.assertEqual(saved['actual'],5)
        self.assertEqual(saved['pause_intervals'],[{'started_at':'2026-11-02T00:10:00Z','finished_at':'2026-11-02T00:10:00Z'}])

    def test_pause_state_survives_init_and_another_device_can_resume(self):
        tid=self.create()
        self.action(tid,'start','2026-11-02T00:00:00Z')
        paused=self.action(tid,'pause','2026-11-02T00:10:00Z')
        app.init();app.init()
        other_device=secrets.token_hex(16)
        with app.conn() as connection:
            connection.execute('INSERT INTO sessions VALUES(?,?,?)',(other_device,1,timing.time.time()+3600))
        code,state=self.req('/state',token=other_device)
        self.assertEqual(code,200)
        self.assertEqual(next(t for t in state['tasks'] if t['id']==tid),paused)
        with self.at('2026-11-02T01:10:00Z'):
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'resume'},token=other_device)[0],200)
        self.assertEqual(self.complete(tid,'2026-11-02T01:15:00Z')['actual'],15)

    def test_invalid_transitions_and_forged_history_do_not_mutate_tasks(self):
        tid=self.create()
        original=self.task(tid)
        for data in ({'action':'pause'},{'action':'resume'},{'action':'pause','note':'extra'},[]):
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',data)[0],400)
            self.assertEqual(self.task(tid),original)
        for key,value in [('pause_history','[]'),('pause_intervals',[]),('paused_at',None),('paused_ms',0),('paused_seconds',0),('pause_count',0)]:
            with self.subTest(key=key):
                self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{key:value})[0],400)
                self.assertEqual(self.task(tid),original)
                self.assertEqual(self.req('/tasks','POST',{'day':'2026-11-02','title':'forged','planned':30,key:value})[0],400)
        self.assertEqual(len(self.req('/state')[1]['tasks']),1)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'started_at':'2026-11-03T00:00:00Z'})[0],200)
        future=self.task(tid)
        with self.at('2026-11-02T00:00:00Z'):
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'pause'})[0],400)
        self.assertEqual(self.task(tid),future)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'finished_at':'2026-11-03T00:01:00Z','done':True})[0],200)
        finished=self.task(tid)
        for action in ('pause','resume','start'):
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':action})[0],400)
            self.assertEqual(self.task(tid),finished)

    def test_pause_actions_are_scoped_to_the_authenticated_owner(self):
        tid=self.create()
        self.action(tid,'start','2026-11-02T00:00:00Z')
        before=self.task(tid)
        for action in ('pause','resume'):
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':action},token=self.other_token)[0],404)
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':action},token=None)[0],401)
        self.assertEqual(self.task(tid),before)

    def test_concurrent_device_pauses_and_resumes_do_not_duplicate_rest(self):
        tid=self.create(started_at='2026-11-02T00:00:00Z')
        calls=self.overlapping_updates(tid,{'action':'pause'},'2026-11-02T00:10:00Z','2026-11-02T00:20:00Z')
        self.assertEqual(calls[0][3],calls[1][3])
        self.assertEqual(self.task(tid)['pause_intervals'],[{'started_at':'2026-11-02T00:10:00Z','finished_at':None}])
        calls=self.overlapping_updates(tid,{'action':'resume'},'2026-11-02T00:40:00Z','2026-11-02T00:50:00Z')
        self.assertEqual(calls[0][3],calls[1][3])
        self.assertEqual(self.complete(tid,'2026-11-02T01:00:00Z')['actual'],30)

    def test_concurrent_pause_then_completion_uses_committed_pause(self):
        tid=self.create(started_at='2026-11-02T00:00:00Z')
        self.overlapping_updates(tid,{'action':'pause'},'2026-11-02T00:10:00Z','2026-11-02T00:20:00Z',second_payload={'done':True})
        saved=self.task(tid)
        self.assertEqual(saved['actual'],10)
        self.assertEqual(saved['pause_intervals'],[{'started_at':'2026-11-02T00:10:00Z','finished_at':'2026-11-02T00:20:00Z'}])

    def test_only_effective_learning_time_is_limited_to_twenty_four_hours(self):
        tid=self.create()
        self.action(tid,'start','2026-11-02T00:00:00Z')
        self.action(tid,'pause','2026-11-02T00:10:00Z')
        self.action(tid,'resume','2026-11-05T00:10:00Z')
        before=self.task(tid)
        with self.at('2026-11-06T00:10:00Z'):
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'done':True})[0],400)
        self.assertEqual(self.task(tid),before)
        self.assertEqual(self.complete(tid,'2026-11-05T00:15:00Z')['actual'],15)

    def test_old_database_migration_adds_empty_history_without_rewriting_rows(self):
        legacy=Path(self.temp.name)/'legacy.db'
        with closing(sqlite3.connect(legacy)) as connection:
            with connection:
                connection.execute('''CREATE TABLE tasks(id INTEGER PRIMARY KEY,user_id INTEGER,
                    day TEXT,title TEXT,category TEXT,planned INTEGER,actual INTEGER,done INTEGER,note TEXT)''')
                connection.execute("INSERT INTO tasks VALUES(1,1,'2026-11-02','old','学习',30,17,1,'keep')")
                original=connection.execute('SELECT * FROM tasks').fetchall()
        with patch.object(app,'DB',legacy):app.init();app.init()
        with closing(sqlite3.connect(legacy)) as connection:
            self.assertEqual(connection.execute('SELECT id,user_id,day,title,category,planned,actual,done,note FROM tasks').fetchall(),original)
            self.assertEqual(connection.execute('SELECT pause_history FROM tasks').fetchone()[0],'[]')
            columns=[row[1] for row in connection.execute('PRAGMA table_info(tasks)')]
            self.assertEqual(columns.count('pause_history'),1)


if __name__=='__main__':unittest.main(verbosity=2)
