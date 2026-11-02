@echo off
setlocal
cd /d "%~dp0"

echo Installing or updating dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency installation failed. Check Python and pip.
    pause
    exit /b 1
)

echo.
echo Starting Storyfinder at http://127.0.0.1:8765
start "" "http://127.0.0.1:8765"
python webapp.py

echo.
echo Storyfinder stopped.
pause
