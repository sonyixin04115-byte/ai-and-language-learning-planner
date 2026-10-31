"""Paste into the WSGI file linked from PythonAnywhere's Web page.
Replace YOURUSERNAME in all three places before Reload.
"""
import os
import sys

project = "/home/YOURUSERNAME/ai-study"
sys.path.insert(0, project)
os.environ["STUDY_PUBLIC_ORIGIN"] = "https://YOURUSERNAME.pythonanywhere.com"
os.environ["STUDY_DB"] = "/home/YOURUSERNAME/ai-study/data/study.db"
os.environ["STUDY_ALLOW_REGISTRATION"] = "0"
from deploy.wsgi_app import create_app
application = create_app()
