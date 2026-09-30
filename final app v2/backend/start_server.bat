@echo off
rem Demo server for the Teachable Assistant app. The phone connects to this PC over Wi-Fi.
rem Needs backend\.env with OPENROUTER_API_KEY=... (see .env.example).
cd /d "%~dp0"

if not exist ".env" (
  echo backend\.env is missing. Copy .env.example to .env and set OPENROUTER_API_KEY.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo First run: creating the Python environment and installing packages...
  python -m venv .venv || goto :error
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

echo.
echo Phone setup: in the app's Device setup, set the Backend URL to one of these (same Wi-Fi):
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo     http:/%%a:8000
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
goto :eof

:error
echo Setup failed.
pause
exit /b 1
