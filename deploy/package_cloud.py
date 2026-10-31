"""Create a small, private migration archive; never uploads anything."""
import argparse
import json
from pathlib import Path
import sqlite3
import tempfile
import zipfile

PROJECT = Path(__file__).resolve().parent.parent


def package(username, output):
    source_db = PROJECT/"backend/study.db"
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zhixu-cloud-") as temp:
        target = Path(temp)/"study.db"
        source = sqlite3.connect(source_db.as_uri()+"?mode=ro",uri=True)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            source.close()
        try:
            uid = destination.execute("SELECT id FROM users WHERE username=?",(username,)).fetchone()
            if not uid:
                raise ValueError("用户名不存在")
            uid = uid[0]
            destination.execute("DELETE FROM sessions")
            for table in ("tasks","courses","course_defaults"):
                destination.execute(f"DELETE FROM {table} WHERE user_id<>?",(uid,))
            destination.execute("DELETE FROM users WHERE id<>?",(uid,))
            destination.commit()
            destination.execute("VACUUM")
        finally:
            destination.close()
        files = list((PROJECT/"backend").glob("*.py"))
        files += [PROJECT/"backend/materials_catalog.json"]
        files += list((PROJECT/"frontend").glob("*.html"))+list((PROJECT/"frontend").glob("*.css"))+list((PROJECT/"frontend").glob("*.js"))
        files += [PROJECT/"deploy/wsgi_app.py",PROJECT/"deploy/pythonanywhere_wsgi.example.py",PROJECT/"deploy/云端部署说明.txt"]
        with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED) as archive:
            for file in files:
                if file.exists():
                    archive.write(file,"ai-study/"+file.relative_to(PROJECT).as_posix())
            archive.write(target,"ai-study/data/study.db")
    return {"archive":str(output),"bytes":output.stat().st_size,"username":username,"sessions":0,"video_files":0}


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--username",required=True);parser.add_argument("--output",default=str(PROJECT/"deploy/ready/ai-study-cloud.zip"));args=parser.parse_args()
    print(json.dumps(package(args.username,args.output),ensure_ascii=False))
