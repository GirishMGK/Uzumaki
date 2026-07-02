@echo off
REM ============================================================
REM  Form 26AS Formatter - double-click to run.
REM  First run sets up Python dependencies (one-time, ~1 minute).
REM  Drag one or more 26AS files onto this .bat to convert several
REM  years at once (select them all in Explorer, then drag together).
REM ============================================================
setlocal
title Form 26AS Formatter
cd /d "%~dp0"

echo Form 26AS Formatter
echo ====================
echo.

REM --- Find Python -------------------------------------------------
echo Checking for Python...
set "PY=py -3"
%PY% --version >nul 2>&1
if errorlevel 1 set "PY=python"
%PY% --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python was not found on this computer.
    echo Please install Python 3.9+ from https://www.python.org/downloads/
    echo During installation:
    echo   - tick "Add Python to PATH"
    echo   - leave "tcl/tk and IDLE" ticked ^(needed for the file-picker window^)
    echo Then double-click this file again.
    echo.
    goto :end
)
echo   found.

REM --- Create the virtual environment on first run ----------------
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Setting up for first use, please wait ^(about a minute^)...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create the Python virtual environment.
        goto :end
    )
)

set "VPY=.venv\Scripts\python.exe"

REM --- Install dependencies once ----------------------------------
if not exist ".venv\.installed" (
    echo Installing dependencies, this happens only once...
    "%VPY%" -m pip install --upgrade pip
    "%VPY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed. Check your internet connection
        echo and try again.
        goto :end
    )
    echo done> ".venv\.installed"
    echo   done.
)

REM --- Make sure the GUI toolkit is available -----------------------
"%VPY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] This Python install is missing the Tkinter GUI library, so the
    echo file-picker window cannot open. Reinstall Python from
    echo https://www.python.org/downloads/ and leave "tcl/tk and IDLE" ticked.
    echo.
    echo In the meantime you can still run this from the command line, e.g.:
    echo   "%VPY%" -m form26as "C:\path\to\26AS.txt" "C:\path\to\another26AS.txt"
    echo.
    goto :end
)

REM --- Launch the picker -------------------------------------------
REM %* passes along every file you dropped onto this .bat (if any).
echo.
echo Opening the file picker window now...
echo ^(it may appear behind this window - check your taskbar^)
echo.
"%VPY%" -m form26as.gui %*
if errorlevel 1 (
    echo.
    echo [ERROR] The formatter reported a problem - see any pop-up window for details.
)

:end
echo.
echo ------------------------------------------------------------
pause
endlocal
