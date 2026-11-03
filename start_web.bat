@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PORT=8765"
set "URL=http://127.0.0.1:%PORT%"

title Storyfinder
echo ============================================
echo   Storyfinder - Author.Today search
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo Python was not found in PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Installing or updating dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency installation failed. Check Python and pip.
    pause
    exit /b 1
)

rem Stop a previous copy of this server, otherwise the browser may keep
rem talking to an old process that still serves the old interface.
set "BUSY_FOREIGN=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
    for /f "tokens=1 delims=," %%N in ('tasklist /FI "PID eq %%P" /FO CSV /NH') do (
        echo %%N | findstr /I "python" >nul
        if not errorlevel 1 (
            echo Stopping previous Storyfinder server (PID %%P^)...
            taskkill /PID %%P /F >nul
        ) else (
            echo Port %PORT% is already used by another program.
            set "BUSY_FOREIGN=1"
        )
    )
)
if "!BUSY_FOREIGN!"=="1" (
    echo Close that program and run start_web.bat again.
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul

echo.
echo Starting Storyfinder at %URL%
echo Close this window to stop the server.
echo.

rem Open the browser only after the server answers /api/health.
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command "for ($i = 0; $i -lt 60; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing '%URL%/api/health' -TimeoutSec 1; if ($r.StatusCode -eq 200) { Start-Process '%URL%'; break } } catch {}; Start-Sleep -Milliseconds 500 }"

python -u webapp.py

echo.
echo Storyfinder stopped.
pause
