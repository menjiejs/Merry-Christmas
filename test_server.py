import unittest, tempfile, threading, json, urllib.request, urllib.error, http.cookiejar
from server import make_server

class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = make_server('127.0.0.1', 0, self.temp.name + '/test.db')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.a = self.client(); self.b = self.client()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.temp.cleanup()
    def client(self):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(self, client, path, data=None):
        req = urllib.request.Request(self.base+path, data=json.dumps(data).encode() if data is not None else None, headers={'Content-Type':'application/json','Origin':self.base})
        try:
            with client.open(req) as r: return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            with e: return e.code, json.load(e)
    def tree(self):
        status, result = self.request(self.a, '/api/trees', {'name':'小鹿'})
        self.assertEqual(status,201); return result['id']
    def gift(self, client, tree, point=0):
        return self.request(client, '/api/trees/'+tree+'/ornaments', {'point':point,'ornament':'star','name':'朋友','text':'圣诞快乐'})
    def test_create_and_shared_read(self):
        tree=self.tree(); self.assertEqual(self.gift(self.a,tree)[0],201)
        _,data=self.request(self.b,'/api/trees/'+tree)
        self.assertEqual(data['name'],'小鹿'); self.assertEqual(len(data['ornaments']),1)
        self.assertFalse(data['hasGifted']); self.assertNotIn('visitor',json.dumps(data))
        self.assertTrue(self.request(self.a,'/api/trees/'+tree)[1]['hasGifted'])
    def test_duplicate_and_point_conflict(self):
        tree=self.tree(); self.gift(self.a,tree)
        self.assertEqual(self.gift(self.a,tree,1)[0],201)
        self.assertEqual(self.gift(self.b,tree)[1]['code'],'POINT_TAKEN')
        self.assertEqual(self.gift(self.b,tree,2)[0],201)
        self.assertEqual(self.gift(self.a,self.tree())[0],201)
    def test_invalid_and_missing(self):
        self.assertEqual(self.request(self.a,'/api/trees',{'name':' '})[0],422)
        self.assertEqual(self.request(self.a,'/api/trees/missing')[0],404)
        tree=self.tree()
        for patch in [{'point':38},{'point':True},{'ornament':'__proto__'},{'text':' '},{'name':'x'*25}]:
            data={'point':0,'ornament':'star','name':'朋友','text':'你好'}; data.update(patch)
            self.assertEqual(self.request(self.a,'/api/trees/'+tree+'/ornaments',data)[0],422)
    def test_concurrent_point_claim(self):
        from concurrent.futures import ThreadPoolExecutor
        tree=self.tree()
        # 先让两位访客获得各自 Cookie，再同时提交同一个挂点。
        self.request(self.a,'/api/trees/'+tree); self.request(self.b,'/api/trees/'+tree)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda c:self.gift(c,tree),[self.a,self.b]))
        self.assertEqual(sorted(r[0] for r in results),[201,409])
        self.assertEqual(len(self.request(self.a,'/api/trees/'+tree)[1]['ornaments']),1)
    def test_persistence_on_restart(self):
        tree=self.tree(); self.gift(self.a,tree)
        self.server.shutdown(); self.server.server_close()
        self.server=make_server('127.0.0.1',0,self.temp.name+'/test.db')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        _,data=self.request(self.a,'/api/trees/'+tree)
        self.assertEqual(len(data['ornaments']),1); self.assertTrue(data['hasGifted'])
    def test_cross_site_rejected(self):
        req=urllib.request.Request(self.base+'/api/trees',data=b'{"name":"test"}',headers={'Content-Type':'application/json','Origin':'https://example.invalid'})
        with self.assertRaises(urllib.error.HTTPError) as caught: self.a.open(req)
        self.assertEqual(caught.exception.code,403); caught.exception.close()
    def test_exclusive_ornament_permission(self):
        import sqlite3
        anchor=self.tree(); self.gift(self.a,anchor)
        with sqlite3.connect(self.temp.name+'/test.db') as db:
            visitor=db.execute('SELECT visitor FROM ornaments WHERE tree=?',(anchor,)).fetchone()[0]
            db.execute('INSERT INTO developer_browser VALUES (?,?)',(anchor,visitor))
        tree=self.tree()
        self.assertTrue(self.request(self.a,'/api/trees/'+tree)[1]['isDeveloper'])
        self.assertFalse(self.request(self.b,'/api/trees/'+tree)[1]['isDeveloper'])
        payload={'point':0,'ornament':'moon','name':'16nights','text':'月夜快乐'}
        self.assertEqual(self.request(self.b,'/api/trees/'+tree+'/ornaments',payload)[0],403)
        self.assertEqual(self.request(self.a,'/api/trees/'+tree+'/ornaments',payload)[0],201)
        self.assertEqual(self.request(self.b,'/api/trees/'+tree)[1]['ornaments'][0]['ornament'],'moon')
    def test_capacity_rejects_write_but_preserves_reads(self):
        self.server.get_app().max_trees=1
        tree=self.tree()
        self.assertEqual(self.request(self.a,'/api/trees',{'name':'第二棵'})[1]['code'],'CAPACITY')
        self.server.get_app().max_ornaments=1
        self.assertEqual(self.gift(self.a,tree)[0],201)
        self.assertEqual(self.gift(self.b,tree,1)[1]['code'],'CAPACITY')
        self.assertEqual(len(self.request(self.a,'/api/trees/'+tree)[1]['ornaments']),1)
    def test_busy_guard_rejects_without_database_write(self):
        app=self.server.get_app()
        for _ in range(8): app.slots.acquire()
        try: self.assertEqual(self.request(self.a,'/healthz')[0],503)
        finally:
            for _ in range(8): app.slots.release()
        self.assertEqual(self.request(self.a,'/healthz')[0],200)
    def test_owner_only_can_remove_message(self):
        tree=self.tree(); self.gift(self.b,tree)
        self.assertTrue(self.request(self.a,'/api/trees/'+tree)[1]['isOwner'])
        self.assertFalse(self.request(self.b,'/api/trees/'+tree)[1]['isOwner'])
        route='/api/trees/'+tree+'/ornaments/0/remove'
        self.assertEqual(self.request(self.b,route,{})[0],403)
        self.assertEqual(self.request(self.a,route,{})[0],200)
        self.assertEqual(self.request(self.a,'/api/trees/'+tree)[1]['ornaments'],[])
        self.assertEqual(self.gift(self.b,tree,1)[0],201)
    def test_audio_range(self):
        req=urllib.request.Request(self.base+'/assets/music/snowfall.mp3',headers={'Range':'bytes=0-99'})
        with self.a.open(req) as r:
            self.assertEqual(r.status,206); self.assertEqual(len(r.read()),100)
            self.assertEqual(r.headers['Content-Type'],'audio/mpeg')
        req=urllib.request.Request(self.base+'/assets/music/snowfall.mp3',headers={'Range':'bytes=999999999-'})
        with self.assertRaises(urllib.error.HTTPError) as caught: self.a.open(req)
        self.assertEqual(caught.exception.code,416); caught.exception.close()
    def test_owner_survives_restart(self):
        tree=self.tree(); self.gift(self.b,tree)
        self.server.shutdown(); self.server.server_close()
        self.server=make_server('127.0.0.1',0,self.temp.name+'/test.db')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.assertTrue(self.request(self.a,'/api/trees/'+tree)[1]['isOwner'])
        self.assertEqual(self.request(self.a,'/api/trees/'+tree+'/ornaments/0/remove',{})[0],200)
    def test_private_message_owner_sender_and_visitor(self):
        tree=self.tree()
        payload={'point':0,'ornament':'ball','name':'发送者','text':'只给你看的秘密','private':True}
        self.assertEqual(self.request(self.b,'/api/trees/'+tree+'/ornaments',payload)[0],201)
        for viewer in (self.a,self.b):
            status,data=self.request(viewer,'/api/trees/'+tree)
            self.assertEqual(status,200)
            entry=data['ornaments'][0]
            self.assertTrue(entry['canRead']); self.assertTrue(entry['private'])
            self.assertEqual(entry['text'],payload['text'])
        outsider=self.client()
        _,data=self.request(outsider,'/api/trees/'+tree)
        entry=data['ornaments'][0]
        self.assertFalse(entry['canRead']); self.assertIsNone(entry['text'])
        self.assertEqual(entry['name'],'发送者'); self.assertEqual(entry['ornament'],'ball')
        self.assertNotIn(payload['text'],json.dumps(data,ensure_ascii=False))
        self.assertNotIn('visitor',json.dumps(data)); self.assertNotIn('owner',entry)
        with outsider.open(self.base+'/api/trees/'+tree) as response:
            self.assertIn('no-store',response.headers['Cache-Control'])
    def test_owner_can_make_public_message_private(self):
        tree=self.tree(); self.gift(self.b,tree)
        outsider=self.client(); path='/api/trees/'+tree
        self.assertEqual(self.request(outsider,path)[1]['ornaments'][0]['text'],'圣诞快乐')
        route=path+'/ornaments/0/privacy'
        for viewer in (self.b,outsider):
            self.assertEqual(self.request(viewer,route,{'private':True})[0],403)
        self.assertEqual(self.request(self.a,route,{'private':True})[0],200)
        self.assertEqual(self.request(self.a,route,{'private':True})[0],200)
        self.assertIsNone(self.request(outsider,path)[1]['ornaments'][0]['text'])
        self.assertEqual(self.request(self.b,path)[1]['ornaments'][0]['text'],'圣诞快乐')
        self.assertEqual(self.request(self.a,route,{'private':False})[0],422)
        self.assertEqual(self.request(self.a,path+'/ornaments/37/privacy',{'private':True})[0],404)
    def test_privacy_value_validation(self):
        tree=self.tree()
        for value in ['false',1,None,[],{}]:
            payload={'point':0,'ornament':'ball','name':'朋友','text':'你好','private':value}
            self.assertEqual(self.request(self.b,'/api/trees/'+tree+'/ornaments',payload)[0],422)
        self.assertEqual(self.gift(self.b,tree)[0],201)
        entry=self.request(self.client(),'/api/trees/'+tree)[1]['ornaments'][0]
        self.assertFalse(entry['private']); self.assertTrue(entry['canRead'])
    def test_many_gifts_keep_cumulative_capacity_after_delete(self):
        tree=self.tree(); self.server.get_app().max_ornaments=2
        self.assertEqual(self.gift(self.b,tree,0)[0],201)
        self.assertEqual(self.gift(self.b,tree,1)[0],201)
        self.assertEqual(self.request(self.a,'/api/trees/'+tree+'/ornaments/0/remove',{})[0],200)
        self.assertEqual(self.gift(self.b,tree,0)[1]['code'],'CAPACITY')
    def test_health_and_static_cache(self):
        self.assertEqual(self.request(self.a,'/healthz')[0],200)
        response=self.a.open(self.base+'/app.js')
        self.assertIn('no-cache',response.headers['Cache-Control'])
        etag=response.headers['ETag']; response.close()
        req=urllib.request.Request(self.base+'/app.js',headers={'If-None-Match':etag})
        with self.assertRaises(urllib.error.HTTPError) as caught: self.a.open(req)
        self.assertEqual(caught.exception.code,304); caught.exception.close()
    def test_new_ornaments(self):
        for ornament in ['candy','stocking']:
            tree=self.tree()
            status,_=self.request(self.a,'/api/trees/'+tree+'/ornaments',{'point':0,'ornament':ornament,'name':'朋友','text':'快乐'})
            self.assertEqual(status,201)
    def test_pages_and_private_files(self):
        tree=self.tree()
        for path in ['/', '/tree/'+tree, '/app.js']:
            self.assertEqual(self.a.open(self.base+path).status,200)
        self.assertEqual(self.request(self.a,'/data/test.db')[0],404)

