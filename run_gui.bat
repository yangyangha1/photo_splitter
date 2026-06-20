@echo off
setlocal
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "LOG_FILE=%SCRIPT_DIR%gui_startup.log"
set "LAUNCHER_DIR=%LocalAppData%\PhotoSplitter\Launcher"
set "LAUNCHER_EXE=%LAUNCHER_DIR%\PhotoSplitterLauncher.exe"
set "BUILD_LAUNCHER=%SCRIPT_DIR%photo_splitter\launcher\build_launcher.ps1"
set "PAYLOAD_SCRIPT=%SCRIPT_DIR%photo_splitter\start_gui_payload.ps1"

echo [%date% %time%] Preparing GUI launcher > "%LOG_FILE%"
echo Working directory: "%SCRIPT_DIR%" >> "%LOG_FILE%"

if not exist "%BUILD_LAUNCHER%" (
    echo Launcher build script was not found: "%BUILD_LAUNCHER%" >> "%LOG_FILE%"
    type "%LOG_FILE%"
    pause
    exit /b 1
)

if not exist "%PAYLOAD_SCRIPT%" (
    echo GUI payload script was not found: "%PAYLOAD_SCRIPT%" >> "%LOG_FILE%"
    type "%LOG_FILE%"
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%BUILD_LAUNCHER%" -OutputPath "%LAUNCHER_EXE%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo.
    echo Launcher build failed.
    echo.
    echo Startup log:
    type "%LOG_FILE%"
    pause
    exit /b 1
)

set "PHOTO_SPLITTER_WORKDIR=%SCRIPT_DIR%"
start "" "%LAUNCHER_EXE%" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%PAYLOAD_SCRIPT%"
