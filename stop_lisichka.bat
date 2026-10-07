@echo on
chcp 65001 >nul
echo Stopping Lisichka / python related to asistent...
cd /d "%~dp0"

rem kill python that runs main.py from this folder
for /f "tokens=2 delims=," %%P in ('tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH') do (
  echo Found python PID %%~P
)

rem Force-kill python.exe started for this project (careful: all python)
wmic process where "CommandLine like '%%asistent%%main.py%%'" call terminate >nul 2>&1
wmic process where "CommandLine like '%%asistent2%%main.py%%'" call terminate >nul 2>&1

timeout /t 2 /nobreak >nul
echo Done. If apps.db still locked, close remaining python.exe in Task Manager.
pause
