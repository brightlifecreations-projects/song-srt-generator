@echo off
REM Start the SRT Generator. Run Install.bat once first.
cd /d "%~dp0"
set "PATH=%~dp0bin;%PATH%"
set "HF_HOME=%~dp0models\hf"
set "PYTHONUTF8=1"

if not exist ".venv\Scripts\python.exe" (
  echo The app isn't installed yet. Double-click Install.bat first.
  pause
  exit /b 1
)

echo Starting SRT Generator - your browser will open in a moment.
echo Keep this window open while you use the app. Close it to stop.
.venv\Scripts\python.exe app.py
pause