class MigrationTests(unittest.TestCase):
    def test_legacy_rows_permissions_and_history_preserved(self):
        import sqlite3
        from server import TreeApp
        with tempfile.TemporaryDirectory() as folder:
            path=folder+'/old.db'
            with sqlite3.connect(path) as db:
                db.executescript("""
                CREATE TABLE trees(id TEXT PRIMARY KEY,name TEXT NOT NULL,owner TEXT);
                CREATE TABLE developer_browser(anchor TEXT NOT NULL,visitor TEXT NOT NULL);
                CREATE TABLE ornaments(tree TEXT NOT NULL REFERENCES trees(id),point INTEGER NOT NULL,ornament TEXT NOT NULL,name TEXT NOT NULL,text TEXT NOT NULL,visitor TEXT NOT NULL,UNIQUE(tree,point),UNIQUE(tree,visitor));
                CREATE TABLE gift_history(tree TEXT NOT NULL,visitor TEXT NOT NULL,UNIQUE(tree,visitor));
                INSERT INTO trees VALUES ('abcdefghijklmnop','旧树','owner-hash');
                INSERT INTO developer_browser VALUES ('abcdefghijklmnop','developer-hash');
                INSERT INTO ornaments VALUES ('abcdefghijklmnop',0,'star','旧朋友','旧留言','sender-hash');
                INSERT INTO gift_history VALUES ('abcdefghijklmnop','sender-hash');
                INSERT INTO gift_history VALUES ('abcdefghijklmnop','removed-sender');
                """)
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(TreeApp,[path,path]))
            TreeApp(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT name,owner FROM trees').fetchone(),('旧树','owner-hash'))
                self.assertEqual(db.execute('SELECT visitor FROM developer_browser').fetchone()[0],'developer-hash')
                self.assertEqual(db.execute('SELECT text,visitor,private FROM ornaments').fetchone(),('旧留言','sender-hash',0))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM gift_history').fetchone()[0],2)
                db.execute("INSERT INTO ornaments(tree,point,ornament,name,text,visitor,private) VALUES ('abcdefghijklmnop',1,'ball','朋友','再次留言','sender-hash',1)")
                db.execute("INSERT INTO gift_history VALUES ('abcdefghijklmnop','sender-hash')")
            TreeApp(path)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM gift_history').fetchone()[0],3)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ornaments').fetchone()[0],2)

    def test_migration_failure_rolls_back_tables_and_version(self):
        import sqlite3
        from unittest.mock import patch
        from server import TreeApp
        with tempfile.TemporaryDirectory() as folder:
            path=folder+'/rollback.db'
            with sqlite3.connect(path) as db:
                db.executescript("""
                CREATE TABLE trees(id TEXT PRIMARY KEY,name TEXT NOT NULL,owner TEXT);
                INSERT INTO trees VALUES ('abcdefghijklmnop','保留树','owner-hash');
                CREATE TABLE ornaments(tree TEXT NOT NULL,point INTEGER NOT NULL,ornament TEXT NOT NULL,name TEXT NOT NULL,text TEXT NOT NULL,visitor TEXT NOT NULL,UNIQUE(tree,point),UNIQUE(tree,visitor));
                INSERT INTO ornaments VALUES ('abcdefghijklmnop',0,'star','朋友','保留留言','sender-hash');
                CREATE TABLE gift_history(tree TEXT NOT NULL,visitor TEXT NOT NULL,UNIQUE(tree,visitor));
                INSERT INTO gift_history VALUES ('abcdefghijklmnop','sender-hash');
                """)
            connect=sqlite3.connect
            class InterruptedConnection(sqlite3.Connection):
                def execute(self,sql,*args):
                    if sql=='ALTER TABLE gift_history_v1 RENAME TO gift_history':
                        raise sqlite3.OperationalError('simulated migration interruption')
                    return super().execute(sql,*args)
            def interrupted_connect(*args,**kwargs):
                return connect(*args,**kwargs,factory=InterruptedConnection)
            with patch('server.sqlite3.connect',side_effect=interrupted_connect):
                with self.assertRaises(sqlite3.OperationalError): TreeApp(path)
            with connect(path) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT text FROM ornaments').fetchone()[0],'保留留言')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM gift_history').fetchone()[0],1)
                self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('ornaments_v1','gift_history_v1')").fetchone())
            TreeApp(path)
            with connect(path) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)

if __name__ == '__main__': unittest.main()
