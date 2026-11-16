"""原生 WSGI 应用。正式部署用 Gunicorn；本地可运行 python3 server.py。"""
from pathlib import Path
from contextlib import contextmanager
from functools import lru_cache
from http.cookies import SimpleCookie
from http import HTTPStatus
from urllib.parse import urlsplit
import argparse
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading

ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger('christmas')

class APIError(Exception):
    def __init__(self,status,code,message):
        self.status,self.code,self.message=status,code,message

class TreeApp:
    def __init__(self,database=None,max_trees=None,max_ornaments=None,max_inflight=8):
        self.database=str(database or os.environ.get('TREE_DATABASE',ROOT/'data'/'trees.db'))
        self.max_trees=int(max_trees if max_trees is not None else os.environ.get('TREE_MAX_TREES','10000'))
        self.max_ornaments=int(max_ornaments if max_ornaments is not None else os.environ.get('TREE_MAX_ORNAMENTS','100000'))
        if self.max_trees<1 or self.max_ornaments<1: raise ValueError('数据上限必须为正数')
        self.slots=threading.BoundedSemaphore(max_inflight)
        Path(self.database).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
              CREATE TABLE IF NOT EXISTS trees(id TEXT PRIMARY KEY,name TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS developer_browser(anchor TEXT NOT NULL,visitor TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS ornaments(
                tree TEXT NOT NULL REFERENCES trees(id),point INTEGER NOT NULL,
                ornament TEXT NOT NULL,name TEXT NOT NULL,text TEXT NOT NULL,
                visitor TEXT NOT NULL,UNIQUE(tree,point),UNIQUE(tree,visitor));
            ''')
        # 兼容旧数据库；旧树没有创建者证据，不能凭名字认领。
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            columns={row[1] for row in db.execute('PRAGMA table_info(trees)')}
            if 'owner' not in columns: db.execute('ALTER TABLE trees ADD COLUMN owner TEXT')
            db.execute('CREATE TABLE IF NOT EXISTS gift_history(tree TEXT NOT NULL,visitor TEXT NOT NULL,UNIQUE(tree,visitor))')
            db.execute('INSERT OR IGNORE INTO gift_history SELECT tree,visitor FROM ornaments')
        self.assets={}
        for name,mime in [('index.html','text/html; charset=utf-8'),('app.js','text/javascript; charset=utf-8')]:
            body=(ROOT/name).read_bytes()
            self.assets[name]=(body,mime,'"'+hashlib.sha256(body).hexdigest()+'"')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.database,timeout=3)
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db: yield db
        finally: db.close()

    @staticmethod
    def visitor(tree,token):
        return hashlib.sha256((tree+':'+token).encode()).hexdigest()

    def developer(self,db,token):
        row=db.execute('SELECT anchor,visitor FROM developer_browser LIMIT 1').fetchone()
        return bool(row and secrets.compare_digest(self.visitor(row[0],token),row[1]))

    def __call__(self,environ,start_response):
        if not self.slots.acquire(blocking=False):
            return self.respond(start_response,503,{'code':'BUSY','message':'访问有点多，请稍后再试'},extra=[('Retry-After','3')])
        try:
            if environ.get('PATH_INFO') == '/assets/music/snowfall.mp3' and environ.get('REQUEST_METHOD') == 'GET':
                return self.music(environ,start_response)
            cookie=SimpleCookie()
            try: cookie.load(environ.get('HTTP_COOKIE',''))
            except Exception: pass
            item=cookie.get('tree_visitor'); token=item.value if item else ''
            new=not bool(re.fullmatch('[a-f0-9]{64}',token))
            if new: token=secrets.token_hex(32)
            extra=[]
            if new:
                secure='; Secure' if environ.get('wsgi.url_scheme')=='https' else ''
                extra.append(('Set-Cookie',f'tree_visitor={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000{secure}'))
            try:
                status,result,asset=self.route(environ,token)
                return self.respond(start_response,status,result,asset,extra)
            except APIError as error:
                return self.respond(start_response,error.status,{'code':error.code,'message':error.message},extra=extra)
            except sqlite3.OperationalError as error:
                LOGGER.exception('Database unavailable')
                if 'locked' in str(error).lower() or 'busy' in str(error).lower():
                    return self.respond(start_response,503,{'code':'BUSY','message':'暂时繁忙，请稍后重试'},extra=extra+[('Retry-After','3')])
                return self.respond(start_response,500,{'code':'STORAGE','message':'暂时无法读取或保存，请稍后再来'},extra=extra)
            except Exception:
                LOGGER.exception('Request failed')
                return self.respond(start_response,500,{'code':'ERROR','message':'出了点小问题，请稍后再试'},extra=extra)
        finally: self.slots.release()

    def music(self,e,start):
        path=ROOT/'assets'/'music'/'snowfall.mp3'
        if not path.is_file(): return self.respond(start,404,{'message':'音乐暂时无法播放'})
        size=path.stat().st_size; begin,end=0,size-1; status=200
        headers=[('Content-Type','audio/mpeg'),('Accept-Ranges','bytes'),('Cache-Control','public, max-age=86400'),('X-Content-Type-Options','nosniff')]
        byte_range=e.get('HTTP_RANGE')
        if byte_range:
            match=re.fullmatch(r'bytes=(\d+)-(\d*)',byte_range)
            if not match:
                start('416 Range Not Satisfiable',[('Content-Range',f'bytes */{size}'),('Content-Length','0')]); return []
            begin=int(match[1]);end=min(int(match[2]) if match[2] else size-1,size-1)
            if begin>end:
                start('416 Range Not Satisfiable',[('Content-Range',f'bytes */{size}'),('Content-Length','0')]); return []
            status=206;headers.append(('Content-Range',f'bytes {begin}-{end}/{size}'))
        headers.append(('Content-Length',str(end-begin+1)))
        start(f'{status} {HTTPStatus(status).phrase}',headers)
        def stream():
            with path.open('rb') as audio:
                audio.seek(begin);remaining=end-begin+1
                while remaining:
                    chunk=audio.read(min(65536,remaining))
                    if not chunk: break
                    remaining-=len(chunk);yield chunk
        return stream()

    def respond(self,start,status,data,asset=None,extra=None):
        headers=[('X-Content-Type-Options','nosniff'),('Referrer-Policy','same-origin')]
        if asset:
            body,mime,etag=asset
            headers += [('Cache-Control','no-cache'),('ETag',etag)]
            if status==304: body=b''
        else:
            body=json.dumps(data,ensure_ascii=False).encode(); mime='application/json; charset=utf-8'
            headers += [('Cache-Control','no-store')]
        headers += [('Content-Type',mime),('Content-Length',str(len(body)))] + (extra or [])
        start(f'{status} {HTTPStatus(status).phrase}',headers)
        return [body]

    def route(self,e,token):
        path=e.get('PATH_INFO','/');method=e.get('REQUEST_METHOD','GET')
        if method=='GET':
            if path=='/healthz':
                with self.connect() as db: db.execute('SELECT 1').fetchone()
                return 200,{'ok':True},None
            name='index.html' if path=='/' or re.fullmatch(r'/tree/[A-Za-z0-9_-]{16}',path) else 'app.js' if path in ('/app.js','/tree/app.js') else None
            if name:
                asset=self.assets[name]
                return (304 if e.get('HTTP_IF_NONE_MATCH')==asset[2] else 200),None,asset
            match=re.fullmatch(r'/api/trees/([A-Za-z0-9_-]{16})',path)
            if not match: raise APIError(404,'NOT_FOUND','找不到这个页面')
            tree=match[1]
            with self.connect() as db:
                row=db.execute('SELECT name,owner FROM trees WHERE id=?',(tree,)).fetchone()
                if not row: raise APIError(404,'TREE_NOT_FOUND','这棵树不存在，请确认链接')
                gifts=db.execute('SELECT point,ornament,name,text FROM ornaments WHERE tree=? ORDER BY point',(tree,)).fetchall()
                gifted=db.execute('SELECT 1 FROM gift_history WHERE tree=? AND visitor=?',(tree,self.visitor(tree,token))).fetchone() is not None
                developer=self.developer(db,token)
            return 200,{'id':tree,'name':row[0],'hasGifted':gifted,'isDeveloper':developer,'isOwner':bool(row[1] and secrets.compare_digest(row[1],self.visitor(tree,token))),'ornaments':[dict(zip(['point','ornament','name','text'],g)) for g in gifts]},None
        if method!='POST': raise APIError(405,'METHOD','不支持这个操作')
        origin=e.get('HTTP_ORIGIN')
        if (origin and urlsplit(origin).netloc!=e.get('HTTP_HOST')) or e.get('HTTP_SEC_FETCH_SITE')=='cross-site':
            raise APIError(403,'ORIGIN','请从本站提交')
        if e.get('CONTENT_TYPE','').split(';')[0]!='application/json': raise APIError(415,'CONTENT_TYPE','请使用 JSON 提交')
        try:
            size=int(e.get('CONTENT_LENGTH','0'))
            if not 0<size<=8192: raise ValueError()
            data=json.loads(e['wsgi.input'].read(size))
            if not isinstance(data,dict): raise ValueError()
        except (ValueError,UnicodeError): raise APIError(400,'BAD_REQUEST','提交的数据格式不正确')
        def text(key,limit):
            value=data.get(key)
            try: return isinstance(value,str) and bool(value.strip()) and len(value.encode('utf-16-le'))//2<=limit
            except UnicodeError: return False
        if path=='/api/trees':
            if not text('name',24): raise APIError(422,'INVALID','请填写 1–24 字的称呼')
            tree=secrets.token_urlsafe(12)
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT COUNT(*) FROM trees').fetchone()[0]>=self.max_trees:
                    raise APIError(503,'CAPACITY','暂时无法种新树，请晚些再来；已有的树仍可查看')
                db.execute('INSERT INTO trees(id,name,owner) VALUES (?,?,?)',(tree,data['name'].strip(),self.visitor(tree,token)))
            return 201,{'id':tree},None
        removal=re.fullmatch(r'/api/trees/([A-Za-z0-9_-]{16})/ornaments/(\d{1,2})/remove',path)
        if removal:
            tree,point=removal[1],int(removal[2])
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                owner=db.execute('SELECT owner FROM trees WHERE id=?',(tree,)).fetchone()
                if not owner: raise APIError(404,'TREE_NOT_FOUND','这棵树不存在')
                if not owner[0] or not secrets.compare_digest(owner[0],self.visitor(tree,token)):
                    raise APIError(403,'OWNER_ONLY','只有树主人可以移除留言')
                if db.execute('DELETE FROM ornaments WHERE tree=? AND point=?',(tree,point)).rowcount==0:
                    raise APIError(404,'NOT_FOUND','这枚装饰已经不在树上了')
            return 200,{'ok':True},None
        match=re.fullmatch(r'/api/trees/([A-Za-z0-9_-]{16})/ornaments',path)
        if not match: raise APIError(404,'NOT_FOUND','找不到这个接口')
        tree=match[1];point=data.get('point')
        if type(point) is not int or not 0<=point<38 or data.get('ornament') not in ('star','ball','bell','moon','candy','stocking') or not text('name',24) or not text('text',300):
            raise APIError(422,'INVALID','请检查装饰、位置和留言内容')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if data['ornament']=='moon' and not self.developer(db,token): raise APIError(403,'EXCLUSIVE','这是 16nights 的专属装饰')
            if not db.execute('SELECT 1 FROM trees WHERE id=?',(tree,)).fetchone(): raise APIError(404,'TREE_NOT_FOUND','这棵树不存在')
            if db.execute('SELECT 1 FROM gift_history WHERE tree=? AND visitor=?',(tree,self.visitor(tree,token))).fetchone(): raise APIError(409,'ALREADY_GIFTED','你已经给这棵树送过装饰了')
            if db.execute('SELECT 1 FROM ornaments WHERE tree=? AND point=?',(tree,point)).fetchone(): raise APIError(409,'POINT_TAKEN','这个位置刚被选走了，请换一个位置')
            if db.execute('SELECT COUNT(*) FROM gift_history').fetchone()[0]>=self.max_ornaments: raise APIError(503,'CAPACITY','新的心意暂时收满了，请晚些再来')
            db.execute('INSERT INTO gift_history VALUES (?,?)',(tree,self.visitor(tree,token)))
            db.execute('INSERT INTO ornaments VALUES (?,?,?,?,?,?)',(tree,point,data['ornament'],data['name'].strip(),data['text'].strip(),self.visitor(tree,token)))
        return 201,{'ok':True},None

@lru_cache(maxsize=1)
def default_app(): return TreeApp()

def application(environ,start_response): return default_app()(environ,start_response)

def make_server(host='127.0.0.1',port=8000,database=None,**limits):
    from wsgiref.simple_server import make_server as wsgi_server, WSGIServer, WSGIRequestHandler
    from socketserver import ThreadingMixIn
    class LocalServer(ThreadingMixIn,WSGIServer): daemon_threads=True
    class LocalHandler(WSGIRequestHandler):
        timeout=10
        def log_message(self,*args): pass
    return wsgi_server(host,port,TreeApp(database,**limits),LocalServer,LocalHandler)

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--host',default='127.0.0.1'); parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args(); server=make_server(args.host,args.port)
    print(f'本地教学服务：http://{args.host}:{args.port}（公网请使用 Gunicorn + Nginx）',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
