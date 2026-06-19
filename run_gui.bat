@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "PYTHON_EXE=python"
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
    set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe"
)

set "LOG_FILE=%SCRIPT_DIR%gui_startup.log"
echo [%date% %time%] Starting Vue GUI with "%PYTHON_EXE%" > "%LOG_FILE%"
echo Working directory: "%SCRIPT_DIR%" >> "%LOG_FILE%"

"%PYTHON_EXE%" -m photo_splitter.web_app >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo.
    echo Vue GUI failed to start. Install dependencies with:
    echo "%PYTHON_EXE%" -m pip install -r "%SCRIPT_DIR%requirements.txt"
    echo.
    echo If web_ui was changed, rebuild it with:
    echo cd /d "%SCRIPT_DIR%web_ui" ^&^& npm install ^&^& npm run build
    echo.
    echo Startup log:
    type "%LOG_FILE%"
    pause
    exit /b 1
)
