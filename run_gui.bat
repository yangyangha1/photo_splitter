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
set "WEB_UI_DIR=%SCRIPT_DIR%photo_splitter\web_ui"
echo [%date% %time%] Starting Vue GUI with "%PYTHON_EXE%" > "%LOG_FILE%"
echo Working directory: "%SCRIPT_DIR%" >> "%LOG_FILE%"

where npm >nul 2>&1
if errorlevel 1 (
    echo.
    echo Vue UI build failed: npm was not found.
    echo Please install Node.js first, then run this file again.
    echo.
    echo Startup log:
    type "%LOG_FILE%"
    pause
    exit /b 1
)

if not exist "%WEB_UI_DIR%\package.json" (
    echo.
    echo Vue UI build failed: package.json was not found.
    echo Expected path: "%WEB_UI_DIR%"
    echo.
    pause
    exit /b 1
)

echo Building Vue UI before startup... >> "%LOG_FILE%"
pushd "%WEB_UI_DIR%"
if not exist "node_modules" (
    echo Installing Vue UI dependencies... >> "%LOG_FILE%"
    call npm install >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        popd
        echo.
        echo Vue UI dependency install failed.
        echo.
        echo Startup log:
        type "%LOG_FILE%"
        pause
        exit /b 1
    )
)

call npm run build >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    popd
    echo.
    echo Vue UI build failed.
    echo.
    echo Startup log:
    type "%LOG_FILE%"
    pause
    exit /b 1
)
popd

echo Vue UI build completed. >> "%LOG_FILE%"

"%PYTHON_EXE%" -m photo_splitter.web_app >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo.
    echo Vue GUI failed to start. Install dependencies with:
    echo "%PYTHON_EXE%" -m pip install -r "%SCRIPT_DIR%photo_splitter\requirements.txt"
    echo.
    echo Startup log:
    type "%LOG_FILE%"
    pause
    exit /b 1
)
