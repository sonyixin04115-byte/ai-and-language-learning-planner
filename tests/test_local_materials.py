import importlib.util,json,tempfile,threading,unittest,urllib.request,urllib.error,http.cookiejar,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import server,materials,import_materials

class LocalMaterialsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.basepath=Path(cls.tmp.name)
        cls.root=cls.basepath/'resources';cls.folder=cls.root/'课程集合'/'测试课程';cls.folder.mkdir(parents=True)
        (cls.folder/'001_入门.mp4').write_bytes(b'0123456789abcdefghijklmnopqrstuvwxyz')
        (cls.folder/'002_笔记.md').write_text('课程笔记',encoding='utf-8')
        (cls.folder/'003_安装.exe').write_bytes(b'do not execute')
        (cls.folder/'004_代码.zip').write_bytes(b'archive')
        (cls.basepath/'outside.txt').write_text('outside',encoding='utf-8')
        server.DB=cls.basepath/'study.db';server.init()
        materials.CATALOG_PATH=cls.basepath/'catalog.json'
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)+'/api'
        cls.owner=cls.client();cls.other=cls.client();cls.anon=cls.client()
        for username,c in [('owner',cls.owner),('other',cls.other)]:
            cls.request('/register',method='POST',data={'username':username,'password':'test-password'},client=c)
        first=import_materials.import_for_user('owner',cls.root);second=import_materials.import_for_user('owner',cls.root)
        assert first==second=={'courses':1,'videos':1,'materials':3}
        cls.state=cls.json_request('/state',client=cls.owner)
        cls.course=next(c for c in cls.state['courses'] if c['is_local'])
        cls.lessons=cls.json_request(f"/courses/{cls.course['id']}/lessons",client=cls.owner)['lessons']
        cls.video=next(l for l in cls.lessons if l['kind']=='video')

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join();cls.tmp.cleanup()

    @staticmethod
    def client():return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    @classmethod
    def request(cls,path,method='GET',data=None,headers=None,client=None):
        req=urllib.request.Request(cls.base+path,method=method,data=json.dumps(data).encode() if data is not None else None,headers=headers or {})
        try:
            with (client or cls.anon).open(req) as response:return response.status,dict(response.headers),response.read()
        except urllib.error.HTTPError as response:
            with response:return response.code,dict(response.headers),response.read()

    @classmethod
    def json_request(cls,path,**kwargs):return json.loads(cls.request(path,**kwargs)[2])

    def test_catalog_metadata_and_idempotent_import(self):
        self.assertEqual((self.course['lesson_count'],self.course['video_count'],self.course['available']),(3,1,True))
        self.assertEqual(len([c for c in self.state['courses'] if c['is_local']]),1)
        self.assertEqual(self.state['tasks'],[])
        self.assertTrue(all('path' not in l for l in self.lessons))

    def test_auth_and_ownership(self):
        path='/materials/'+self.video['id']
        self.assertEqual(self.request(path)[0],401)
        self.assertEqual(self.request(path,client=self.other)[0],404)
        self.assertEqual(self.request(f"/courses/{self.course['id']}/lessons",client=self.other)[0],404)
        self.assertEqual(self.request('/materials/not-registered',client=self.owner)[0],404)

    def test_video_ranges_and_head(self):
        path='/materials/'+self.video['id']
        status,headers,body=self.request(path,client=self.owner,headers={'Range':'bytes=2-5'})
        self.assertEqual((status,body),(206,b'2345'));self.assertEqual(headers['Content-Range'],'bytes 2-5/36')
        self.assertEqual(self.request(path,client=self.owner,headers={'Range':'bytes=-3'})[2],b'xyz')
        self.assertEqual(self.request(path,client=self.owner,headers={'Range':'bytes=34-'})[2],b'yz')
        for value in ['bytes=99-','bytes=5-2','bytes=-0','bytes=0-1,4-5','broken']:
            with self.subTest(value=value):self.assertEqual(self.request(path,client=self.owner,headers={'Range':value})[0],416)
        status,headers,body=self.request(path,method='HEAD',client=self.owner)
        self.assertEqual((status,headers['Content-Length'],body),(200,'36',b''))

    def test_attachment_no_sniff_and_traversal(self):
        archive=next(l for l in self.lessons if l['kind']=='archive')
        status,headers,body=self.request('/materials/'+archive['id'],client=self.owner)
        self.assertEqual(status,200);self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        data=json.loads(materials.CATALOG_PATH.read_text(encoding='utf-8'))
        data['courses'][0]['lessons'].append({'id':'traversal','title':'bad','path':'../outside.txt','kind':'text','size':7,'extension':'.txt','section':'bad'})
        materials.CATALOG_PATH.write_text(json.dumps(data),encoding='utf-8')
        self.assertEqual(self.request('/materials/traversal',client=self.owner)[0],404)

if __name__=='__main__':unittest.main(verbosity=2)
