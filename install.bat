@echo off
REM SRT Generator - one-time installer for Windows (x64).
REM Everything installs INSIDE this folder. Nothing on your PC is touched
REM (except one standard Microsoft component, if it's missing).
REM Needs internet once (Python runtime + AI models). Safe to re-run anytime.
setlocal
cd /d "%~dp0"

echo.
echo   SRT Generator - installing
echo   Everything stays inside this folder.
echo.

REM Only 64-bit Intel/AMD Windows is supported in this build.
if not "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
  echo X This build is for 64-bit Windows on Intel/AMD.
  echo   This PC reports: %PROCESSOR_ARCHITECTURE%. Ask Aman for help.
  pause
  exit /b 1
)

REM Windows' 260-character path limit: the app's Python packages nest deep folders, so a long
REM folder path can make the install fail. Warn (but carry on) unless long paths are switched on.
REM (goto, not a ( ) block: a path like "Program Files (x86)" would break the block.)
set "HERE=%~dp0"
"%SystemRoot%\System32\reg.exe" query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled 2>nul | "%SystemRoot%\System32\find.exe" "0x1" >nul
if not errorlevel 1 goto :pathok
if "%HERE:~70,1%"=="" goto :pathok
echo ! This folder's path is long:
echo   %HERE%
echo   Windows may refuse some of the app's files. If the install fails, move this
echo   folder somewhere short, like C:\SRT-Generator, and run Install.bat again.
echo.
:pathok

REM Bundled tools: uv (installs a private Python), ffmpeg/ffprobe (audio handling).
if not exist "bin\uv.exe" goto :missingbin
if not exist "bin\ffmpeg.exe" goto :missingbin
if not exist "bin\ffprobe.exe" goto :missingbin

set "PATH=%~dp0bin;%PATH%"
set "UV_PYTHON_INSTALL_DIR=%~dp0.python"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_CACHE_DIR=%~dp0.uv-cache"
set "HF_HOME=%~dp0models\hf"
set "PYTHONUTF8=1"

REM The AI engine needs Microsoft's standard C++ runtime. Most PCs have it;
REM install it quietly if this one doesn't.
set "NEEDVC="
if not exist "%SystemRoot%\System32\vcruntime140.dll" set "NEEDVC=1"
if not exist "%SystemRoot%\System32\msvcp140.dll" set "NEEDVC=1"
if defined NEEDVC (
  echo [0/3] Installing a small standard Microsoft component...
  echo       If Windows asks "allow this app to make changes?", click Yes.
  curl -sL -o "%TEMP%\vc_redist.x64.exe" https://aka.ms/vc_redist.x64.exe
  "%TEMP%\vc_redist.x64.exe" /install /passive /norestart
)
if not exist "%SystemRoot%\System32\vcruntime140.dll" goto :vcfail

echo [1/3] Setting up the app's private Python...
if not exist ".venv\Scripts\python.exe" (
  bin\uv.exe venv --quiet --python 3.10 .venv
  if errorlevel 1 goto :fail
)
echo       Now downloading the app's components - progress below.
echo       (Several minutes. The window is NOT stuck.)
bin\uv.exe pip install --python .venv\Scripts\python.exe -r requirements-windows.txt
if errorlevel 1 goto :fail
echo       done

echo [2/3] Downloading the AI models and doing a test run...
echo       (first time only - roughly 2 GB, can take 10-30 minutes)
.venv\Scripts\python.exe warmup.py
if errorlevel 1 goto :fail

echo.
echo       Optional: the Hinglish speech model (about 781 MB) writes Hindi speech
echo       straight in Roman letters. Skip it and it downloads the first time
echo       you use Hinglish.
choice /c YN /n /m "      Download it now? [Y/N] "
if not errorlevel 2 (
  .venv\Scripts\python.exe hinglish_model.py download
  if errorlevel 1 echo       Couldn't download it now - it downloads the first time you use Hinglish.
)

echo.
echo [3/3] All set.
echo.
echo   PASS - installation complete.
echo   From now on, just double-click  Start.bat  to use the app.
echo.
pause
exit /b 0

:missingbin
echo X The bin folder is missing from this download.
echo   Re-download the zip from Aman and unzip it again.
pause
exit /b 1

:vcfail
echo X Couldn't install the Microsoft component automatically.
echo   Open this link in your browser, run the file it downloads,
echo   then double-click this installer again:
echo   https://aka.ms/vc_redist.x64.exe
pause
exit /b 1

:fail
echo.
echo X Something didn't finish. No harm done - your PC is unchanged.
echo   1^) Check your internet connection
echo   2^) Double-click this installer again ^(it continues where it stopped^)
echo   3^) Still stuck? Send Aman a photo of this window.
echo.
pause
exit /b 1
