@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 marsi.py --run
) else (
    python marsi.py --run
)
if errorlevel 1 (
    echo.
    echo Marsi could not start. Read the error above and see README.md.
    pause
)
