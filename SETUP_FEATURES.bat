@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0"
if errorlevel 1 exit /b 1
set "ESTIMATOR_PYTHON=py -3.12"
%ESTIMATOR_PYTHON% -c "import sys, struct; sys.exit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if not errorlevel 1 goto python_ready
set "ESTIMATOR_PYTHON=python"
%ESTIMATOR_PYTHON% -c "import sys, struct; sys.exit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if not errorlevel 1 goto python_ready
echo Python 3.12 x64 is required. Install it from https://www.python.org/downloads/windows/
echo Enable Python Launcher or Add python.exe to PATH, then reopen this file.
set "ESTIMATOR_EXIT=1"
goto finish
:python_ready
%ESTIMATOR_PYTHON% -X utf8 -B feature_setup.py %*
set "ESTIMATOR_EXIT=%ERRORLEVEL%"

:finish
popd
if "%ESTIMATOR_NO_PAUSE%"=="1" goto return
pause
:return
exit /b %ESTIMATOR_EXIT%
