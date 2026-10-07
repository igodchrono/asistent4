@echo on
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
set "PY=%~dp0..\python\python.exe"
if not exist "%PY%" set PY=python
"%PY%" -u main.py
pause
