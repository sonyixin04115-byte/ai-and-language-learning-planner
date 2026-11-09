import os, json, sqlite3, hashlib, secrets, time, math, base64, binascii
from contextlib import contextmanager
from datetime import date, timedelta, datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import sys
if str(ROOT_PATH := Path(__file__).resolve().parent) not in sys.path: sys.path.insert(0,str(ROOT_PATH))
import materials
import language_learning
import listening_learning

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('STUDY_DB', str(ROOT / 'study.db')))
ORIGIN = os.environ.get('STUDY_ORIGIN', 'http://127.0.0.1:5174')
SESSION_SECONDS = 604800
WEEKS = [
 ('Python 起步', ['安装 Python，运行第一段程序','变量、数字与字符串','输入输出与类型转换','条件判断 if / else','循环 for / while','练习：猜数字游戏','复习与整理本周代码']),
 ('Python 数据结构', ['列表与切片','元组、集合与字典','函数与参数','模块与标准库','文件读写与异常','练习：命令行记账本','复习：独立重写记账本']),
 ('数学基础 · 线性代数', ['函数与坐标系','向量及其运算','矩阵与矩阵乘法','线性方程组的直观意义','用 NumPy 表示向量和矩阵','练习：计算相似度','复习与错题整理']),
 ('数学基础 · 微积分与概率', ['变化率与导数的直观意义','偏导数与梯度','梯度下降的直观实验','概率与条件概率','均值、方差与常见分布','练习：模拟抛硬币并绘图','数学基础自测与复习']),
 ('数据处理工具', ['NumPy 数组与广播','NumPy 索引和聚合','Pandas 读取表格','缺失值与重复值处理','Matplotlib 绘制图表','练习：探索一份公开数据','完成数据分析小报告']),
 ('机器学习第一步', ['特征、标签与监督学习','训练集、验证集与测试集','线性回归的直观理解','用 scikit-learn 训练回归模型','损失函数与模型评价','练习：预测一个连续数值','复习：检查数据泄漏']),
 ('分类与模型评价', ['逻辑回归与二分类','准确率、精确率与召回率','混淆矩阵与 F1','决策树的直观理解','随机森林与基线比较','练习：鸢尾花分类','整理分类实验结果']),
 ('让模型更可靠', ['过拟合与欠拟合','标准化与 Pipeline','交叉验证','超参数与网格搜索','类别不平衡与误差分析','练习：改进分类模型','复盘：解释模型的局限']),
 ('神经网络入门', ['神经元与多层网络','激活函数与前向传播','反向传播的直观理解','PyTorch 张量','自动求导与优化器','练习：拟合简单函数','整理神经网络笔记']),
 ('深度学习实践', ['Dataset 与 DataLoader','搭建训练循环','验证、评估与保存模型','卷积与图像的基础概念','搭建简单 CNN','练习：手写数字分类','观察错误预测并复盘']),
 ('人工智能应用初探', ['文本、分词与词向量概念','注意力机制的直观理解','Transformer 的整体结构','大语言模型的能力与局限','提示词、检索与事实核验','练习：设计一个 AI 应用方案','总结：传统 ML 与深度学习']),
 ('独立小项目', ['确定问题、数据与评价指标','清洗数据并划分数据集','建立简单基线','训练并比较模型','分析错误并完善结果','编写项目说明与演示','结课复盘与下一阶段计划'])
]

SUPPLEMENTS = [
    ('Python 中文教程', 'https://docs.python.org/zh-cn/3/tutorial/', '补充参考：查阅 Python 语法与基础示例，按自己的需要选择章节。'),
    ('scikit-learn 入门指南', 'https://scikit-learn.org/stable/getting_started.html', '补充参考：具备 Python 和机器学习基础后，按需查阅模型训练示例。'),
    ('动手学深度学习', 'https://zh.d2l.ai/', '补充参考：中文教材与课程，涵盖数学预备知识和深度学习。')
]

@contextmanager
def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    try:
        with c: yield c
    finally: c.close()

