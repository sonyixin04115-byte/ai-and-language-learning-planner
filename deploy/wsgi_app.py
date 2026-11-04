"""Cloud WSGI boundary for the existing Python API, without a second API copy.

Use one create_app instance per WSGI worker. Local video bytes deliberately stay
on the user's removable disk; only task records and course metadata are hosted.
"""
from collections import defaultdict, deque
from email.message import Message
from http import HTTPStatus
import hashlib
from html import escape
import importlib.util
import io
import json
import mimetypes
import os
import math
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit, parse_qs

PROJECT = Path(__file__).resolve().parent.parent
STATIC_NAMES = {"index.html", "app.js", "style.css", "tables.css", "course-tables.css", "task-timer.js", "task-timer.css", "avatar.css", "listening.js", "listening.css"}
LOCAL_API = "const API='http://127.0.0.1:8001/api';"
CLOUD_API = "const API='/api';"


class LoginLimiter:
    """Small per-worker backoff; use the host's proxy protection as well."""
    def __init__(self):
        self.events = defaultdict(deque)
        self.lock = threading.Lock()

    def allowed(self, key):
        now = time.monotonic()
        with self.lock:
            if len(self.events) > 5000:
                self.events = defaultdict(deque, {k:v for k,v in self.events.items() if v and v[-1]>now-60})
            history = self.events[key]
            while history and history[0] <= now-60:
                history.popleft()
            if len(history) >= 20:
                return False
            history.append(now)
            return True


def load_backend(db_path):
    # A fresh module namespace lets tests instantiate isolated apps safely. The
    # actual route implementation is the same file used by the local server.
    spec = importlib.util.spec_from_file_location("zhixu_cloud_"+secrets.token_hex(8), PROJECT/"backend/server.py")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    backend.DB = db_path
    backend.init()
    return backend


def public_origin(value):
    parsed = urlsplit(value or "")
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("STUDY_PUBLIC_ORIGIN must be your exact HTTPS site origin")
    return f"https://{parsed.netloc.lower()}"


