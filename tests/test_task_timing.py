"""Persistent task clocks, with isolated data and deterministic server time."""
from datetime import datetime, timezone
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


if __name__=='__main__':unittest.main(verbosity=2)
