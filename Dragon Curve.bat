@echo off
setlocal
cd /d "%~dp0"
py -3 -V >nul 2>&1
if not errorlevel 1 (
    py -3 dragon_curve.py --menu
    goto done
)
python -V >nul 2>&1
if not errorlevel 1 (
    python dragon_curve.py --menu
    goto done
)
echo Python 3.10 or newer is required. Install Python 3 and try again.
:done
echo.
pause
endlocal
