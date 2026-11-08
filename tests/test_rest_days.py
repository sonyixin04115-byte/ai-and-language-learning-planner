"""Rest-day changes use isolated accounts and the real HTTP handlers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sqlite3
import time
import unittest
from unittest.mock import patch

import test_task_timing as timing

app=timing.app
DAY='2026-11-08'


class RestDayTests(unittest.TestCase):
    setUp=timing.TaskTimingTests.setUp
    tearDown=timing.TaskTimingTests.tearDown
    req=timing.TaskTimingTests.req
    task=timing.TaskTimingTests.task
    at=timing.TaskTimingTests.at

    def create(self,**fields):
        existing={row['id'] for row in self.req('/state')[1]['tasks']}
        code,result=self.req('/tasks','POST',{'day':DAY,'title':'自己的任务','planned':30,'category':'学习',**fields})
        self.assertEqual(code,201,result)
        created=[row['id'] for row in self.req('/state')[1]['tasks'] if row['id'] not in existing]
        self.assertEqual(len(created),1)
        return created[0]

    def rest(self,day=DAY,value=True,token='owner'):
        return self.req('/days/rest','POST',{'day':day,'rest':value},token=token)

    def test_flags_are_sorted_persistent_and_account_scoped(self):
        self.assertEqual(self.req('/state')[1]['rest_days'],[])
        self.assertEqual(self.rest('2026-12-16')[0],200)
        self.assertEqual(self.rest()[0],200)
        self.assertEqual(self.req('/state')[1]['rest_days'],[DAY,'2026-12-16'])
        self.assertEqual(self.req('/state',token=self.other_token)[1]['rest_days'],[])
        self.assertEqual(self.rest('2026-12-16',token=self.other_token)[0],200)
        app.init();app.init()
        second=secrets.token_hex(16)
        with app.conn() as c:c.execute('INSERT INTO sessions VALUES(?,?,?)',(second,1,time.time()+3600))
        self.assertEqual(self.req('/state',token=second)[1]['rest_days'],[DAY,'2026-12-16'])
        self.assertEqual(self.rest('2026-12-16',False)[1],{'ok':True,'rest':False,'paused_tasks':0})
        self.assertEqual(self.req('/state')[1]['rest_days'],[DAY])
        self.assertEqual(self.req('/state',token=self.other_token)[1]['rest_days'],['2026-12-16'])

    def test_mark_pauses_only_active_selected_day_tasks_using_one_instant(self):
        active=[self.create(day=DAY,started_at='2026-11-08T01:00:00Z',note='保留任务 '+str(i)) for i in range(2)]
        paused=self.create(day=DAY,started_at='2026-11-08T01:00:00Z')
        with self.at('2026-11-08T01:02:00Z'):self.assertEqual(self.req(f'/tasks/{paused}','PATCH',{'action':'pause'})[0],200)
        untouched=[paused,self.create(day=DAY),self.create(day='2026-11-09',started_at='2026-11-08T01:00:00Z'),
                   self.create(day=DAY,done=True,started_at='2026-11-08T01:00:00Z',finished_at='2026-11-08T01:03:00Z'),
                   self.create(day=DAY,started_at='2026-11-08T01:00:00Z',finished_at='2026-11-08T01:03:00Z')]
        archived=self.create(day=DAY,started_at='2026-11-08T01:00:00Z')
        with app.conn() as c:c.execute('UPDATE tasks SET archived=1 WHERE id=?',(archived,))
        other=self.req('/tasks','POST',{'day':DAY,'title':'其他账号','planned':30,'started_at':'2026-11-08T01:00:00Z'},token=self.other_token)
        self.assertEqual(other[0],201)
        before={tid:self.task(tid) for tid in untouched}
        other_before=self.req('/state',token=self.other_token)[1]['tasks']
        moment=datetime(2026,11,8,1,10,tzinfo=timezone.utc)
        with patch.object(app,'now_utc',return_value=moment) as clock:
            self.assertEqual(self.rest()[1],{'ok':True,'rest':True,'paused_tasks':2})
        self.assertEqual(clock.call_count,1)
        for tid in active:
            row=self.task(tid)
            self.assertEqual(row['pause_intervals'],[{'started_at':'2026-11-08T01:10:00Z','finished_at':None}])
            self.assertEqual(row['done'],0)
            self.assertTrue(row['note'].startswith('保留任务'))
        self.assertEqual({tid:self.task(tid) for tid in untouched},before)
        self.assertEqual(self.req('/state',token=self.other_token)[1]['tasks'],other_before)
        with app.conn() as c:
            self.assertEqual(c.execute('SELECT pause_history FROM tasks WHERE id=?',(archived,)).fetchone()[0],'[]')
            self.assertEqual(c.execute('SELECT created_at FROM rest_days WHERE user_id=1 AND day=?',(DAY,)).fetchone()[0],'2026-11-08T01:10:00Z')

    def test_rest_excludes_break_and_unmark_never_resumes(self):
        tid=self.create(day=DAY,started_at='2026-11-08T01:00:00Z')
        with self.at('2026-11-08T01:10:00Z'):self.assertEqual(self.rest()[0],200)
        paused=self.task(tid)
        with self.at('2026-11-08T03:00:00Z'):self.assertEqual(self.rest()[1]['paused_tasks'],0)
        self.assertEqual(self.task(tid),paused)
        self.assertEqual(self.rest(value=False)[0],200)
        self.assertEqual(self.task(tid),paused)
        self.assertEqual(self.rest(value=False)[0],200)
        with self.at('2026-11-08T03:10:00Z'):self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'resume'})[0],200)
        with self.at('2026-11-08T03:15:00Z'):self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'done':True})[0],200)
        self.assertEqual(self.task(tid)['actual'],15)
        self.assertEqual(self.req('/state')[1]['rest_days'],[])

    def test_rest_flag_does_not_block_deliberate_tasks_or_repeat_pause_new_work(self):
        self.assertEqual(self.rest()[0],200)
        tid=self.create(day=DAY,note='休息日也可自选任务')
        with self.at('2026-11-08T01:00:00Z'):self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'start'})[0],200)
        with self.at('2026-11-08T01:20:00Z'):self.assertEqual(self.rest()[1]['paused_tasks'],0)
        self.assertEqual(self.task(tid)['pause_intervals'],[])
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'note':'修改自己的安排'})[0],200)
        self.assertEqual(self.req('/state')[1]['rest_days'],[DAY])

    def test_invalid_payload_methods_and_anonymous_requests_do_not_write(self):
        before=self.req('/state')[1]
        invalid=[{},[],None,False,{'day':DAY},{'day':DAY,'rest':'true'},{'day':DAY,'rest':1},
                 {'day':DAY,'rest':None},{'day':DAY,'rest':True,'user_id':2},
                 {'day':None,'rest':True},{'day':20261108,'rest':True},
                 {'day':'20261108','rest':True},{'day':'2026-W45-7','rest':True},
                 {'day':'2026-02-30','rest':True},{'day':'2026-11-08T00:00:00Z','rest':True}]
        for payload in invalid:
            with self.subTest(payload=payload):self.assertEqual(self.req('/days/rest','POST',payload)[0],400)
        for method in ('GET','PATCH','DELETE'):
            with self.subTest(method=method):self.assertEqual(self.req('/days/rest',method,{'day':DAY,'rest':True})[0],405)
        self.assertEqual(self.rest(token=None)[0],401)
        self.assertEqual(self.req('/state')[1],before)

    def test_failed_batch_rolls_back_all_pauses_and_the_rest_flag(self):
        valid=self.create(day=DAY,started_at='2026-11-08T01:00:00Z')
        future=self.create(day=DAY,started_at='2026-11-08T03:00:00Z')
        before=[self.task(valid),self.task(future)]
        with self.at('2026-11-08T02:00:00Z'):self.assertEqual(self.rest()[0],400)
        self.assertEqual([self.task(valid),self.task(future)],before)
        self.assertEqual(self.req('/state')[1]['rest_days'],[])

    def test_concurrent_devices_mark_once_without_duplicate_pause(self):
        tid=self.create(day=DAY,started_at='2026-11-08T01:00:00Z')
        with self.at('2026-11-08T01:10:00Z'):
            with ThreadPoolExecutor(max_workers=2) as workers:
                results=list(workers.map(lambda _:self.rest(),range(2)))
        self.assertTrue(all(code==200 for code,_ in results))
        self.assertEqual(sorted(result['paused_tasks'] for _,result in results),[0,1])
        self.assertEqual(len(self.task(tid)['pause_intervals']),1)
        self.assertEqual(self.req('/state')[1]['rest_days'],[DAY])

    def test_additive_migration_preserves_existing_task_columns(self):
        legacy=Path(self.temp.name)/'legacy.db'
        with closing(sqlite3.connect(legacy)) as c:
            with c:
                c.execute('CREATE TABLE tasks(id INTEGER PRIMARY KEY,user_id INTEGER,day TEXT,title TEXT,category TEXT,planned INTEGER,actual INTEGER,done INTEGER,note TEXT)')
                c.execute("INSERT INTO tasks VALUES(1,1,'2026-11-08','原任务','学习',30,17,1,'原笔记')")
                before=c.execute('SELECT * FROM tasks').fetchall()
        with patch.object(app,'DB',legacy):app.init();app.init()
        with closing(sqlite3.connect(legacy)) as c:
            self.assertEqual(c.execute('SELECT id,user_id,day,title,category,planned,actual,done,note FROM tasks').fetchall(),before)
            self.assertEqual(c.execute('SELECT count(*) FROM rest_days').fetchone()[0],0)


if __name__=='__main__':unittest.main(verbosity=2)
