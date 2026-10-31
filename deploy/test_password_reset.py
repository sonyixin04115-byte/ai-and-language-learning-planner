import hashlib
from http.cookies import SimpleCookie
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from urllib.parse import urlencode,urlsplit,parse_qs
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator
from wsgi_app import create_app
from create_password_reset import create_request

ORIGIN='https://reset-test.pythonanywhere.com'

class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'study.db'
        self.app=validator(create_app(self.db,ORIGIN,True))
        self.token='testing-token-that-is-long-enough-12345678'
        status,headers,_=self.request('/api/register','POST',{'username':'yixin','password':'Original123','name':'测试'})
        self.assertEqual(status,200)
        parsed=SimpleCookie();parsed.load(headers['Set-Cookie']);self.cookie='session='+parsed['session'].value
        self.request('/api/tasks','POST',{'day':'2026-11-01','title':'保留我的任务','planned':30,'note':'保留笔记'},cookie=self.cookie)
        self.request_file=self.db.parent/'password-reset.json'
        self.configure()
    def tearDown(self):self.temp.cleanup()
    def configure(self,username='yixin',expires=None):
        self.request_file.write_text(json.dumps({'username':username,'token_hash':hashlib.sha256(self.token.encode()).hexdigest(),'expires':time.time()+900 if expires is None else expires}),encoding='utf-8')
    def request(self,path,method='GET',data=None,cookie=None,form=False,origin=ORIGIN):
        raw=(urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else b''
        url=urlsplit(path);env={};setup_testing_defaults(env)
        env.update(PATH_INFO=url.path,QUERY_STRING=url.query,REQUEST_METHOD=method,HTTP_HOST=ORIGIN.removeprefix('https://'),CONTENT_LENGTH=str(len(raw)),CONTENT_TYPE='application/x-www-form-urlencoded' if form else 'application/json',REMOTE_ADDR='127.0.0.1',**{'wsgi.input':io.BytesIO(raw),'wsgi.url_scheme':'https'})
        if origin:env['HTTP_ORIGIN']=origin
        if cookie:env['HTTP_COOKIE']=cookie
        status=[];headers=[]
        def respond(s,h,exc_info=None):status.append(s);headers.extend(h)
        result=self.app(env,respond)
        try:body=b''.join(result)
        finally:
            if hasattr(result,'close'):result.close()
        return int(status[0].split()[0]),dict(headers),body
    def reset(self,**kwargs):
        return self.request('/reset-password','POST',{'token':self.token,'password':'NewPassword123','confirm_password':'NewPassword123',**kwargs},form=True)
    def test_reset_preserves_data_revokes_sessions_and_one_time(self):
        status,headers,body=self.request('/reset-password?token='+self.token)
        self.assertEqual(status,200);self.assertEqual(headers['Cache-Control'],'no-store');self.assertEqual(headers['Referrer-Policy'],'same-origin')
        self.assertIn(b'<form method="post" action="/reset-password">',body)
        self.assertNotIn(b'Original123',body)
        status,headers,_=self.reset()
        self.assertEqual(status,200);self.assertEqual(headers['Referrer-Policy'],'no-referrer')
        self.assertEqual(self.reset()[0],403)
        status,headers,_=self.request('/reset-password?token='+self.token)
        self.assertEqual(status,403);self.assertEqual(headers['Referrer-Policy'],'no-referrer')
        self.assertEqual(self.request('/api/state',cookie=self.cookie)[0],401)
        self.assertEqual(self.request('/api/login','POST',{'username':'yixin','password':'Original123'})[0],401)
        status,headers,_=self.request('/api/login','POST',{'username':'yixin','password':'NewPassword123'})
        self.assertEqual(status,200);parsed=SimpleCookie();parsed.load(headers['Set-Cookie'])
        data=json.loads(self.request('/api/state',cookie='session='+parsed['session'].value)[2])
        self.assertEqual(data['tasks'][0]['note'],'保留笔记')
    def test_wrong_expired_unknown_missing_requests(self):
        self.assertEqual(self.request('/reset-password?token='+'z'*43)[0],403)
        self.configure(expires=time.time()-1);self.assertEqual(self.reset()[0],403)
        self.configure(username='unknown');self.assertEqual(self.reset()[0],403)
        self.request_file.unlink();self.assertEqual(self.reset()[0],403)
    def test_validation_does_not_consume_link_and_requires_origin(self):
        self.assertEqual(self.reset(confirm_password='different')[0],400)
        self.assertEqual(self.reset(password='short',confirm_password='short')[0],400)
        for untrusted_origin in (None,'null','https://external.example'):
            with self.subTest(origin=untrusted_origin):
                self.assertEqual(self.request('/reset-password','POST',{'token':self.token,'password':'NewPassword123','confirm_password':'NewPassword123'},form=True,origin=untrusted_origin)[0],403)
        self.assertEqual(self.reset()[0],200)
    def test_helper_stores_no_raw_token(self):
        url=create_request('yixin',ORIGIN,self.request_file)
        token=parse_qs(urlsplit(url).query)['token'][0]
        data=self.request_file.read_text(encoding='utf-8');self.assertNotIn(token,data)
        self.assertEqual(self.request('/reset-password?token='+token)[0],200)
        self.assertEqual(self.request('/data/password-reset.json')[0],404)
    def test_head_invalid_reset_no_body(self):
        status,_,body=self.request('/reset-password',method='HEAD')
        self.assertEqual(status,403);self.assertEqual(body,b'')

if __name__=='__main__':unittest.main(verbosity=2)
