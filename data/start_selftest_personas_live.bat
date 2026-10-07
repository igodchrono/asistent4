@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0..\data\core" cd /d "%~dp0..\data"
if exist "%~dp0data\core" cd /d "%~dp0data"
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
set QT_OPENGL=software
set "PY=%~dp0..\python\python.exe"
if not exist "%PY%" set "PY=%~dp0..\..\python\python.exe"
if not exist "%PY%" set PY=python
echo Selftest PERSONAS LIVE FULL
echo LLM + browser (smart queries) + 18+ local image + memory
echo Using: %PY%
echo Folder: %CD%
"%PY%" -u selftest_personas_live.py --llm --browser
echo Exit %ERRORLEVEL%
echo Log: %CD%\selftest_personas_live.log
pause
