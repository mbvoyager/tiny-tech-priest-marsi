@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 marsi.py --terminal
) else (
    python marsi.py --terminal
)
if errorlevel 1 (
    echo.
    echo Marsi could not start. Read the error above and see README.md.
    pause
)
