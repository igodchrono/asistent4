@echo on
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0data"
echo ========================================
echo  Lisichka DEBUG - live console
echo ========================================
echo Folder: %CD%

set LISICHKA_DEBUG=1
set DEBUG_CONSOLE=1
set LOG_LEVEL=DEBUG
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

set "PY=%~dp0python\python.exe"
if not exist "%PY%" (
  where python >nul 2>&1 && set "PY=python"
)

echo Using Python: %PY%
echo LISICHKA_DEBUG=%LISICHKA_DEBUG% LOG_LEVEL=%LOG_LEVEL%
echo ========================================
echo.

"%PY%" -u main.py
set ERR=%ERRORLEVEL%

echo.
echo ========================================
echo Exit code: %ERR%
echo Logs: data\logs\assistant_YYYYMMDD.log
echo ========================================
pause
endlocal
