@echo off
REM Double-click to start the dashboard on Windows.
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python is not installed. Get it from https://www.python.org/downloads/
  echo During install, tick "Add python.exe to PATH". Then run this file again.
  pause
  exit /b 1
)

if not exist .venv (
  echo First run: setting things up, this takes a minute...
  %PY% -m venv .venv || (echo Could not create the virtual environment. & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q --disable-pip-version-check -r requirements.txt || (echo Installing packages failed. & pause & exit /b 1)

if not exist config.yaml copy config.example.yaml config.yaml >nul
if not exist .env copy .env.example .env >nul

echo.
echo Dashboard running at http://127.0.0.1:8000  (close this window to stop it)
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000"
python -m app.main
pause