def create_app(db_path=None, origin=None, allow_registration=False):
    origin = public_origin(origin or os.environ.get("STUDY_PUBLIC_ORIGIN"))
    configured_db = db_path or os.environ.get("STUDY_DB")
    if not configured_db:
        raise ValueError("Set STUDY_DB to a private persistent database path")
    db = Path(configured_db)
    if not db.is_absolute():
        raise ValueError("STUDY_DB must be absolute")
    db.parent.mkdir(parents=True, exist_ok=True)
    backend = load_backend(db)
    with backend.conn() as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS password_reset_redemptions(token_hash TEXT PRIMARY KEY, redeemed_at REAL NOT NULL, user_id INTEGER NOT NULL)')
    limiter = LoginLimiter()
    registration = allow_registration or os.environ.get("STUDY_ALLOW_REGISTRATION") == "1"

    class Request(backend.Handler):
        def __init__(self, environ, body):
            # BaseHTTPRequestHandler's socket constructor is intentionally not
            # used: WSGI has already parsed and bounded the incoming request.
            self.command = environ.get("REQUEST_METHOD", "GET").upper()
            if self.command == "HEAD":
                self.command = "GET"
            self.path = environ.get("PATH_INFO", "/")
            if environ.get("QUERY_STRING"):
                self.path += "?" + environ["QUERY_STRING"]
            self.headers = Message()
            for key, value in environ.items():
                if key.startswith("HTTP_") and key not in {"HTTP_CONTENT_LENGTH", "HTTP_CONTENT_TYPE"}:
                    self.headers[key[5:].replace("_", "-")] = str(value)
            # WSGI servers may supply CONTENT_LENGTH='' for bodyless requests.
            # The boundary has already validated and read the body; expose its
            # actual numeric length to the shared HTTP handler, never the raw
            # WSGI string or a duplicate client Content-Length header.
            self.headers["Content-Length"] = str(len(body))
            if environ.get("CONTENT_TYPE"):
                self.headers["Content-Type"] = str(environ["CONTENT_TYPE"])
            # The cloud boundary already checked the real Origin; normalize it
            # for the local transport's fixed CORS check without weakening it.
            if self.headers.get("Origin"):
                self.headers.replace_header("Origin", backend.ORIGIN)
            self.rfile = io.BytesIO(body)
            self.wfile = io.BytesIO()
            self.status = 200
            self.response_headers = []

        def send_response(self, code, message=None):
            self.status = int(code)

        def send_header(self, name, value):
            if name.lower().startswith("access-control-"):
                return  # The website and API use the same HTTPS origin.
            if name.lower() == "set-cookie":
                attributes = {part.strip().split("=", 1)[0].lower() for part in str(value).split(";")[1:]}
                if "secure" not in attributes:
                    value += "; Secure"
            self.response_headers.append((name, str(value)))

        def end_headers(self):
            pass

    def simple(start_response, code, payload, extra=()):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = [("Content-Type", "application/json; charset=utf-8"), ("Content-Length",str(len(body))), ("Cache-Control","no-store"), ("X-Content-Type-Options","nosniff"), *extra]
        start_response(f"{code} {HTTPStatus(code).phrase}", headers)
        return [body]

    def reset_html(start_response, code, content, referrer_policy='no-referrer'):
        body = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>人工智能学习计划 · 重置密码</title>'
                '<style>body{font:16px/1.8 system-ui,sans-serif;background:#f5f7f2;color:#203c35;margin:0;padding:32px 18px}main{max-width:420px;margin:7vh auto;background:#fff;border:1px solid #d9e1d7;border-radius:12px;padding:28px}label{display:block;margin:18px 0}input{display:block;box-sizing:border-box;width:100%;padding:12px;margin-top:6px;border:1px solid #ccd8cc;border-radius:6px;font:inherit}button{background:#22664f;color:white;border:0;border-radius:6px;padding:12px 20px;font:inherit;cursor:pointer}p{color:#6b7e70}a{color:#22664f}</style>'
                '</head><body><main>' + content + '</main></body></html>').encode('utf-8')
        headers = [('Content-Type','text/html; charset=utf-8'),('Content-Length',str(len(body))),('Cache-Control','no-store'),
                   ('Referrer-Policy',referrer_policy),('X-Frame-Options','DENY'),('X-Content-Type-Options','nosniff'),
                   ('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
                   ('Strict-Transport-Security','max-age=31536000')]
        start_response(f'{code} {HTTPStatus(code).phrase}',headers)
        return [body]

    def reset_config(token):
        if not isinstance(token,str) or not 32 <= len(token) <= 128:
            return None
        request_file = db.parent/'password-reset.json'
        try:
            if request_file.stat().st_size > 4096: return None
            request = json.loads(request_file.read_text(encoding='utf-8'))
            expires = float(request['expires'])
            digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
            expected = request['token_hash']
            username = request['username']
            if not isinstance(expected,str) or not isinstance(username,str) or not math.isfinite(expires) or expires <= time.time(): return None
            if len(expected)!=64 or not secrets.compare_digest(digest,expected): return None
            return username,digest
        except (OSError,ValueError,KeyError,TypeError):
            return None

    def reset_dispatch(environ,start_response,method):
        if method not in {'GET','HEAD','POST'}:
            return simple(start_response,405,{'error':'不支持的操作'})
        if not limiter.allowed('reset:'+environ.get('REMOTE_ADDR','unknown')):
            return simple(start_response,429,{'error':'尝试过于频繁，请一分钟后再试'})
        fields = parse_qs(environ.get('QUERY_STRING',''),keep_blank_values=True)
        if method == 'POST':
            if environ.get('HTTP_ORIGIN') != origin:
                return simple(start_response,403,{'error':'来源不被允许'})
            try:
                length=int(environ.get('CONTENT_LENGTH') or 0)
                if length<0 or length>65536: return simple(start_response,413,{'error':'内容过长'})
                raw=environ.get('wsgi.input',io.BytesIO()).read(length)
                if len(raw)!=length: raise ValueError('incomplete')
                fields=parse_qs(raw.decode('utf-8'),keep_blank_values=True,strict_parsing=True)
            except (ValueError,UnicodeError):
                return simple(start_response,400,{'error':'表单无效'})
        def single(key):
            values=fields.get(key,[])
            return values[0] if len(values)==1 else ''
        token=single('token')
        config=reset_config(token)
        invalid='<h1>重置链接不可用</h1><p>链接可能已经使用、已经过期或无效。请向系统所有者重新申请。</p><a href="/">返回登录</a>'
        if not config: return reset_html(start_response,403,invalid)
        username,digest=config
        with backend.conn() as connection:
            # SQLite's write lock serializes redemption and password update.
            connection.execute('BEGIN IMMEDIATE')
            user=connection.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
            consumed=connection.execute('SELECT token_hash FROM password_reset_redemptions WHERE token_hash=?',(digest,)).fetchone()
            if not user or consumed: return reset_html(start_response,403,invalid)
            if method == 'POST':
                password=single('password');confirmation=single('confirm_password')
                if not 8<=len(password)<=128 or password!=confirmation:
                    return reset_html(start_response,400,'<h1>密码未修改</h1><p>密码需要 8–128 个字符，且两次输入一致。请返回上一页重试，重置链接尚未使用。</p>')
                # Recheck the expiry after acquiring the database write lock.
                if reset_config(token)!=config: return reset_html(start_response,403,invalid)
                salt=secrets.token_hex(16)
                hashed=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),600000).hex()
                connection.execute('UPDATE users SET password=?,salt=? WHERE id=?',(hashed,salt,user['id']))
                connection.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
                connection.execute('INSERT INTO password_reset_redemptions VALUES(?,?,?)',(digest,time.time(),user['id']))
                return reset_html(start_response,200,'<h1>密码已重置</h1><p>账号与学习记录已保留，原有登录会话已退出。请使用刚设置的新密码登录。</p><a href="/">返回登录页面 →</a>')
        # HTML form POSTs under no-referrer carry Origin:null, which our strict
        # source check must reject. same-origin preserves the real Origin for
        # this local form and still suppresses referrers to any external site.
        return reset_html(start_response,200,'<h1>设置新密码</h1><p>账号：'+escape(username)+'。此链接仅可使用一次，请在 15 分钟内完成。</p>'
            '<form method="post" action="/reset-password"><input type="hidden" name="token" value="'+escape(token,quote=True)+'">'
            '<label>新密码<input type="password" name="password" minlength="8" maxlength="128" autocomplete="new-password" required></label>'
            '<label>再次输入新密码<input type="password" name="confirm_password" minlength="8" maxlength="128" autocomplete="new-password" required></label>'
            '<button type="submit">保存新密码</button></form>',referrer_policy='same-origin')

    def dispatch(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        host = environ.get("HTTP_HOST", "").lower()
        if host != urlsplit(origin).netloc:
            return simple(start_response, 400, {"error":"访问域名不匹配"})
        supplied_origin = environ.get("HTTP_ORIGIN")
        if supplied_origin and supplied_origin != origin:
            return simple(start_response, 403, {"error":"来源不被允许"})
        if method not in {"GET","HEAD","POST","PATCH","DELETE","OPTIONS"}:
            return simple(start_response, 405, {"error":"不支持的操作"})
        if path == '/reset-password':
            return reset_dispatch(environ,start_response,method)
        if not path.startswith("/api/"):
            if method not in {"GET","HEAD"}:
                return simple(start_response, 405, {"error":"不支持的操作"})
            name = "index.html" if path == "/" else path[1:]
            if name not in STATIC_NAMES:
                return simple(start_response, 404, {"error":"页面不存在"})
            file = PROJECT/"frontend"/name
            try:
                body = file.read_bytes()
            except OSError:
                return simple(start_response, 404, {"error":"页面不存在"})
            if name == "app.js":
                text = body.decode("utf-8")
                # Fail closed rather than silently ship a localhost API URL.
                if text.count(LOCAL_API) != 1 and text.count(CLOUD_API) != 1:
                    return simple(start_response, 500, {"error":"前端云端地址未配置"})
                body = text.replace(LOCAL_API, CLOUD_API).encode("utf-8")
            content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            headers = [("Content-Type",content_type+"; charset=utf-8"),("Content-Length",str(len(body))), ("Cache-Control","no-cache"), ("X-Content-Type-Options","nosniff"), ("Referrer-Policy","same-origin"), ("X-Frame-Options","DENY"), ("Strict-Transport-Security","max-age=31536000")]
            start_response("200 OK",headers)
            return [] if method == "HEAD" else [body]
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError,ValueError):
            return simple(start_response, 400, {"error":"内容长度无效"})
        if length < 0 or length > 65536:
            return simple(start_response, 413, {"error":"内容过长"})
        body = environ.get("wsgi.input", io.BytesIO()).read(length)
        if len(body) != length:
            return simple(start_response, 400, {"error":"请求内容不完整"})
        if path == "/api/register" and method == "POST" and not registration:
            return simple(start_response, 403, {"error":"私人计划系统暂未开放注册，请使用迁移后的原账号登录"})
        if path in {"/api/login","/api/register"} and method == "POST":
            if not limiter.allowed(environ.get("REMOTE_ADDR", "unknown")):
                return simple(start_response, 429, {"error":"尝试过于频繁，请一分钟后再试"}, [("Retry-After","60")])
        if path.startswith("/api/materials/"):
            return simple(start_response, 409, {"error":"云端只同步任务和课程目录，视频仍在你的 G 盘，请在电脑本机版播放"})
        request = Request(environ, body)
        if method == "OPTIONS":
            request.do_OPTIONS()
        else:
            request.handle_api()
        response = request.wfile.getvalue()
        if request.status == 200 and path == "/api/state":
            data = json.loads(response)
            data["runtime"] = "cloud"
            data["storage_note"] = "云端同步任务、笔记和课程目录；本地视频仍保留在 G 盘"
            for course in data.get("courses", []):
                if course.get("local_key"):
                    course.update(available=False, storage="external_drive")
            response = json.dumps(data, ensure_ascii=False).encode("utf-8")
        elif request.status == 200 and path.endswith("/lessons"):
            data = json.loads(response)
            data["course"]["available"] = False
            response = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers = [(k,v) for k,v in request.response_headers if k.lower() != "content-length"]
        headers += [("Content-Length",str(len(response))), ("X-Content-Type-Options","nosniff"), ("Strict-Transport-Security","max-age=31536000")]
        start_response(f"{request.status} {HTTPStatus(request.status).phrase}",headers)
        return [] if method == "HEAD" else [response]

    def application(environ, start_response):
        response = dispatch(environ, start_response)
        # HEAD carries the equivalent GET headers, including for error pages,
        # but never sends response-body bytes to the WSGI server.
        if environ.get("REQUEST_METHOD", "GET").upper() == "HEAD":
            if hasattr(response, "close"):
                response.close()
            return []
        return response

    return application
