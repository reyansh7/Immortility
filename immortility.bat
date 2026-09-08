@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PY=%ROOT%\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Immortility: venv not found at "%PY%"
  echo Create it with: python -m venv venv
  echo Then: "%ROOT%\venv\Scripts\python.exe" -m pip install -r "%ROOT%\requirements.txt"
  exit /b 1
)
cd /d "%ROOT%"
"%PY%" "%ROOT%\main.py" %*
