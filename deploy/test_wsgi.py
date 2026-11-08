"""No web accounts, external servers, or real user database are used."""
from http.cookies import SimpleCookie
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

from wsgi_app import create_app

ORIGIN="https://test-student.pythonanywhere.com"


class WSGITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Path(self.temp.name)/"data/study.db"
        self.app=validator(create_app(self.db,ORIGIN,allow_registration=True))

    def tearDown(self):
        self.temp.cleanup()

    def request(self,path,method="GET",data=None,cookie=None,origin=ORIGIN,host=None,length=None,app=None,content_type="application/json"):
        raw=json.dumps(data).encode() if data is not None else b""
        environ={};setup_testing_defaults(environ)
        parsed_path=urlsplit(path)
        environ.update(PATH_INFO=parsed_path.path,QUERY_STRING=parsed_path.query,REQUEST_METHOD=method,HTTP_HOST=host or ORIGIN.removeprefix('https://'),CONTENT_LENGTH=str(len(raw) if length is None else length),CONTENT_TYPE=content_type,REMOTE_ADDR="127.0.0.1",**{"wsgi.input":io.BytesIO(raw),"wsgi.url_scheme":"https"})
        if origin:environ['HTTP_ORIGIN']=origin
        if cookie:environ['HTTP_COOKIE']=cookie
        status=[];headers=[]
        def start_response(code,values,exc_info=None):status.append(code);headers.extend(values)
        stream=(app or self.app)(environ,start_response)
        try:body=b''.join(stream)
        finally:
            if hasattr(stream,'close'):stream.close()
        return int(status[0].split()[0]),dict(headers),body

    def signup(self):
        code,headers,_=self.request('/api/register','POST',{'username':'test-student','password':'TestingOnly123','name':'测试'})
        self.assertEqual(code,200)
        parsed=SimpleCookie();parsed.load(headers['Set-Cookie'])
        return 'session='+parsed['session'].value,headers

    def test_same_origin_static_and_private_paths(self):
        status,headers,body=self.request('/')
        self.assertEqual(status,200);self.assertIn('人工智能学习计划'.encode(),body)
        status,headers,body=self.request('/app.js')
        self.assertEqual(status,200);self.assertIn(b"const API='/api';",body);self.assertNotIn(b'const API=\'http://127.0.0.1',body)
        for path in ['/data/study.db','/../backend/study.db','/deploy/wsgi_app.py','/backend/materials_catalog.json']:
            self.assertEqual(self.request(path)[0],404)
        self.assertEqual(self.request('/api/state')[0],401)

    def test_origin_host_length_and_config_guards(self):
        self.assertEqual(self.request('/api/health',origin='https://evil.example')[0],403)
        self.assertEqual(self.request('/api/health',host='evil.example')[0],400)
        self.assertEqual(self.request('/api/login','POST',{},length=65537)[0],413)
        with self.assertRaises(ValueError):create_app(self.db,'http://test.example')
        with self.assertRaises(ValueError):create_app('relative.db',ORIGIN)

    def test_cookies_task_persistence_and_new_process(self):
        cookie,headers=self.signup()
        self.assertIn('Secure',headers['Set-Cookie']);self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        status,_,body=self.request('/api/state',cookie=cookie)
        data=json.loads(body);self.assertEqual(status,200);self.assertEqual(data['runtime'],'cloud');self.assertEqual(data['tasks'],[])
        self.assertEqual(self.request('/api/tasks','POST',{'day':'2026-11-01','title':'自选任务','category':'其他','planned':25,'note':'自己选择的笔记'},cookie)[0],201)
        restarted=validator(create_app(self.db,ORIGIN))
        status,_,body=self.request('/api/state',cookie=cookie,app=restarted)
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['tasks'][0]['note'],'自己选择的笔记')
        self.assertEqual(self.request('/api/register','POST',{'username':'new-one','password':'ValidPassword'},app=restarted)[0],403)

    def test_cookie_token_text_cannot_replace_secure_attribute(self):
        with patch('secrets.token_urlsafe',return_value='testing-secure-token'):
            _,headers=self.signup()
        parsed=SimpleCookie();parsed.load(headers['Set-Cookie'])
        self.assertTrue(parsed['session']['secure'])

    def test_rest_day_and_single_task_reset_persist_through_wsgi_restart(self):
        cookie,_=self.signup()
        payload={'day':'2026-11-08','title':'云端自选任务','planned':30,'category':'学习','note':'保留云端笔记','started_at':'2026-11-01T01:00:00Z'}
        self.assertEqual(self.request('/api/tasks','POST',payload,cookie)[0],201)
        code,_,body=self.request('/api/state',cookie=cookie)
        self.assertEqual(code,200)
        task=json.loads(body)['tasks'][0];tid=task['id']
        code,_,body=self.request('/api/days/rest','POST',{'day':'2026-11-08','rest':True},cookie)
        self.assertEqual(code,200)
        self.assertEqual(json.loads(body),{'ok':True,'rest':True,'paused_tasks':1})
        restarted=validator(create_app(self.db,ORIGIN))
        code,_,body=self.request('/api/state',cookie=cookie,app=restarted)
        state=json.loads(body)
        self.assertEqual((code,state['runtime'],state['rest_days']),(200,'cloud',['2026-11-08']))
        self.assertEqual(len(state['tasks'][0]['pause_intervals']),1)
        self.assertIsNone(state['tasks'][0]['pause_intervals'][0]['finished_at'])
        self.assertEqual(self.request('/api/days/rest','POST',{'day':'2026-11-08','rest':False},cookie,app=restarted)[0],200)
        _,_,body=self.request('/api/state',cookie=cookie,app=restarted)
        self.assertEqual(len(json.loads(body)['tasks'][0]['pause_intervals']),1)
        self.assertEqual(self.request(f'/api/tasks/{tid}','PATCH',{'action':'reset'},cookie,app=restarted)[0],200)
        final=validator(create_app(self.db,ORIGIN))
        code,_,body=self.request('/api/state',cookie=cookie,app=final)
        state=json.loads(body);saved=state['tasks'][0]
        self.assertEqual((code,state['rest_days']),(200,[]))
        self.assertEqual((saved['started_at'],saved['finished_at'],saved['actual'],saved['pause_intervals']),(None,None,0,[]))
        for field in ('title','day','category','planned','note','done'):
            self.assertEqual(saved[field],task[field])

    def test_rest_day_wsgi_auth_and_methods_do_not_write(self):
        cookie,_=self.signup()
        data={'day':'2026-11-08','rest':True}
        self.assertEqual(self.request('/api/days/rest','POST',data)[0],401)
        for method in ('GET','HEAD','PATCH','DELETE'):
            code,_,body=self.request('/api/days/rest',method,data,cookie)
            self.assertEqual(code,405)
            if method=='HEAD':self.assertEqual(body,b'')
        self.assertEqual(self.request('/api/days/rest','POST',{'day':'20261108','rest':True},cookie)[0],400)
        self.assertEqual(self.request('/api/days/rest','POST',{'day':'2026-11-08','rest':1},cookie)[0],400)
        _,_,body=self.request('/api/state',cookie=cookie)
        self.assertEqual(json.loads(body)['rest_days'],[])

    def test_manual_login_requires_password_after_a_new_document(self):
        cookie,headers=self.signup()
        parsed=SimpleCookie();parsed.load(headers['Set-Cookie'])
        self.assertEqual(parsed['session']['max-age'],'')
        self.assertEqual(self.request('/api/state',cookie=cookie)[0],200)
        status,headers,body=self.request('/api/session/start','POST',cookie=cookie)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body),{'automatic':False})
        self.assertIn('Max-Age=0',headers['Set-Cookie'])
        self.assertEqual(self.request('/api/state',cookie=cookie)[0],401)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM sessions').fetchone()[0],0)

    def test_automatic_login_is_explicit_and_server_verified(self):
        self.signup()
        for value in ('true',1,[],None):
            status,_,_=self.request('/api/login','POST',{'username':'test-student','password':'TestingOnly123','auto_login':value})
            self.assertEqual(status,400)
        status,headers,_=self.request('/api/login','POST',{'username':'test-student','password':'TestingOnly123','auto_login':True})
        self.assertEqual(status,200)
        parsed=SimpleCookie();parsed.load(headers['Set-Cookie'])
        self.assertEqual(parsed['session']['max-age'],'604800')
        cookie='session='+parsed['session'].value
        for _ in range(2):
            status,_,body=self.request('/api/session/start','POST',cookie=cookie)
            self.assertEqual((status,json.loads(body)),(200,{'automatic':True}))
            self.assertEqual(self.request('/api/state',cookie=cookie)[0],200)
        forged='session=auto.'+parsed['session'].value.removeprefix('auto.')+'tampered'
        status,_,body=self.request('/api/session/start','POST',cookie=forged)
        self.assertEqual((status,json.loads(body)),(200,{'automatic':False}))

    def test_legacy_and_expired_auto_tokens_do_not_restore(self):
        self.signup()
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('INSERT INTO sessions VALUES(?,?,?)',('legacy-token',1,9999999999))
            c.execute('INSERT INTO sessions VALUES(?,?,?)',('auto.expired-token',1,1))
        for token in ('legacy-token','auto.expired-token'):
            status,_,body=self.request('/api/session/start','POST',cookie='session='+token)
            self.assertEqual((status,json.loads(body)),(200,{'automatic':False}))
            self.assertEqual(self.request('/api/state',cookie='session='+token)[0],401)
            with closing(sqlite3.connect(self.db)) as c:
                self.assertEqual(c.execute('SELECT count(*) FROM sessions WHERE token=?',(token,)).fetchone()[0],0)

    def test_turning_off_auto_replaces_only_this_device_and_logout_is_idempotent(self):
        self.signup()
        def login(automatic,cookie=None):
            status,headers,_=self.request('/api/login','POST',{'username':'test-student','password':'TestingOnly123','auto_login':automatic},cookie=cookie)
            self.assertEqual(status,200)
            parsed=SimpleCookie();parsed.load(headers['Set-Cookie'])
            return 'session='+parsed['session'].value,parsed['session']['max-age']
        device_a,_=login(True)
        device_b,_=login(True)
        manual,max_age=login(False,device_a)
        self.assertEqual(max_age,'')
        self.assertEqual(self.request('/api/state',cookie=device_a)[0],401)
        self.assertEqual(self.request('/api/state',cookie=manual)[0],200)
        self.assertEqual(self.request('/api/state',cookie=device_b)[0],200)
        for _ in range(2):
            status,headers,_=self.request('/api/logout','POST',cookie=manual)
            self.assertEqual(status,200)
            self.assertIn('Max-Age=0',headers['Set-Cookie'])
        self.assertEqual(self.request('/api/state',cookie=manual)[0],401)
        self.assertEqual(self.request('/api/state',cookie=device_b)[0],200)
        self.assertEqual(self.request('/api/logout','POST')[0],200)

    def test_language_query_parameters_reach_the_shared_backend_and_persist(self):
        cookie,_=self.signup()
        url='/api/language-practice?language=english&level=B2&day=2026-11-02&direction=to_zh'
        status,_,body=self.request(url,cookie=cookie)
        result=json.loads(body)
        self.assertEqual(status,200,result)
        self.assertEqual((result['language'],result['level'],result['day'],result['direction']),
                         ('english','B2','2026-11-02','to_zh'))
        self.assertEqual(len(result['items']),5)
        item=result['items'][0]
        payload={'language':'english','level':'B2','day':item['day'],'direction':'to_zh',
                 'exercise_id':item['id'],'answer':'My private WSGI answer','review':True}
        self.assertEqual(self.request('/api/language-practice','PATCH',payload,cookie)[0],200)
        restarted=validator(create_app(self.db,ORIGIN))
        status,_,body=self.request(url,cookie=cookie,app=restarted)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['items'][0]['answer'],'My private WSGI answer')
        status,_,body=self.request('/api/language-practice/review?language=english&level=B1',cookie=cookie,app=restarted)
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['items'],[])
        status,_,body=self.request('/api/language-practice?language=korean&level=6&day=2026-11-03&direction=to_target',cookie=cookie,app=restarted)
        self.assertEqual(status,200)
        self.assertEqual((json.loads(body)['language'],json.loads(body)['level']),('korean','6'))

    def test_pythonanywhere_blank_content_length_after_login(self):
        # PythonAnywhere's uWSGI environment uses blank metadata for GETs.
        self.assertEqual(self.request('/api/health',length='',content_type='')[0],200)
        self.assertEqual(self.request('/api/state',length='',content_type='')[0],401)
        self.signup()
        status,headers,_=self.request('/api/login','POST',{'username':'test-student','password':'TestingOnly123'})
        self.assertEqual(status,200)
        parsed=SimpleCookie();parsed.load(headers['Set-Cookie'])
        cookie='session='+parsed['session'].value
        status,_,body=self.request('/api/state',cookie=cookie,length='',content_type='')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['user']['username'],'test-student')
        self.assertEqual(json.loads(body)['runtime'],'cloud')

    def test_head_success_and_errors_have_no_body(self):
        for path,expected in [('/',200),('/api/health',200),('/data/study.db',404),('/api/state',401),('/api/materials/missing',409)]:
            status,headers,body=self.request(path,method='HEAD')
            self.assertEqual(status,expected)
            self.assertEqual(body,b'')
            self.assertGreater(int(headers['Content-Length']),0)

    def test_course_metadata_no_cloud_video_stream(self):
        cookie,_=self.signup()
        status,_,body=self.request('/api/state',cookie=cookie)
        self.assertEqual(status,200);self.assertIn('视频',json.loads(body)['storage_note'])
        self.assertEqual(self.request('/api/materials/not-a-real-id',cookie=cookie)[0],409)

    def test_login_limit(self):
        for _ in range(20):
            status,_,_=self.request('/api/login','POST',{'username':'nobody','password':'not-the-password'})
            self.assertEqual(status,401)
        status,headers,_=self.request('/api/login','POST',{'username':'nobody','password':'not-the-password'})
        self.assertEqual(status,429);self.assertEqual(headers['Retry-After'],'60')


if __name__=='__main__':unittest.main(verbosity=2)
