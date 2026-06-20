@echo off
set PYTHONMALLOC=malloc
echo Starting backend with PYTHONMALLOC=malloc (fix Python 3.13 crash)...
python Backend\app.py
