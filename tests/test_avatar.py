"""Avatar validation and isolation use only temporary accounts and SQLite files."""
import base64
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('avatar_backend',ROOT/'backend/server.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
PREFIX='data:image/jpeg;base64,'
# A complete 2x2 RGB JPEG test fixture; no image library is needed at runtime.
JPEG=base64.b64decode(
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDzyiiig+eP/9k=')

def data_url(image):return PREFIX+base64.b64encode(image).decode('ascii')
AVATAR=data_url(JPEG)

class AvatarTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();app.DB=Path(self.temp.name)/'study.db';app.init()
        self.token=secrets.token_hex(16);self.other_token=secrets.token_hex(16)
        with app.conn() as connection:
            for uid,token in [(1,self.token),(2,self.other_token)]:
                connection.execute('INSERT INTO users(id,username,name,password,salt,start) VALUES(?,?,?,?,?,?)',
                    (uid,'avatar-test-'+str(uid),'临时头像测试','test-only-hash','test-only-salt','2026-11-02'))
                connection.execute('INSERT INTO sessions VALUES(?,?,?)',(token,uid,time.time()+3600))
            connection.execute('INSERT INTO tasks(user_id,day,title,category,planned,note) VALUES(?,?,?,?,?,?)',
                (1,'2026-11-02','保留自己的任务','学习',30,'头像操作不得修改任务'))
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)+'/api'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def req(self,path='/avatar',method='PATCH',data=None,token='owner'):
        if token=='owner':token=self.token
        headers={'Content-Type':'application/json'}
        if token:headers['Cookie']='session='+token
        request=urllib.request.Request(self.base+path,method=method,headers=headers,
            data=json.dumps(data).encode('utf-8') if data is not None else None)
        try:
            with urllib.request.urlopen(request) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as response:
            with response:return response.code,json.load(response)

    def state(self,token='owner'):return self.req('/state','GET',token=token)[1]

    def test_valid_small_jpeg_roundtrip_is_persistent_and_tasks_are_unchanged(self):
        before=self.state()['tasks']
        self.assertEqual(app.jpeg_dimensions(JPEG),(2,2))
        self.assertEqual(self.req(data={'avatar':AVATAR}),(200,{'avatar':AVATAR}))
        app.init()
        self.assertEqual(self.state()['user']['avatar'],AVATAR)
        self.assertEqual(self.state()['tasks'],before)

    def test_authentication_is_required_before_avatar_changes(self):
        for token in (None,'invalid-session'):
            self.assertEqual(self.req(data={'avatar':AVATAR},token=token)[0],401)
        with app.conn() as connection:
            self.assertEqual([row['avatar'] for row in connection.execute('SELECT avatar FROM users ORDER BY id')],['',''])

    def test_other_accounts_and_explicit_user_ids_cannot_be_modified(self):
        before=self.state()['tasks']
        self.assertEqual(self.req(data={'avatar':AVATAR,'user_id':2})[0],400)
        self.assertEqual(self.req(data={'avatar':AVATAR})[0],200)
        self.assertEqual(self.state(token=self.other_token)['user']['avatar'],'')
        self.assertEqual(self.req(data={'avatar':AVATAR},token=self.other_token)[0],200)
        self.assertEqual(self.req(data={'avatar':''})[0],200)
        self.assertEqual(self.state()['user']['avatar'],'')
        self.assertEqual(self.state(token=self.other_token)['user']['avatar'],AVATAR)
        self.assertEqual(self.state()['tasks'],before)

    def test_empty_string_removes_only_the_current_avatar(self):
        self.assertEqual(self.req(data={'avatar':AVATAR})[0],200)
        self.assertEqual(self.req(data={'avatar':''}),(200,{'avatar':''}))
        self.assertEqual(self.state()['user']['avatar'],'')

    def test_malformed_and_non_jpeg_inputs_preserve_the_previous_avatar(self):
        self.assertEqual(self.req(data={'avatar':AVATAR})[0],200)
        invalid=[None,123,[],{},'https://example.com/avatar.jpg','data:image/svg+xml;base64,PHN2Zy8+',
                 'data:image/png;base64,iVBORw0KGgo=',PREFIX+'@@@',PREFIX+'非ASCII',
                 data_url(b'not a jpeg'),data_url(JPEG[:-2]),data_url(JPEG+JPEG),
                 data_url(b'\xff\xd8\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00\xff\xd9')]
        for value in invalid:
            with self.subTest(value=str(value)[:50]):
                self.assertEqual(self.req(data={'avatar':value})[0],400)
                self.assertEqual(self.state()['user']['avatar'],AVATAR)

    def test_encoded_decoded_and_actual_dimension_limits(self):
        self.assertEqual(self.req(data={'avatar':AVATAR})[0],200)
        frame=JPEG.index(b'\xff\xc0')
        wide=bytearray(JPEG);wide[frame+7:frame+9]=(513).to_bytes(2,'big')
        empty=bytearray(JPEG);empty[frame+5:frame+7]=b'\0\0'
        invalid=[PREFIX+'A'*54000,data_url(b'\xff\xd8'+b'\0'*40000+b'\xff\xd9'),data_url(wide),data_url(empty)]
        for value in invalid:
            with self.subTest(length=len(value)):
                self.assertEqual(self.req(data={'avatar':value})[0],400)
                self.assertEqual(self.state()['user']['avatar'],AVATAR)

    def test_only_patch_and_an_exact_avatar_field_are_accepted(self):
        for method in ('GET','POST','DELETE'):
            self.assertEqual(self.req(method=method,data=None)[0],405)
        for value in ({},{'avatar':AVATAR,'name':'不要修改名称'},[],None):
            self.assertEqual(self.req(data=value)[0],400)
        self.assertEqual(self.state()['user']['name'],'临时头像测试')

    def test_old_users_gain_avatar_without_password_or_existing_field_changes(self):
        current=app.DB;legacy=Path(self.temp.name)/'legacy.db'
        with closing(sqlite3.connect(legacy)) as connection:
            connection.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,name TEXT,password TEXT,salt TEXT,minutes INTEGER DEFAULT 180,start TEXT,custom_note TEXT)')
            connection.execute('INSERT INTO users VALUES(1,?,?,?,?,?,?,?)',
                ('legacy-user','保留称呼','old-hash-unchanged','old-salt-unchanged',210,'2026-10-01','保留额外字段'))
            connection.commit()
        app.DB=legacy
        try:
            app.init();app.init()
            with app.conn() as connection:
                row=dict(connection.execute('SELECT * FROM users WHERE id=1').fetchone())
                self.assertEqual(row,{'id':1,'username':'legacy-user','name':'保留称呼','password':'old-hash-unchanged',
                    'salt':'old-salt-unchanged','minutes':210,'start':'2026-10-01','custom_note':'保留额外字段','avatar':''})
                connection.execute('UPDATE users SET avatar=? WHERE id=1',(AVATAR,))
            app.init()
            with app.conn() as connection:
                after=dict(connection.execute('SELECT * FROM users WHERE id=1').fetchone())
                self.assertEqual(after,{**row,'avatar':AVATAR})
        finally:app.DB=current

if __name__=='__main__':unittest.main(verbosity=2)
