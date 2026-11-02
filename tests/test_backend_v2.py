import importlib.util
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import http.cookiejar
import hashlib
from datetime import date
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('study_backend',ROOT/'backend/server.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)

class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();app.DB=Path(cls.tmp.name)/'study.db'
        # Exercise migration of the previous real schema, not only fresh creation.
        c=sqlite3.connect(app.DB)
        with c:
            c.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,name TEXT,password TEXT,salt TEXT,minutes INTEGER DEFAULT 180,start TEXT); CREATE TABLE tasks(id INTEGER PRIMARY KEY,user_id INTEGER,day TEXT,title TEXT,category TEXT,planned INTEGER,actual INTEGER DEFAULT 0,done INTEGER DEFAULT 0,note TEXT DEFAULT '');")
        c.close()
        app.init()
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)+'/api'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()

    def setUp(self):
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.username='test_'+hashlib.sha256(self.id().encode()).hexdigest()[:16]
        self.assertEqual(self.req('/register','POST',{'username':self.username,'password':'test-password'})[0],200)

    def req(self,path,method='GET',data=None,client=None):
        body=json.dumps(data).encode() if data is not None else None
        request=urllib.request.Request(self.base+path,data=body,method=method,headers={'Content-Type':'application/json'})
        try:
            with (client or self.client).open(request) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as response:
            with response:return response.code,json.load(response)

    def state(self):return self.req('/state')[1]

    def assert_default_courses(self):
        courses=self.state()['courses']
        self.assertEqual(len(courses),8)
        ai=[course for course in courses if course['category']=='ai']
        self.assertEqual(len(ai),3)
        self.assertEqual({(course['title'],course['url']) for course in ai},{
            ('Python 中文教程','https://docs.python.org/zh-cn/3/tutorial/'),
            ('scikit-learn 入门指南','https://scikit-learn.org/stable/getting_started.html'),
            ('动手学深度学习','https://zh.d2l.ai/')})
        self.assertTrue(all(course['curriculum_key'] is None for course in ai))
        self.assertEqual({course['curriculum_key'] for course in courses if course['category']=='english'},
            {'english-system-b2','english-academic-openlearn','english-zh-ielts-dut'})
        self.assertEqual({course['curriculum_key'] for course in courses if course['category']=='korean'},
            {'korean-chongya','korean-hanyuup'})
        self.assertEqual({course['category'] for course in courses},{'ai','english','korean'})
        recommended=[course for course in courses if course['category'] in ('ai','english')]
        self.assertEqual(len(recommended),6)
        self.assertEqual({course['source_label'] for course in recommended},{'补充推荐'})
        self.assertEqual({course['source'] for course in recommended},{'supplement'})
        korean=[course for course in courses if course['category']=='korean']
        self.assertEqual(len(korean),2)
        self.assertEqual({course['source_label'] for course in korean},{'我的课程'})
        self.assertEqual({course['source'] for course in korean},{'user'})
        return courses

    def test_health_manual_signup_defaults_once(self):
        self.assertEqual(self.req('/health')[1],{'app':'zhixu-planner','version':'2'})
        self.assertEqual(self.state()['tasks'],[])
        original_ids={course['id'] for course in self.assert_default_courses()}
        app.init();app.init()
        self.assertEqual({course['id'] for course in self.assert_default_courses()},original_ids)
        self.assertEqual(self.req('/template','POST',{'start':'2026-11-01'})[0],410)
        self.assertEqual(self.state()['tasks'],[])

    def test_courses_crud_and_isolation(self):
        status,result=self.req('/courses','POST',{'title':'我的课程','url':'https://example.com/course','description':'资料说明'})
        self.assertEqual(status,201);cid=result['id']
        course=next(c for c in self.state()['courses'] if c['id']==cid)
        self.assertEqual(course['source_label'],'我的课程')
        self.assertEqual(self.req(f'/courses/{cid}','PATCH',{'title':'更新标题'})[0],200)
        course=next(c for c in self.state()['courses'] if c['id']==cid)
        self.assertEqual(course['url'],'https://example.com/course')
        stranger=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.assertEqual(self.req('/register','POST',{'username':self.username+'_other','password':'test-password'},stranger)[0],200)
        self.assertEqual(self.req(f'/courses/{cid}','PATCH',{'title':'越权'},stranger)[0],404)
        self.assertEqual(self.req(f'/courses/{cid}','DELETE',client=stranger)[0],404)
        self.assertEqual(self.req(f'/courses/{cid}','DELETE')[0],200)
        self.assertFalse(any(c['id']==cid for c in self.state()['courses']))
        app.init()
        self.assert_default_courses()

    def test_invalid_course_urls(self):
        for url in ['javascript:alert(1)','file:///C:/secret','https://','https://user:pass@example.com','https://example.com/has space']:
            with self.subTest(url=url):self.assertEqual(self.req('/courses','POST',{'title':'test','url':url})[0],400)
        self.assertEqual(self.req('/courses','POST',{'title':'','url':''})[0],400)
        self.assertEqual(self.req('/courses','POST',{'title':'x','source':'invalid'})[0],400)

    def test_partial_task_updates_preserve_progress(self):
        self.assertEqual(self.req('/tasks','POST',{'day':'2026-11-01','title':'运动','planned':20})[0],201)
        task=self.state()['tasks'][0];tid=task['id']
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'actual':15,'done':True,'note':'已完成'})[0],200)
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'title':'运动更新'})[0],200)
        task=self.state()['tasks'][0]
        self.assertEqual((task['actual'],task['done'],task['note']),(15,1,'已完成'))
        self.assertEqual(self.req(f'/tasks/{tid}','PATCH',{'done':False})[0],200)
        task=self.state()['tasks'][0]
        self.assertEqual((task['actual'],task['done'],task['note']),(15,0,'已完成'))

    def test_new_task_keeps_course_note_and_progress(self):
        payload={'day':'2026-11-01','title':'手动安排一节课程','planned':30,'note':'课程：Python；课时：001；由我自行安排','actual':12,'done':True}
        self.assertEqual(self.req('/tasks','POST',payload)[0],201)
        task=self.state()['tasks'][0]
        self.assertEqual((task['note'],task['actual'],task['done'],task['created_source']),(payload['note'],12,1,'manual'))
        for changes in [{'note':'x'*10001},{'actual':1441},{'actual':-1},{'done':'false'}]:
            with self.subTest(changes=list(changes)):
                self.assertEqual(self.req('/tasks','POST',{**payload,**changes})[0],400)

    def test_legacy_archive_preserves_user_work_and_restore(self):
        with app.conn() as c:
            user=c.execute('SELECT * FROM users WHERE username=?',(self.username,)).fetchone()
            app.seed(c,user['id'],date.fromisoformat(user['start']),user['minutes'])
            tasks=c.execute('SELECT id FROM tasks WHERE user_id=? ORDER BY id',(user['id'],)).fetchall()
            c.execute('UPDATE tasks SET done=1 WHERE id=?',(tasks[0]['id'],))
            c.execute('UPDATE tasks SET actual=5 WHERE id=?',(tasks[1]['id'],))
            c.execute("UPDATE tasks SET note='学习疑问' WHERE id=?",(tasks[2]['id'],))
            c.execute("INSERT INTO tasks(user_id,day,title,category,planned) VALUES(?,?,?,?,?)",(user['id'],user['start'],'自己安排','生活',20))
        self.assertEqual(len(self.state()['tasks']),253)
        status,result=self.req('/tasks/archive-template','POST',{})
        self.assertEqual((status,result['archived']),(200,249))
        state=self.state();self.assertEqual(len(state['tasks']),4);self.assertEqual(state['archived_task_count'],249)
        self.assertEqual(self.req('/tasks/archive-template','POST',{})[1]['archived'],0)
        self.assertEqual(self.req('/tasks/restore-archived','POST',{})[1]['restored'],249)
        self.assertEqual(len(self.state()['tasks']),253)

if __name__=='__main__':unittest.main(verbosity=2)
