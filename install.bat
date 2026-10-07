@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PY=%~dp0python\python.exe"
if not exist "%PY%" (
  where python >nul 2>&1 && set "PY=python"
)
if "%PY%"=="" (
  echo Python not found. Put portable python next to this file or install Python.
  pause
  exit /b 1
)

set "DATA=%~dp0data"
if not exist "%DATA%\requirements.txt" if exist "%~dp0requirements.txt" set "DATA=%~dp0"

echo ========================================
echo  Lisichka install
echo  Python: %PY%
echo  Deps:   %DATA%
echo ========================================
echo.

echo [1/5] pip upgrade
"%PY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 echo pip upgrade warning, continue...

echo.
echo [2/5] requirements.txt
if exist "%DATA%\requirements.txt" (
  "%PY%" -m pip install -r "%DATA%\requirements.txt"
) else (
  echo requirements.txt not found
)

if exist "%DATA%\REQUIREMENTS_VOICE.txt" (
  echo.
  echo [2b] voice extras
  "%PY%" -m pip install -r "%DATA%\REQUIREMENTS_VOICE.txt"
)

echo.
echo [3/5] CUDA torch
where nvidia-smi >nul 2>&1
if errorlevel 1 (
  echo nvidia-smi not found: leave CPU torch from requirements
  goto PYAUDIO
)

nvidia-smi -L
echo Replacing torch with CUDA 12.4 wheels...
"%PY%" -m pip uninstall -y torch torchvision torchaudio
"%PY%" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
if errorlevel 1 (
  echo cu124 failed, trying cu121...
  "%PY%" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
)
"%PY%" -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"

:PYAUDIO
echo.
echo [4/5] PyAudio
"%PY%" -c "import pyaudio" >nul 2>&1
if not errorlevel 1 (
  echo PyAudio already ok
  goto CHECK
)
"%PY%" -m pip install pipwin
"%PY%" -m pipwin install pyaudio
if errorlevel 1 "%PY%" -m pip install pyaudio
"%PY%" -c "import pyaudio" >nul 2>&1
if errorlevel 1 (
  echo PyAudio failed. Mic may not work. Install a wheel for your Python later.
)

:CHECK
echo.
echo [5/5] import check
"%PY%" -c "import PyQt5, aiohttp, PIL; print('core ok')"
if exist "%DATA%\check_install.py" (
  "%PY%" "%DATA%\check_install.py"
)

echo.
echo Done. Start with start.bat
pause
endlocal
