"""Lesson progress uses temporary accounts/catalogs; real study data is untouched."""
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
spec=importlib.util.spec_from_file_location('lesson_backend',ROOT/'backend/server.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)


def lesson(lid,kind='video'):
    return {'id':lid,'title':lid,'section':'测试章节','kind':kind,'size':100,'extension':'.mp4' if kind=='video' else '.pdf'}


class LessonProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();app.DB=Path(self.temp.name)/'study.db'
        self.catalog={'root':'','courses':[], 'by_course':{
            'course-a':{'path':'A','lessons':[lesson('a-first'),lesson('a-second'),lesson('a-notes','document')]},
            'course-b':{'path':'B','lessons':[lesson('b-first')]}
        },'by_lesson':{}}
        self.catalog_patch=patch.object(app.materials,'catalog',return_value=self.catalog)
        self.catalog_patch.start();app.init()
        self.token=secrets.token_hex(16);self.other_token=secrets.token_hex(16)
        with app.conn() as c:
            for uid,token in [(1,self.token),(2,self.other_token)]:
                c.execute('INSERT INTO users(id,username,name,start) VALUES(?,?,?,?)',(uid,'temporary-'+str(uid),'临时测试账号','2026-11-01'))
                c.execute('INSERT INTO sessions VALUES(?,?,?)',(token,uid,time.time()+3600))
            c.executemany('INSERT INTO courses(id,user_id,title,local_key) VALUES(?,?,?,?)',[
                (11,1,'测试课程A','course-a'),(12,1,'测试课程B','course-b'),(21,2,'另一个账号的课程','course-a')])
            c.execute('INSERT INTO tasks(user_id,day,title,category,planned,note) VALUES(?,?,?,?,?,?)',(1,'2026-11-01','自己的安排','其他',30,'自己的记录'))
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

    def progress(self):return self.req('/courses/11/lessons')[1]

    def test_video_only_progress_and_undo_do_not_modify_daily_tasks(self):
        before=self.req('/state')[1]['tasks']
        data=self.progress()
        self.assertEqual(data['course']['completed_count'],0)
        self.assertTrue(all(item['done'] is False for item in data['lessons']))
        self.assertEqual(self.req('/courses/11/lessons/a-first','PATCH',{'done':True}),
            (200,{'ok':True,'done':True,'completed_count':1}))
        rows=self.progress()['lessons']
        self.assertEqual([(x['id'],x['done']) for x in rows],[('a-first',True),('a-second',False),('a-notes',False)])
        state=self.req('/state')[1]
        self.assertEqual(next(c for c in state['courses'] if c['id']==11)['completed_count'],1)
        self.assertEqual(state['tasks'],before)
        self.assertEqual(self.req('/courses/11/lessons/a-first','PATCH',{'done':False})[1]['completed_count'],0)
        self.assertFalse(self.progress()['lessons'][0]['done'])
        self.assertEqual(self.req('/state')[1]['tasks'],before)

    def test_idempotent_marks_persist_across_init_and_new_login_session(self):
        for _ in range(2):self.assertEqual(self.req('/courses/11/lessons/a-first','PATCH',{'done':True})[0],200)
        with app.conn() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM lesson_progress').fetchone()[0],1)
        app.init();app.init()
        second_session=secrets.token_hex(16)
        with app.conn() as c:c.execute('INSERT INTO sessions VALUES(?,?,?)',(second_session,1,time.time()+3600))
        status,data=self.req('/courses/11/lessons',token=second_session)
        self.assertEqual(status,200);self.assertTrue(data['lessons'][0]['done'])
        self.assertEqual(data['course']['completed_count'],1)

    def test_auth_account_isolation_and_course_ownership(self):
        self.assertEqual(self.req('/courses/11/lessons',token=None)[0],401)
        self.assertEqual(self.req('/courses/11/lessons/a-first','PATCH',{'done':True},token=None)[0],401)
        self.assertEqual(self.req('/courses/11/lessons/a-first','PATCH',{'done':True},token=self.other_token)[0],404)
        self.assertEqual(self.req('/courses/11/lessons',token=self.other_token)[0],404)
        self.assertEqual(self.req('/courses/11/lessons/a-first','PATCH',{'done':True})[0],200)
        status,data=self.req('/courses/21/lessons',token=self.other_token)
        self.assertEqual(status,200);self.assertFalse(data['lessons'][0]['done'])

    def test_cross_course_unknown_documents_and_archived_courses_are_rejected(self):
        for path in ['/courses/11/lessons/b-first','/courses/11/lessons/unknown','/courses/11/lessons/a-notes']:
            with self.subTest(path=path):self.assertEqual(self.req(path,'PATCH',{'done':True})[0],404)
        with app.conn() as c:c.execute('UPDATE courses SET archived=1 WHERE id=11')
        self.assertEqual(self.req('/courses/11/lessons/a-first','PATCH',{'done':True})[0],404)
        self.assertEqual(self.req('/courses/11/lessons')[0],404)

    def test_strict_boolean_input_and_unsupported_methods(self):
        for data in [{},{'done':'false'},{'done':0},{'done':1},{'done':None},[True]]:
            with self.subTest(data=data):self.assertEqual(self.req('/courses/11/lessons/a-first','PATCH',data)[0],400)
        self.assertEqual(self.req('/courses/11/lessons/a-first','POST',{'done':True})[0],405)
        self.assertEqual(self.progress()['course']['completed_count'],0)

    def test_removed_catalog_videos_no_longer_inflate_course_count(self):
        self.assertEqual(self.req('/courses/11/lessons/a-first','PATCH',{'done':True})[0],200)
        self.catalog['by_course']['course-a']['lessons']=[lesson('a-second'),lesson('a-notes','document')]
        self.assertEqual(self.progress()['course']['completed_count'],0)
        state=self.req('/state')[1]
        self.assertEqual(next(c for c in state['courses'] if c['id']==11)['completed_count'],0)


if __name__=='__main__':unittest.main(verbosity=2)
