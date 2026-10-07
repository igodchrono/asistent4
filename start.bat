@echo off
setlocal
set "ROOT=%~dp0"
set "APP=%ROOT%data"
set "PYW=%ROOT%python\pythonw.exe"
if not exist "%PYW%" set "PYW=%ROOT%python\python.exe"
if not exist "%APP%\main.py" (
  echo [ERROR] main.py not found
  pause
  exit /b 1
)
if not exist "%PYW%" (
  echo [ERROR] pythonw not found
  pause
  exit /b 1
)
pushd "%APP%"
start "" "%PYW%" "%APP%\main.py"
popd
exit
