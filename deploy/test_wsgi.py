"""No web accounts, external servers, or real user database are used."""
from http.cookies import SimpleCookie
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
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

    def request(self,path,method="GET",data=None,cookie=None,origin=ORIGIN,host=None,length=None,app=None):
        raw=json.dumps(data).encode() if data is not None else b""
        environ={};setup_testing_defaults(environ)
        environ.update(PATH_INFO=path,QUERY_STRING='',REQUEST_METHOD=method,HTTP_HOST=host or ORIGIN.removeprefix('https://'),CONTENT_LENGTH=str(len(raw) if length is None else length),CONTENT_TYPE="application/json",REMOTE_ADDR="127.0.0.1",**{"wsgi.input":io.BytesIO(raw),"wsgi.url_scheme":"https"})
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
        self.assertEqual(status,200);self.assertIn('知序'.encode(),body)
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