def init():
    with conn() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, name TEXT, password TEXT, salt TEXT, minutes INTEGER DEFAULT 180, start TEXT, avatar TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, expires REAL);
        CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, user_id INTEGER, day TEXT, title TEXT, category TEXT, planned INTEGER, actual INTEGER DEFAULT 0, done INTEGER DEFAULT 0, note TEXT DEFAULT '', archived INTEGER DEFAULT 0, created_source TEXT DEFAULT 'manual');
        CREATE TABLE IF NOT EXISTS courses(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL, url TEXT DEFAULT '', description TEXT DEFAULT '', source TEXT DEFAULT 'user', archived INTEGER DEFAULT 0, local_key TEXT);
        CREATE TABLE IF NOT EXISTS lesson_progress(user_id INTEGER NOT NULL, course_id INTEGER NOT NULL, lesson_id TEXT NOT NULL, completed_at REAL NOT NULL, PRIMARY KEY(user_id,course_id,lesson_id));
        CREATE TABLE IF NOT EXISTS rest_days(user_id INTEGER NOT NULL, day TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(user_id,day));
        CREATE TABLE IF NOT EXISTS course_defaults(user_id INTEGER PRIMARY KEY);''')
        user_columns={row['name'] for row in c.execute('PRAGMA table_info(users)')}
        if 'avatar' not in user_columns:c.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT ''")
        columns = {row['name'] for row in c.execute('PRAGMA table_info(tasks)')}
        if 'archived' not in columns: c.execute('ALTER TABLE tasks ADD COLUMN archived INTEGER DEFAULT 0')
        if 'created_source' not in columns: c.execute("ALTER TABLE tasks ADD COLUMN created_source TEXT DEFAULT 'manual'")
        for column in ('started_at','finished_at'):
            if column not in columns:c.execute('ALTER TABLE tasks ADD COLUMN '+column+' TEXT')
        if 'pause_history' not in columns:c.execute("ALTER TABLE tasks ADD COLUMN pause_history TEXT NOT NULL DEFAULT '[]'")
        course_columns={row['name'] for row in c.execute('PRAGMA table_info(courses)')}
        if 'local_key' not in course_columns:c.execute('ALTER TABLE courses ADD COLUMN local_key TEXT')
        for user in c.execute('SELECT id FROM users').fetchall(): seed_supplements(c, user['id'])
        language_learning.init(c)
        listening_learning.init(c)

def seed_supplements(c, uid):
    if c.execute('SELECT user_id FROM course_defaults WHERE user_id=?', (uid,)).fetchone(): return
    c.executemany("INSERT INTO courses(user_id,title,url,description,source) VALUES(?,?,?,?,'supplement')",
                  [(uid, title, url, description) for title, url, description in SUPPLEMENTS])
    c.execute('INSERT INTO course_defaults(user_id) VALUES(?)', (uid,))

def jpeg_dimensions(image):
    """Parse a bounded, standalone 8-bit JPEG; never trust client dimensions."""
    invalid=ValueError('头像需为有效 JPEG 图片')
    if not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):raise invalid
    position=2;frame=None;components={};quantizers=set();huffman=set();scans=0;entropy=0
    while position<len(image):
        if image[position]!=255:raise invalid
        while position<len(image) and image[position]==255:position+=1
        if position>=len(image):raise invalid
        marker=image[position];position+=1
        if marker==0xd9:
            if position!=len(image) or frame is None or not scans or not entropy:raise invalid
            return frame
        # A standalone JPEG has one SOI and no restart markers outside a scan.
        if marker in {0,1,0xd8} or 0xd0<=marker<=0xd7:raise invalid
        if position+2>len(image):raise invalid
        length=int.from_bytes(image[position:position+2],'big')
        if length<2 or position+length>len(image)-2:raise invalid
        segment=image[position+2:position+length];position+=length
        if marker in {0xc0,0xc1,0xc2}:
            if frame is not None or len(segment)<6 or segment[0]!=8:raise invalid
            height=int.from_bytes(segment[1:3],'big');width=int.from_bytes(segment[3:5],'big');count=segment[5]
            if not 1<=width<=512 or not 1<=height<=512:raise ValueError('头像尺寸需为 1–512 像素')
            if count not in {1,3} or len(segment)!=6+3*count:raise invalid
            for offset in range(6,len(segment),3):
                key,sampling,table=segment[offset:offset+3]
                if key in components or not 1<=sampling>>4<=4 or not 1<=sampling&15<=4 or table>3:raise invalid
                components[key]=table
            frame=(width,height)
        elif marker==0xdb:
            cursor=0
            while cursor<len(segment):
                table=segment[cursor];cursor+=1
                if table>>4 not in {0,1} or table&15>3:raise invalid
                cursor+=64*(1+(table>>4))
                if cursor>len(segment):raise invalid
                quantizers.add(table&15)
            if not segment:raise invalid
        elif marker==0xc4:
            cursor=0
            while cursor<len(segment):
                if cursor+17>len(segment):raise invalid
                table=segment[cursor];counts=segment[cursor+1:cursor+17];cursor+=17
                if table>>4 not in {0,1} or table&15>3 or not 1<=sum(counts)<=256:raise invalid
                remaining=1
                for count in counts:
                    remaining=remaining*2-count
                    if remaining<0:raise invalid
                cursor+=sum(counts)
                if cursor>len(segment):raise invalid
                huffman.add((table>>4,table&15))
            if not segment:raise invalid
        elif marker==0xda:
            if frame is None or not segment:raise invalid
            count=segment[0]
            if not 1<=count<=len(components) or len(segment)!=4+2*count:raise invalid
            first,last,approximation=segment[-3:]
            if not 0<=first<=last<=63 or approximation>>4>13 or approximation&15>13:raise invalid
            seen=set()
            for offset in range(1,1+2*count,2):
                key,tables=segment[offset:offset+2]
                if key not in components or key in seen or components[key] not in quantizers:raise invalid
                seen.add(key)
                if first==0 and (0,tables>>4) not in huffman:raise invalid
                if last>0 and (1,tables&15) not in huffman:raise invalid
            scans+=1
            while position<len(image):
                if image[position]!=255:entropy+=1;position+=1;continue
                next_marker=position+1
                while next_marker<len(image) and image[next_marker]==255:next_marker+=1
                if next_marker>=len(image):raise invalid
                if image[next_marker]==0 or 0xd0<=image[next_marker]<=0xd7:
                    entropy+=image[next_marker]==0;position=next_marker+1
                else:break
        elif marker==0xdd:
            if len(segment)!=2:raise invalid
        elif not (0xe0<=marker<=0xef or marker==0xfe):raise invalid
    raise invalid

def avatar_input(value):
    """Accept only a small canonical JPEG data URL, or an explicit removal."""
    if not isinstance(value,str):raise ValueError('头像格式无效')
    if value=='':return ''
    prefix='data:image/jpeg;base64,'
    if len(value)>54000:raise ValueError('头像数据过长，请重新选择图片')
    if not value.startswith(prefix):raise ValueError('头像需为 JPEG 图片')
    try:image=base64.b64decode(value[len(prefix):],validate=True)
    except (binascii.Error,ValueError):raise ValueError('头像编码无效')
    if not image or len(image)>40000:raise ValueError('头像图片不能超过 40000 字节')
    jpeg_dimensions(image)
    return prefix+base64.b64encode(image).decode('ascii')

def course_input(data, old=None):
    old = dict(old) if old is not None else {}
    title = str(data.get('title', old.get('title', ''))).strip()
    url = str(data.get('url', old.get('url', ''))).strip()
    description = str(data.get('description', old.get('description', '')))
    source = str(data.get('source', old.get('source', 'user')))
    if not title or len(title)>200: raise ValueError('课程名称需为 1–200 字')
    if len(url)>2048 or len(description)>10000: raise ValueError('链接或课程说明过长')
    if source not in ('user','supplement'): raise ValueError('课程来源无效')
    if url:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or any(ch.isspace() for ch in url):
            raise ValueError('链接需使用完整的 http 或 https 地址')
    return title, url, description, source

def course_lessons(course):
    external = language_learning.external_lessons(course)
    return external if external is not None else materials.lesson_list(course)

def course_metadata(course):
    external = language_learning.external_lessons(course)
    if external is not None:
        entry = next((item for item in language_learning.course_catalog()
                      if item['key'] == course.get('curriculum_key')), {})
        return {'local_key':None,'is_local':False,'is_external':True,'lesson_count':len(external),
                'video_count':sum(item.get('unit_type')=='video' or item.get('format')=='视频' for item in external),
                'trackable_count':sum(language_learning.trackable(item) for item in external),'available':True,
                'instruction_language':entry.get('instruction_language', ''),
                'learning_role':entry.get('learning_role', ''),
                'unit_label':('课题' if external and all(item.get('unit_type')=='topic' for item in external)
                              else '模块' if external and all(item.get('unit_type')=='module' for item in external) else '节')}
    metadata = materials.metadata(course)
    metadata['trackable_count'] = metadata['video_count']
    return metadata

def completed_lessons(c, uid, course, lessons=None):
    """Count only currently valid, trackable local or official external lessons."""
    if lessons is None: lessons = course_lessons(course)
    trackable = {lesson['id'] for lesson in lessons or [] if language_learning.trackable(lesson)}
    return trackable.intersection(row['lesson_id'] for row in c.execute(
        'SELECT lesson_id FROM lesson_progress WHERE user_id=? AND course_id=?',
        (uid,course['id'])))

def now_utc():
    return datetime.now(timezone.utc)

def plan_day(value):
    message = '日期需为有效的 YYYY-MM-DD 格式'
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if day.isoformat() != value:
        raise ValueError(message)
    return day

def task_time(value, label):
    if value is None or value=='':return None
    if not isinstance(value,str) or len(value)>128:raise ValueError(label+'格式无效')
    try:
        moment=datetime.fromisoformat(value)
        if moment.tzinfo is None or moment.utcoffset() is None:raise ValueError()
        return moment.astimezone(timezone.utc)
    except (ValueError,OverflowError):raise ValueError(label+'需使用带时区的 ISO 时间')

def serialize_task_time(value):
    return value.isoformat().replace('+00:00','Z') if value is not None else None

def read_pause_history(value):
    """Read only server-owned, ordered pause intervals; the last may be open."""
    try:items=json.loads(value) if isinstance(value,str) else value
    except (ValueError,TypeError):raise ValueError('暂停记录格式无效')
    if items is None:items=[]
    if not isinstance(items,list) or len(items)>2048:raise ValueError('暂停记录格式无效')
    result=[];previous_end=None
    for index,item in enumerate(items):
        if not isinstance(item,dict) or set(item)!={'started_at','finished_at'}:raise ValueError('暂停记录格式无效')
        start=task_time(item['started_at'],'暂停开始时间')
        end=task_time(item['finished_at'],'暂停结束时间')
        if start is None or (end is not None and end<start):raise ValueError('暂停时间无效')
        if end is None and index!=len(items)-1:raise ValueError('只能有一段未结束的暂停')
        if previous_end is not None and start<previous_end:raise ValueError('暂停时间不能重叠')
        result.append({'started_at':start,'finished_at':end});previous_end=end
    return result

def serialize_pause_history(pauses):
    return [{key:serialize_task_time(item[key]) for key in ('started_at','finished_at')} for item in pauses]

def paused_duration(pauses,start,finish):
    """Only the intersection with the current task window is deducted."""
    return sum((max(timedelta(),min(finish,item['finished_at'] or finish)-max(start,item['started_at'])) for item in pauses),timedelta())

def paused_seconds(pauses,start,finish):
    return paused_duration(pauses,start,finish).total_seconds()

def task_timing(data, old=None, done=False, *, moment=None):
    """Calculate active time, with pause actions and history committed together."""
    if not isinstance(data,dict):raise ValueError('任务数据需为对象')
    reserved={'pause_history','pause_intervals','paused_at','paused_ms','paused_seconds','pause_count'}
    if reserved.intersection(data):raise ValueError('暂停记录由计时操作自动保存，不能手动修改')
    action=data.get('action')
    if 'action' in data:
        if action not in ('start','pause','resume','reset') or set(data)!={'action'}:raise ValueError('计时操作请单独提交 action:start、pause、resume 或 reset')
        # Reset changes only the clock, including old manually recorded minutes.
        # Do not parse old timing here: reset can also clear an invalid old clock.
        if action=='reset':return None,None,0,[]
    old=dict(old) if old is not None else {}
    pauses=read_pause_history(old.get('pause_history','[]'))
    start=task_time(data.get('started_at',old.get('started_at')),'开始时间')
    finish=task_time(data.get('finished_at',old.get('finished_at')),'结束时间')
    if 'action' in data:
        if old.get('done'):raise ValueError('已完成任务请先取消完成，再开始计时')
        if action=='start':
            if start is None:start=moment if moment is not None else now_utc()
        else:
            if start is None or finish is not None:raise ValueError('请先开始一个尚未结束的任务')
            open_pause=pauses[-1] if pauses and pauses[-1]['finished_at'] is None else None
            if action=='pause' and open_pause is None:
                action_time=moment if moment is not None else now_utc()
                if action_time<start or (pauses and action_time<pauses[-1]['finished_at']):raise ValueError('当前时间早于已有计时记录')
                if len(pauses)>=2048:raise ValueError('暂停次数过多，请分开记录任务')
                pauses.append({'started_at':action_time,'finished_at':None})
            elif action=='resume' and open_pause is not None:
                action_time=moment if moment is not None else now_utc()
                if action_time<open_pause['started_at']:raise ValueError('当前时间早于暂停开始时间')
                open_pause['finished_at']=action_time
    # Old completed records may have only a start and manually recorded minutes.
    # Editing their text must not invent an end time or erase their saved effort.
    if done and old.get('done') and start is not None and finish is None and not {'started_at','finished_at'}.intersection(data):
        actual=int(old.get('actual',0))
        if not 0<=actual<=1440:raise ValueError('用时应为 0–1440 分钟')
        return old['started_at'],None,actual,serialize_pause_history(pauses)
    if finish is not None and start is None:raise ValueError('请先填写开始时间')
    if done and start is not None and finish is None:finish=now_utc()
    if finish is not None and pauses and pauses[-1]['finished_at'] is None:
        # A manually corrected end can precede a later pause; a zero-length
        # interval then remains outside the corrected window and deducts none.
        pauses[-1]['finished_at']=max(finish,pauses[-1]['started_at'])
    elif start is None and pauses and pauses[-1]['finished_at'] is None and {'started_at','finished_at'}.intersection(data):
        pauses[-1]['finished_at']=max(now_utc(),pauses[-1]['started_at'])
    if finish is not None:
        wall=finish-start
        if wall<timedelta():raise ValueError('结束时间不能早于开始时间')
        # Subtract exact timedeltas before converting to seconds: subtracting
        # two large floats can incorrectly ceil an exact minute upward.
        seconds=max(timedelta(),wall-paused_duration(pauses,start,finish)).total_seconds()
        if seconds>86400:raise ValueError('单次有效计时最多 24 小时，请分开记录')
        actual=math.ceil(seconds/60)
    elif start is not None:actual=0
    else:actual=int(data.get('actual',old.get('actual',0)))
    if not 0<=actual<=1440:raise ValueError('用时应为 0–1440 分钟')
    return serialize_task_time(start),serialize_task_time(finish),actual,serialize_pause_history(pauses)

def set_rest_day(c, uid, data):
    """Set one owner's day flag and pause that day's active clocks atomically."""
    if not isinstance(data,dict) or set(data)!={'day','rest'}:raise ValueError('请单独提交休息日期和休息状态')
    day = plan_day(data['day']).isoformat()
    rest = data['rest']
    if not isinstance(rest,bool):raise ValueError('休息状态应为 true 或 false')
    if not c.in_transaction:c.execute('BEGIN IMMEDIATE')
    if not rest:
        c.execute('DELETE FROM rest_days WHERE user_id=? AND day=?',(uid,day))
        return {'ok':True,'rest':False,'paused_tasks':0}
    if c.execute('SELECT 1 FROM rest_days WHERE user_id=? AND day=?',(uid,day)).fetchone():
        return {'ok':True,'rest':True,'paused_tasks':0}
    moment=now_utc();paused_count=0
    rows=c.execute("SELECT * FROM tasks WHERE user_id=? AND day=? AND archived=0 AND done=0 AND started_at IS NOT NULL AND started_at<>'' AND (finished_at IS NULL OR finished_at='') ORDER BY id",(uid,day)).fetchall()
    for old in rows:
        pauses=read_pause_history(old['pause_history'])
        if pauses and pauses[-1]['finished_at'] is None:continue
        started_at,finished_at,actual,pauses=task_timing({'action':'pause'},old,False,moment=moment)
        c.execute('UPDATE tasks SET started_at=?,finished_at=?,actual=?,pause_history=? WHERE id=? AND user_id=?',
                  (started_at,finished_at,actual,json.dumps(pauses,separators=(',',':')),old['id'],uid))
        paused_count+=1
    c.execute('INSERT INTO rest_days(user_id,day,created_at) VALUES(?,?,?)',(uid,day,serialize_task_time(moment)))
    return {'ok':True,'rest':True,'paused_tasks':paused_count}

def reschedule_task(c, old, data):
    """Move an owned task inside its existing write transaction."""
    if set(data) != {'action', 'day'}:
        raise ValueError('改期操作只需提交目标日期')
    if old['done']:
        raise ValueError('已完成的任务不能改期')
    day = plan_day(data['day'])
    if day <= date.fromisoformat(old['day']):
        raise ValueError('新日期必须晚于任务当前的安排日期')

    target = day.isoformat()
    new_pause_history = None
    if old['started_at'] and not old['finished_at']:
        rest_day = c.execute('SELECT 1 FROM rest_days WHERE user_id=? AND day=?',
                             (old['user_id'], target)).fetchone()
        if rest_day:
            pauses = read_pause_history(old['pause_history'])
            if not pauses or pauses[-1]['finished_at'] is not None:
                # Keep the saved clock values and append the usual pause interval.
                _, _, _, pauses = task_timing({'action': 'pause'}, old, False)
                new_pause_history = json.dumps(pauses, separators=(',', ':'))

    c.execute('UPDATE tasks SET day=?,pause_history=COALESCE(?,pause_history) '
              'WHERE id=? AND user_id=? AND archived=0',
              (target, new_pause_history, old['id'], old['user_id']))
    return {'ok': True, 'day': target, 'paused_on_move': new_pause_history is not None}

def archive_template(c, user):
    """Archive untouched old template rows; keep all work and manual differences."""
    if not c.in_transaction:c.execute('BEGIN IMMEDIATE')
    uid = user['id']; start = date.fromisoformat(user['start']); ids = []
    for w, (_, topics) in enumerate(WEEKS):
        for d, topic in enumerate(topics):
            day = (start + timedelta(days=w*7+d)).isoformat()
            for title, category, portion in [(topic,'学习',.4),('动手练习：'+topic,'练习',.4),('回顾今日知识，写下疑问与总结','复习',.2)]:
                row = c.execute("SELECT id FROM tasks WHERE user_id=? AND day=? AND title=? AND category=? AND planned=? AND created_source IN ('template','legacy_template') AND done=0 AND actual=0 AND note='' AND started_at IS NULL AND finished_at IS NULL AND archived=0 ORDER BY id LIMIT 1",
                    (uid, day, title, category, round(user['minutes']*portion))).fetchone()
                if row: ids.append(row['id'])
    c.executemany("UPDATE tasks SET archived=1,created_source='legacy_template' WHERE id=? AND user_id=?",[(tid,uid) for tid in ids])
    return len(ids)

def seed(c, uid, start, minutes):
    for w, (stage, topics) in enumerate(WEEKS):
        for d, topic in enumerate(topics):
            day = (start + timedelta(days=w*7+d)).isoformat()
            for title, category, portion in [(topic, '学习', .4), ('动手练习：' + topic, '练习', .4), ('回顾今日知识，写下疑问与总结', '复习', .2)]:
                c.execute("INSERT INTO tasks(user_id,day,title,category,planned,created_source) VALUES(?,?,?,?,?,'template')", (uid,day,title,category,round(minutes*portion)))

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, status, data, cookie=None):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin',ORIGIN)
        self.send_header('Access-Control-Allow-Credentials','true')
        self.send_header('Cache-Control','no-store')
        if cookie: self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(body)
    def do_OPTIONS(self):
        self.send_response(204); self.send_header('Access-Control-Allow-Origin',ORIGIN)
        self.send_header('Access-Control-Allow-Credentials','true')
        self.send_header('Access-Control-Allow-Headers','Content-Type,Range')
        self.send_header('Access-Control-Allow-Methods','GET,HEAD,POST,PATCH,DELETE,OPTIONS'); self.end_headers()
    def do_GET(self): self.handle_api()
    def do_HEAD(self): self.handle_api()
    def do_POST(self): self.handle_api()
    def do_PATCH(self): self.handle_api()
    def do_DELETE(self): self.handle_api()
    def handle_api(self):
        try:
            origin = self.headers.get('Origin')
            if origin and origin != ORIGIN: return self.reply(403, {'error':'来源不被允许'})
            n = int(self.headers.get('Content-Length',0))
            if n > 65536: return self.reply(413, {'error':'内容过长'})
            data = json.loads(self.rfile.read(n)) if n else {}
            path = self.path.split('?')[0]
            if path == '/api/health' and self.command == 'GET': return self.reply(200, {'app':'zhixu-planner','version':'2'})
            with conn() as c:
                cookies = dict(p.strip().split('=',1) for p in self.headers.get('Cookie','').split(';') if '=' in p)
                current_token = cookies.get('session','')
                if path in ('/api/register','/api/login') and self.command == 'POST':
                    auto_login = data.get('auto_login',False)
                    if not isinstance(auto_login,bool):return self.reply(400, {'error':'自动登录设置无效'})
                    username = str(data.get('username','')).strip()
                    password = str(data.get('password',''))
                    if not 3 <= len(username) <= 40 or not 8 <= len(password) <= 128:
                        return self.reply(400, {'error':'用户名需 3–40 字，密码需 8–128 字'})
                    if path.endswith('register'):
                        salt = secrets.token_hex(16)
                        hashed = hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),600000).hex()
                        try:
                            cur = c.execute('INSERT INTO users(username,name,password,salt,start) VALUES(?,?,?,?,?)', (username,str(data.get('name','学习者'))[:40],hashed,salt,date.today().isoformat()))
                        except sqlite3.IntegrityError: return self.reply(409, {'error':'用户名已存在'})
                        uid = cur.lastrowid
                        seed_supplements(c, uid)
                        language_learning.seed_courses(c, uid)
                    else:
                        u = c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
                        salt = u['salt'] if u else 'invalid'
                        hashed = hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),600000).hex()
                        if not u or not secrets.compare_digest(hashed,u['password']): return self.reply(401, {'error':'用户名或密码错误'})
                        uid = u['id']
                    # The mode is bound to the exact random token stored by the server.
                    # Legacy tokens have no dot and are deliberately treated as manual.
                    token = ('auto.' if auto_login else 'manual.') + secrets.token_urlsafe(32)
                    c.execute('DELETE FROM sessions WHERE expires < ?',(time.time(),))
                    c.execute('DELETE FROM sessions WHERE token=?',(current_token,))
                    c.execute('INSERT INTO sessions VALUES(?,?,?)',(token,uid,time.time()+SESSION_SECONDS))
                    cookie=f'session={token}; HttpOnly; SameSite=Strict; Path=/'
                    if auto_login:cookie+=f'; Max-Age={SESSION_SECONDS}'
                    return self.reply(200, {'ok':True,'auto_login':auto_login},cookie)
                token = current_token
                if path == '/api/logout' and self.command == 'POST':
                    c.execute('DELETE FROM sessions WHERE token=?',(token,))
                    return self.reply(200, {'ok':True},'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                u = c.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>?',(token,time.time())).fetchone()
                if path == '/api/session/start' and self.command == 'POST':
                    if u and token.startswith('auto.'):
                        return self.reply(200, {'automatic':True})
                    # Each new document explicitly ends a previous ordinary session,
                    # even if a browser restores its session cookie after being closed.
                    c.execute('DELETE FROM sessions WHERE token=?',(token,))
                    return self.reply(200, {'automatic':False},'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                if not u: return self.reply(401, {'error':'请先登录'})
                uid = u['id']
                if path == '/api/days/rest':
                    if self.command != 'POST':return self.reply(405, {'error':'不支持的操作'})
                    result=set_rest_day(c,uid,data)
                    c.commit()
                    return self.reply(200,result)
                if path == '/api/state' and self.command == 'GET':
                    courses=[]
                    for row in c.execute('SELECT id,title,url,description,source,local_key,category,curriculum_key FROM courses WHERE user_id=? AND archived=0 ORDER BY CASE source WHEN \'user\' THEN 0 ELSE 1 END,id',(uid,)):
                        course=dict(row);course['source_label']='我的课程' if course['source']=='user' else '补充推荐';course.update(course_metadata(course));course['completed_count']=len(completed_lessons(c,uid,course));courses.append(course)
                    tasks=[]
                    for row in c.execute('SELECT id,day,title,category,planned,actual,done,note,created_source,started_at,finished_at,pause_history FROM tasks WHERE user_id=? AND archived=0 ORDER BY day,id',(uid,)):
                        task=dict(row);task['pause_intervals']=serialize_pause_history(read_pause_history(task.pop('pause_history')));tasks.append(task)
                    return self.reply(200, {'user':{k:u[k] for k in ('username','name','minutes','start','avatar')}, 'tasks':tasks, 'rest_days':[row['day'] for row in c.execute('SELECT day FROM rest_days WHERE user_id=? ORDER BY day',(uid,))], 'archived_task_count':c.execute('SELECT count(*) FROM tasks WHERE user_id=? AND archived=1',(uid,)).fetchone()[0], 'courses':courses, 'language_settings':language_learning.settings(c,uid), 'weeks':[{'title':w[0],'topics':w[1]} for w in WEEKS]})
                if path == '/api/listening/audio':
                    if self.command not in ('GET','HEAD'):return self.reply(405, {'error':'不支持的操作'})
                    query={key:values[-1] for key,values in parse_qs(urlsplit(self.path).query).items()}
                    try:body=listening_learning.read_audio(c,uid,query)
                    except (FileNotFoundError,OSError):return self.reply(404, {'error':'听力音频暂不可用，请刷新材料或选择设备语音'})
                    size=len(body)
                    try:start,end,partial=materials.range_bounds(self.headers.get('Range'),size)
                    except ValueError:
                        self.send_response(416)
                        for key,value in {'Content-Range':f'bytes */{size}','Content-Length':'0','Accept-Ranges':'bytes','Cache-Control':'private, no-store','Access-Control-Allow-Origin':ORIGIN,'Access-Control-Allow-Credentials':'true'}.items():self.send_header(key,value)
                        self.end_headers();return
                    self.send_response(206 if partial else 200)
                    for key,value in {'Content-Type':'audio/wav','Content-Length':str(end-start+1),'Accept-Ranges':'bytes','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Access-Control-Allow-Origin':ORIGIN,'Access-Control-Allow-Credentials':'true'}.items():self.send_header(key,value)
                    if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                    self.end_headers()
                    if self.command!='HEAD':self.wfile.write(body[start:end+1])
                    return
                if path in ('/api/listening','/api/listening/reveal'):
                    query={key:values[-1] for key,values in parse_qs(urlsplit(self.path).query).items()}
                    if path=='/api/listening' and self.command=='GET':
                        result=listening_learning.get(c,uid,query)
                    elif self.command=='POST':
                        c.execute('BEGIN IMMEDIATE')
                        result=(listening_learning.reveal if path.endswith('/reveal') else listening_learning.submit)(c,uid,data)
                    else:return self.reply(405, {'error':'不支持的操作'})
                    c.commit()
                    return self.reply(200,result)
                if path in ('/api/language-practice','/api/language-practice/review','/api/language-practice/next','/api/language-settings'):
                    query={key:values[-1] for key,values in parse_qs(urlsplit(self.path).query).items()}
                    if path=='/api/language-practice' and self.command=='GET':
                        result=language_learning.daily(c,uid,query)
                    elif path=='/api/language-practice/review' and self.command=='GET':
                        result=language_learning.reviews(c,uid,query)
                    elif path=='/api/language-practice/next' and self.command=='POST':
                        c.execute('BEGIN IMMEDIATE')
                        result=language_learning.next_daily(c,uid,data)
                    elif path=='/api/language-practice' and self.command=='PATCH':
                        c.execute('BEGIN IMMEDIATE')
                        result=language_learning.save_answer(c,uid,data)
                    elif path=='/api/language-settings' and self.command=='PATCH':
                        result=language_learning.save_settings(c,uid,data)
                    else:return self.reply(405, {'error':'不支持的操作'})
                    c.commit()
                    return self.reply(200,result)
                if path == '/api/avatar':
                    if self.command!='PATCH':return self.reply(405, {'error':'不支持的操作'})
                    if not isinstance(data,dict) or set(data)!={'avatar'}:raise ValueError('请只提交头像数据')
                    avatar=avatar_input(data['avatar'])
                    c.execute('UPDATE users SET avatar=? WHERE id=?',(avatar,uid))
                    c.commit()
                    return self.reply(200, {'avatar':avatar})
                if path == '/api/settings' and self.command == 'PATCH':
                    minutes = int(data['minutes'])
                    if not 30 <= minutes <= 600: raise ValueError('每日时间应为 30–600 分钟')
                    c.execute('UPDATE users SET name=?,minutes=? WHERE id=?',(str(data['name'])[:40],minutes,uid))
                    return self.reply(200, {'ok':True})
                if path == '/api/template' and self.command == 'POST':
                    return self.reply(410, {'error':'自动排课已关闭；每天的任务由你自己添加和安排'})
                if path == '/api/tasks/archive-template' and self.command == 'POST':
                    return self.reply(200, {'ok':True,'archived':archive_template(c,u)})
                if path == '/api/tasks/restore-archived' and self.command == 'POST':
                    result=c.execute('UPDATE tasks SET archived=0 WHERE user_id=? AND archived=1',(uid,))
                    return self.reply(200, {'ok':True,'restored':result.rowcount})
                if path == '/api/courses' and self.command == 'POST':
                    title,url,description,source=course_input(data)
                    category=language_learning.category(data)
                    result=c.execute('INSERT INTO courses(user_id,title,url,description,source,category) VALUES(?,?,?,?,?,?)',(uid,title,url,description,source,category))
                    return self.reply(201, {'ok':True,'id':result.lastrowid})
                if path.startswith('/api/materials/') and self.command in ('GET','HEAD'):
                    owned={row['local_key'] for row in c.execute('SELECT local_key FROM courses WHERE user_id=? AND archived=0 AND local_key IS NOT NULL',(uid,))}
                    return materials.stream(self,path.rsplit('/',1)[1],owned,ORIGIN)
                if path.startswith('/api/courses/') and path.endswith('/lessons') and self.command=='GET':
                    cid=int(path.split('/')[-2]);row=c.execute('SELECT id,title,local_key,category,curriculum_key FROM courses WHERE id=? AND user_id=? AND archived=0',(cid,uid)).fetchone()
                    if not row:return self.reply(404,{'error':'课程不存在'})
                    course=dict(row);lessons=course_lessons(course)
                    if lessons is None:return self.reply(404,{'error':'这门课程没有可跟踪的课时目录'})
                    completed=completed_lessons(c,uid,course,lessons)
                    for lesson in lessons:lesson['trackable']=language_learning.trackable(lesson);lesson['done']=lesson['trackable'] and lesson['id'] in completed
                    course.update(course_metadata(course));course['completed_count']=len(completed)
                    return self.reply(200,{'course':course,'lessons':lessons})
                parts=path.split('/')
                if len(parts)==6 and parts[1:3]==['api','courses'] and parts[4]=='lessons':
                    cid=int(parts[3]);lid=parts[5]
                    row=c.execute('SELECT id,title,local_key,category,curriculum_key FROM courses WHERE id=? AND user_id=? AND archived=0',(cid,uid)).fetchone()
                    if not row:return self.reply(404,{'error':'课程不存在'})
                    course=dict(row);lessons=course_lessons(course)
                    lesson=next((item for item in lessons or [] if item['id']==lid and language_learning.trackable(item)),None)
                    if not lesson:return self.reply(404,{'error':'课时不存在或不能勾选'})
                    if self.command!='PATCH':return self.reply(405,{'error':'不支持的操作'})
                    if not isinstance(data,dict) or type(data.get('done')) is not bool:raise ValueError('完成状态应为 true 或 false')
                    if data['done']:
                        c.execute('INSERT INTO lesson_progress(user_id,course_id,lesson_id,completed_at) VALUES(?,?,?,?) ON CONFLICT(user_id,course_id,lesson_id) DO NOTHING',(uid,cid,lid,time.time()))
                    else:c.execute('DELETE FROM lesson_progress WHERE user_id=? AND course_id=? AND lesson_id=?',(uid,cid,lid))
                    return self.reply(200,{'ok':True,'done':data['done'],'completed_count':len(completed_lessons(c,uid,course,lessons))})
                if path.startswith('/api/courses/'):
                    cid=int(path.rsplit('/',1)[1])
                    old=c.execute('SELECT * FROM courses WHERE id=? AND user_id=? AND archived=0',(cid,uid)).fetchone()
                    if not old: return self.reply(404, {'error':'课程不存在'})
                    if self.command=='DELETE': c.execute('UPDATE courses SET archived=1 WHERE id=? AND user_id=?',(cid,uid))
                    elif self.command=='PATCH':
                        title,url,description,source=course_input(data,old)
                        category=language_learning.category(data,old)
                        c.execute('UPDATE courses SET title=?,url=?,description=?,source=?,category=? WHERE id=? AND user_id=?',(title,url,description,source,category,cid,uid))
                    else: return self.reply(405, {'error':'不支持的操作'})
                    return self.reply(200, {'ok':True})
                if path == '/api/tasks' and self.command == 'POST':
                    if not isinstance(data,dict):raise ValueError('任务数据需为对象')
                    day = date.fromisoformat(data['day']).isoformat(); title = str(data['title']).strip()
                    planned = int(data['planned'])
                    if not title or len(title)>200 or not 1<=planned<=600: raise ValueError('请填写任务和 1–600 分钟预计用时')
                    category = str(data.get('category','其他')).strip()[:30] or '其他'
                    note = str(data.get('note','')); done = data.get('done',False)
                    if len(note)>10000: raise ValueError('笔记最多 10000 字')
                    if not isinstance(done,(bool,int)) or done not in (False,True,0,1): raise ValueError('完成状态应为 true 或 false')
                    started_at,finished_at,actual,pauses=task_timing(data,done=bool(done))
                    c.execute('INSERT INTO tasks(user_id,day,title,category,planned,note,actual,done,started_at,finished_at,pause_history) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid,day,title,category,planned,note,actual,int(done),started_at,finished_at,json.dumps(pauses,separators=(',',':'))))
                    return self.reply(201, {'ok':True})
                if path.startswith('/api/tasks/'):
                    tid = int(path.rsplit('/',1)[1])
                    # Serialize task read/modify/write operations. A second
                    # device must observe the first committed start/end time,
                    # rather than overwrite it from an earlier snapshot.
                    if self.command in ('PATCH','DELETE'):c.execute('BEGIN IMMEDIATE')
                    if not c.execute('SELECT id FROM tasks WHERE id=? AND user_id=? AND archived=0',(tid,uid)).fetchone(): return self.reply(404, {'error':'任务不存在'})
                    result = {'ok': True}
                    if self.command == 'DELETE': c.execute('DELETE FROM tasks WHERE id=? AND user_id=?',(tid,uid))
                    elif self.command == 'PATCH':
                        if not isinstance(data,dict):raise ValueError('任务数据需为对象')
                        old = c.execute('SELECT * FROM tasks WHERE id=? AND user_id=?',(tid,uid)).fetchone()
                        if data.get('action') == 'reschedule':
                            result = reschedule_task(c, old, data)
                        elif data.get('action')=='reset':
                            started_at,finished_at,actual,pauses=task_timing(data,old,bool(old['done']))
                            c.execute('UPDATE tasks SET actual=?,started_at=?,finished_at=?,pause_history=? WHERE id=? AND user_id=?',
                                      (actual,started_at,finished_at,json.dumps(pauses,separators=(',',':')),tid,uid))
                        else:
                            note = str(data.get('note',old['note']))
                            if len(note)>10000: raise ValueError('笔记最多 10000 字')
                            done=data.get('done',bool(old['done']))
                            if not isinstance(done,(bool,int)) or done not in (False,True,0,1):raise ValueError('完成状态应为 true 或 false')
                            started_at,finished_at,actual,pauses=task_timing(data,old,bool(done))
                            title = str(data.get('title',old['title'])).strip()
                            day = date.fromisoformat(data.get('day',old['day'])).isoformat()
                            planned = int(data.get('planned',old['planned']))
                            category = str(data.get('category',old['category'])).strip()[:30] or '其他'
                            if not title or len(title)>200 or not 1<=planned<=600: raise ValueError('请填写任务和 1–600 分钟预计用时')
                            c.execute('UPDATE tasks SET done=?,actual=?,note=?,title=?,day=?,planned=?,category=?,started_at=?,finished_at=?,pause_history=? WHERE id=? AND user_id=?',(int(bool(done)),actual,note,title,day,planned,category,started_at,finished_at,json.dumps(pauses,separators=(',',':')),tid,uid))
                    else: return self.reply(405, {'error':'不支持的操作'})
                    c.commit()
                    return self.reply(200, result)
                return self.reply(404, {'error':'接口不存在'})
        except (ValueError, KeyError, TypeError) as e: self.reply(400, {'error':str(e) or '输入有误'})
        except Exception: self.reply(500, {'error':'服务器发生错误，请重试'})

if __name__ == '__main__':
    port = int(os.environ.get('STUDY_PORT','8001'))
    init(); print(f'API running: http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
