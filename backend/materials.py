"""Metadata-only course catalog and bounded local file streaming."""
import hashlib
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

CATALOG_PATH=Path(__file__).with_name('materials_catalog.json')
VIDEO={'.mp4','.m4v','.webm','.mkv','.mov','.avi','.wmv','.flv'}
ALLOWED=VIDEO|{'.pdf','.txt','.md','.ipynb','.zip','.rar','.docx','.pptx','.html','.htm'}
IGNORED={'.idea','__pycache__','.ipynb_checkpoints','.git','asset','assets','data','pictures','图片','bert-base-chinese'}
_cache={}

def stable_id(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()
def natural(text):return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)',str(text))]

def scan(root):
    root=Path(root).resolve(strict=True);courses=[]
    for collection in sorted((p for p in root.iterdir() if p.is_dir()),key=natural):
        for folder in sorted((p for p in collection.iterdir() if p.is_dir()),key=natural):
            course_path=folder.relative_to(root).as_posix();lessons=[]
            for file in sorted(folder.rglob('*'),key=natural):
                if not file.is_file() or file.suffix.lower() not in ALLOWED:continue
                relative=file.relative_to(root)
                if any(part in IGNORED for part in relative.parts):continue
                try:
                    if not file.resolve(strict=True).is_relative_to(root):continue
                    size=file.stat().st_size
                except OSError:continue
                ext=file.suffix.lower();path=relative.as_posix()
                kind='video' if ext in VIDEO else 'archive' if ext in {'.zip','.rar'} else 'text' if ext in {'.txt','.md'} else 'notebook' if ext=='.ipynb' else 'document'
                inside=file.relative_to(folder)
                section=inside.parent.as_posix() if inside.parent!=Path('.') else '课程资料'
                lessons.append({'id':stable_id(path),'title':file.stem,'section':section,'kind':kind,'size':size,'extension':ext,'path':path})
            courses.append({'local_key':stable_id(course_path),'title':folder.name,'collection':collection.name,'path':course_path,'lessons':lessons})
    return {'version':1,'root':str(root),'courses':courses}

def catalog():
    try:
        stat=CATALOG_PATH.stat();key=(str(CATALOG_PATH),stat.st_mtime_ns,stat.st_size)
    except OSError:return {'root':'','courses':[],'by_course':{},'by_lesson':{}}
    if _cache.get('key')!=key:
        data=json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
        data['by_course']={c['local_key']:c for c in data['courses']}
        data['by_lesson']={l['id']:(c,l) for c in data['courses'] for l in c['lessons']}
        _cache.clear();_cache.update(key=key,data=data)
    return _cache['data']

def metadata(course):
    key=course.get('local_key')
    if not key:return {'local_key':None,'is_local':False,'lesson_count':0,'video_count':0,'available':True}
    data=catalog();group=data['by_course'].get(key)
    available=False
    if group and data['root']:
        try:
            root=Path(data['root']).resolve(strict=True)
            target=(root/group['path']).resolve(strict=True)
            available=target.is_dir() and target.is_relative_to(root)
        except OSError:pass
    return {'local_key':key,'is_local':True,'lesson_count':len(group['lessons']) if group else 0,
            'video_count':sum(l['kind']=='video' for l in group['lessons']) if group else 0,'available':available}

def lesson_list(course):
    data=catalog();group=data['by_course'].get(course.get('local_key'))
    if group is None:return None
    fields=('id','title','section','kind','size','extension')
    return [{k:l[k] for k in fields} for l in group['lessons']]

def range_bounds(header,size):
    if not header:return 0,size-1,False
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',header.strip())
    if not match or size<=0:raise ValueError('invalid range')
    first,last=match.groups()
    if not first:
        if not last or int(last)<=0:raise ValueError('invalid range')
        start=max(0,size-int(last));end=size-1
    else:
        start=int(first);end=min(int(last),size-1) if last else size-1
        if start>=size or start>end:raise ValueError('invalid range')
    return start,end,True

def stream(handler,lesson_id,owned_keys,origin):
    item=catalog()['by_lesson'].get(lesson_id)
    if not item or item[0]['local_key'] not in owned_keys:
        handler.reply(404,{'error':'资料不存在'});return
    group,lesson=item
    try:
        root=Path(catalog()['root']).resolve(strict=True)
        path=(root/lesson['path']).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():raise FileNotFoundError()
        size=path.stat().st_size
        file=path.open('rb')
    except (OSError,ValueError):
        handler.reply(404,{'error':'本地资料暂不可用，请检查 G 盘及资料目录是否连接'});return
    with file:
        try:start,end,partial=range_bounds(handler.headers.get('Range'),size)
        except ValueError:
            handler.send_response(416);handler.send_header('Content-Range',f'bytes */{size}');handler.send_header('Content-Length','0');handler.send_header('Access-Control-Allow-Origin',origin);handler.send_header('Access-Control-Allow-Credentials','true');handler.end_headers();return
        ext=path.suffix.lower()
        inline=ext in VIDEO|{'.pdf','.txt','.md'}
        content_type='text/plain; charset=utf-8' if ext in {'.txt','.md'} else mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        handler.send_response(206 if partial else 200)
        for k,v in {'Content-Type':content_type,'Content-Length':str(max(0,end-start+1)),'Accept-Ranges':'bytes','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Access-Control-Allow-Origin':origin,'Access-Control-Allow-Credentials':'true','Content-Disposition':('inline' if inline else 'attachment')+"; filename*=UTF-8''"+quote(path.name,safe='')}.items():handler.send_header(k,v)
        if partial:handler.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        handler.end_headers()
        if handler.command=='HEAD':return
        file.seek(start);remaining=max(0,end-start+1)
        try:
            while remaining:
                chunk=file.read(min(1024*1024,remaining))
                if not chunk:break
                handler.wfile.write(chunk);remaining-=len(chunk)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
