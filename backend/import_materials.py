"""Index user-provided materials without copying or executing their contents."""
import argparse,json
from pathlib import Path
import materials
import server

def import_for_user(username,root):
    data=materials.scan(root)
    server.init()
    with server.conn() as c:
        user=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
        if not user:raise ValueError('账号不存在，请先注册：'+username)
        for course in data['courses']:
            description=f"你的本地课程 · {course['collection']} · {sum(l['kind']=='video' for l in course['lessons'])} 个视频，{len(course['lessons'])} 项资料。按需查阅，不自动生成每日任务。"
            existing=c.execute('SELECT id FROM courses WHERE user_id=? AND local_key=?',(user['id'],course['local_key'])).fetchone()
            if existing:
                c.execute("UPDATE courses SET title=?,description=?,source='user',archived=0 WHERE id=?",(course['title'],description,existing['id']))
            else:
                c.execute("INSERT INTO courses(user_id,title,url,description,source,local_key) VALUES(?,?,'',?,'user',?)",(user['id'],course['title'],description,course['local_key']))
    # Atomic manifest write; no video bytes are copied.
    temp=materials.CATALOG_PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(materials.CATALOG_PATH)
    return {'courses':len(data['courses']),'videos':sum(l['kind']=='video' for course in data['courses'] for l in course['lessons']),'materials':sum(len(course['lessons']) for course in data['courses'])}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--username',required=True);parser.add_argument('--root',default='G:/人工智能学习');args=parser.parse_args()
    print(json.dumps(import_for_user(args.username,args.root),ensure_ascii=False))
