@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PYTHON_EXE=python"
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
    set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe"
)

"%PYTHON_EXE%" -m photo_splitter.cli %*
if errorlevel 1 (
    echo.
    echo Split failed. Install dependencies with:
    echo "%PYTHON_EXE%" -m pip install -r "%SCRIPT_DIR%photo_splitter\requirements.txt"
    pause
    exit /b 1
)
