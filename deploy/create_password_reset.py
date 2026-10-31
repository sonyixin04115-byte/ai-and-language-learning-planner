"""Owner-only helper. Never include its generated private JSON in a repository."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import time
from urllib.parse import quote
from wsgi_app import public_origin


def create_request(username,origin,output,minutes=15):
    origin=public_origin(origin)
    if not 3<=len(username)<=40 or not 1<=minutes<=15:
        raise ValueError('Username must be 3–40 characters; expiry 1–15 minutes')
    token=secrets.token_urlsafe(32)
    request={'version':1,'username':username,'token_hash':hashlib.sha256(token.encode()).hexdigest(),'expires':time.time()+minutes*60}
    target=Path(output)
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.name+'.tmp')
    temp.write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
    try: temp.chmod(0o600)
    except OSError: pass
    temp.replace(target)
    return origin+'/reset-password?token='+quote(token,safe='')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Create an owner-authorized one-time password reset request')
    parser.add_argument('--username',required=True)
    parser.add_argument('--origin',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--minutes',type=int,default=15)
    args=parser.parse_args()
    print(create_request(args.username,args.origin,args.output,args.minutes))
