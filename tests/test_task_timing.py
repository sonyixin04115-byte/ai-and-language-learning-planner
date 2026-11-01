"""Persistent task clocks, with isolated data and deterministic server time."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('timing_backend',ROOT/'backend/server.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)


class TaskTimingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();app.DB=Path(self.temp.name)/'study.db'
        self.catalog_patch=patch.object(app.materials,'catalog',return_value={'root':'','courses':[],'by_course':{},'by_lesson':{}})
        self.catalog_patch.start();app.init()
        self.token=secrets.token_hex(16);self.other_token=secrets.token_hex(16)
        with app.conn() as c:
            for uid,token in [(1,self.token),(2,self.other_token)]:
                c.execute('INSERT INTO users(id,username,name,start) VALUES(?,?,?,?)',(uid,'temporary-'+str(uid),'测试','2026-11-01'))
                c.execute('INSERT INTO sessions VALUES(?,?,?)',(token,uid,time.time()+3600))
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)+'/api'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.catalog_patch.stop();self.temp.cleanup()

    def req(self,path,method='GET',data=None,token='owner'):
        if token=='owner':token=self.token
        headers={'Content-Type':'application/json'}
        if token:headers['Cookie']='session='+token
        request=urllib.request.Request(self.base+path,method=method,headers=headers,
            data=json.dumps(data).encode('utf-8') if data is not None else None)
        try:
            with urllib.request.urlopen(request) as result:return result.status,json.load(result)
        except urllib.error.HTTPError as result:
            with result:return result.code,json.load(result)

    def create(self,**fields):
        code,data=self.req('/tasks','POST',{'title':'自己安排的任务','day':'2026-11-01','category':'学习','planned':30,**fields})
        self.assertEqual(code,201,data)
        return self.req('/state')[1]['tasks'][-1]['id']

    def task(self,tid):return next(t for t in self.req('/state')[1]['tasks'] if t['id']==tid)

    def at(self,value):return patch.object(app,'now_utc',return_value=datetime.fromisoformat(value).astimezone(timezone.utc))

    def test_start_repeat_complete_and_uncomplete_preserve_real_clock(self):
        tid=self.create(actual=20,note='保留笔记')
        with self.at('2026-11-01T02:00:00+00:00'):
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'start'})[0],200)
        start=self.task(tid);self.assertEqual(start['started_at'],'2026-11-01T02:00:00Z')
        self.assertIsNone(start['finished_at']);self.assertEqual(start['actual'],0)
        with self.at('2026-11-01T02:00:30+00:00'):
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'start'})[0],200)
        self.assertEqual(self.task(tid)['started_at'],start['started_at'])
        with self.at('2026-11-01T02:01:30+00:00'):
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'done':True})[0],200)
        finished=self.task(tid)
        self.assertEqual((finished['finished_at'],finished['actual'],finished['done']),('2026-11-01T02:01:30Z',2,1))
        self.assertEqual(finished['note'],'保留笔记')
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'start'})[0],400)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'done':False})[0],200)
        undone=self.task(tid)
        self.assertEqual((undone['started_at'],undone['finished_at'],undone['actual']),
            (finished['started_at'],finished['finished_at'],2))
        with self.at('2026-11-01T05:00:00+00:00'):
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'done':True})[0],200)
        self.assertEqual(self.task(tid)['finished_at'],finished['finished_at'])

    def test_manual_offset_times_determine_actual_and_support_clear(self):
        tid=self.create(started_at='2026-11-01T09:00:00+09:00',finished_at='2026-11-01T08:30:30+08:00',actual=999)
        row=self.task(tid)
        self.assertEqual((row['started_at'],row['finished_at'],row['actual']),('2026-11-01T00:00:00Z','2026-11-01T00:30:30Z',31))
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'note':'只改笔记','actual':200})[0],200)
        self.assertEqual(self.task(tid)['actual'],31)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'started_at':None})[0],400)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'started_at':None,'finished_at':'','actual':33})[0],200)
        cleared=self.task(tid)
        self.assertEqual((cleared['started_at'],cleared['finished_at'],cleared['actual']),(None,None,33))

    def test_open_interval_has_no_final_minutes_and_nonzero_rounds_up(self):
        tid=self.create(started_at='2026-11-01T02:00:00Z',actual=15)
        self.assertEqual(self.task(tid)['actual'],0)
        with self.at('2026-11-01T02:00:01+00:00'):
            self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'done':True})[0],200)
        self.assertEqual(self.task(tid)['actual'],1)
        zero=self.create(started_at='2026-11-01T02:00:00Z',finished_at='2026-11-01T02:00:00Z')
        self.assertEqual(self.task(zero)['actual'],0)

    def test_invalid_intervals_are_rejected_without_mutating_task(self):
        tid=self.create(actual=12)
        invalid=[
            {'started_at':'2026-11-01T12:00'},
            {'started_at':'not-a-date'},
            {'started_at':False},
            {'finished_at':'2026-11-01T02:00:00Z'},
            {'started_at':'2026-11-01T02:00:00Z','finished_at':'2026-11-01T01:59:00Z'},
            {'started_at':'2026-11-01T02:00:00Z','finished_at':'2026-11-02T02:00:01Z'},
            {'action':'restart'},
            {'action':'start','actual':30},
            {'done':'false'},
        ]
        for fields in invalid:
            with self.subTest(fields=fields):self.assertEqual(self.req(f'/tasks/{tid}','PATCH',fields)[0],400)
        self.assertEqual((self.task(tid)['started_at'],self.task(tid)['finished_at'],self.task(tid)['actual']),(None,None,12))
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'started_at':'2026-11-01T02:00:00Z','finished_at':'2026-11-02T02:00:00Z'})[0],200)
        self.assertEqual(self.task(tid)['actual'],1440)

    def test_manual_minutes_without_timer_and_ownership_remain_unchanged(self):
        tid=self.create(actual=25,done=True)
        self.assertEqual(self.task(tid)['actual'],25)
        self.assertIsNone(self.task(tid)['started_at'])
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'done':False})[0],200)
        self.assertEqual(self.task(tid)['actual'],25)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'action':'start'},token=self.other_token)[0],404)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'done':True},token=None)[0],401)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'actual':1441})[0],400)

    def test_timestamps_persist_after_migration_and_second_session(self):
        tid=self.create(started_at='2026-11-01T02:00:00Z',finished_at='2026-11-01T02:45:00Z',done=True)
        app.init();app.init()
        second_token=secrets.token_hex(16)
        with app.conn() as c:c.execute('INSERT INTO sessions VALUES(?,?,?)',(second_token,1,time.time()+3600))
        code,state=self.req('/state',token=second_token)
        self.assertEqual(code,200)
        row=next(t for t in state['tasks'] if t['id']==tid)
        self.assertEqual((row['started_at'],row['finished_at'],row['actual']),('2026-11-01T02:00:00Z','2026-11-01T02:45:00Z',45))

    def test_archive_only_untouched_templates_preserves_manual_and_timed_tasks(self):
        title=app.WEEKS[0][1][0]
        manual=self.create(title=title,category='学习',planned=72)
        running=self.create(title=title,category='学习',planned=72)
        self.assertEqual(self.req(f'/tasks/{running}','PATCH',{'action':'start'})[0],200)
        template_ids={}
        with app.conn() as c:
            for label,changes in [
                ('untouched',{}),
                ('started',{'started_at':'2026-11-01T02:00:00Z'}),
                ('finished',{'started_at':'2026-11-01T02:00:00Z','finished_at':'2026-11-01T02:00:00Z'}),
                ('noted',{'note':'自己的笔记'}),
                ('minutes',{'actual':12}),
                ('done',{'done':1}),
            ]:
                cur=c.execute("INSERT INTO tasks(user_id,day,title,category,planned,created_source) VALUES(1,'2026-11-01',?,'学习',72,'template')",(title,))
                template_ids[label]=cur.lastrowid
                for key,value in changes.items():c.execute('UPDATE tasks SET '+key+'=? WHERE id=?',(value,cur.lastrowid))
        code,result=self.req('/tasks/archive-template','POST',{})
        self.assertEqual((code,result['archived']),(200,1))
        visible={task['id'] for task in self.req('/state')[1]['tasks']}
        self.assertIn(manual,visible);self.assertIn(running,visible)
        self.assertNotIn(template_ids['untouched'],visible)
        self.assertTrue(all(tid in visible for label,tid in template_ids.items() if label!='untouched'))

    def overlapping_updates(self,tid,payload,first_time,second_time,second_path=None,second_method='PATCH',second_payload=None):
        """Overlap two API calls before the first write, without real data."""
        first_read=threading.Event();second_ready=threading.Event()
        release_first=threading.Event();first_reply=threading.Event()
        calls=[];guard=threading.Lock();first_thread=[None]
        original_timing=app.task_timing;original_conn=app.conn
        @contextmanager
        def traced_conn():
            with original_conn() as connection:
                def trace(statement):
                    # The second writer may be blocked on the write lock before
                    # it can read. Its attempted BEGIN confirms real overlap.
                    if statement=='BEGIN IMMEDIATE' and first_thread[0] is not None and threading.get_ident()!=first_thread[0]:second_ready.set()
                connection.set_trace_callback(trace)
                yield connection
        def timing(data,old=None,done=False):
            with guard:index=len(calls);calls.append(None)
            if index==0:
                first_thread[0]=threading.get_ident();first_read.set()
                if not release_first.wait(5):raise RuntimeError('test first-write gate timed out')
            else:
                second_ready.set()
                if not first_reply.wait(5):raise RuntimeError('test commit gate timed out')
            with self.at(first_time if index==0 else second_time):result=original_timing(data,old,done)
            calls[index]=result
            return result
        def first_request():
            try:return self.req(f'/tasks/{tid}','PATCH',payload)
            finally:first_reply.set()
        with patch.object(app,'conn',side_effect=traced_conn),patch.object(app,'task_timing',side_effect=timing):
            with ThreadPoolExecutor(max_workers=2) as workers:
                first=workers.submit(first_request)
                try:
                    self.assertTrue(first_read.wait(5))
                    second=workers.submit(self.req,second_path or f'/tasks/{tid}',second_method,payload if second_payload is None else second_payload)
                    self.assertTrue(second_ready.wait(5))
                finally:release_first.set()
                self.assertEqual(first.result(timeout=10)[0],200)
                self.assertEqual(second.result(timeout=10)[0],200)
        return calls

    def test_overlapping_device_starts_and_completions_keep_first_times(self):
        tid=self.create()
        starts=self.overlapping_updates(tid,{'action':'start'},'2026-11-02T00:00:00Z','2026-11-02T00:05:00Z')
        self.assertEqual([row[0] for row in starts],['2026-11-02T00:00:00Z']*2)
        self.assertEqual(self.task(tid)['started_at'],'2026-11-02T00:00:00Z')
        finishes=self.overlapping_updates(tid,{'done':True},'2026-11-02T00:01:00Z','2026-11-02T00:05:00Z')
        self.assertEqual([row[1] for row in finishes],['2026-11-02T00:01:00Z']*2)
        row=self.task(tid)
        self.assertEqual((row['finished_at'],row['actual']),('2026-11-02T00:01:00Z',1))

    def test_overlapping_archive_cannot_hide_newly_started_template(self):
        tid=self.create(title=app.WEEKS[0][1][0],category='学习',planned=72)
        with app.conn() as c:c.execute("UPDATE tasks SET created_source='template' WHERE id=?",(tid,))
        self.overlapping_updates(tid,{'action':'start'},'2026-11-02T00:00:00Z','2026-11-02T00:05:00Z',
            second_path='/tasks/archive-template',second_method='POST',second_payload={})
        state=self.req('/state')[1]
        self.assertEqual(state['archived_task_count'],0)
        self.assertEqual(self.task(tid)['started_at'],'2026-11-02T00:00:00Z')


if __name__=='__main__':unittest.main(verbosity=2)
