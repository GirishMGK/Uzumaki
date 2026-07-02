@echo off
REM ============================================================
REM  Form 26AS Formatter - double-click to run.
REM  First run sets up Python dependencies (one-time, ~1 minute).
REM  You can also drag a 26AS file directly onto this .bat file.
REM ============================================================
setlocal
cd /d "%~dp0"

REM --- Find Python -------------------------------------------------
set "PY=py -3"
%PY% --version >nul 2>&1
if errorlevel 1 set "PY=python"
%PY% --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo Python was not found on this computer.
    echo Please install Python 3.9+ from https://www.python.org/downloads/
    echo and tick "Add Python to PATH" during installation, then try again.
    echo.
    pause
    exit /b 1
)

REM --- Create the virtual environment on first run ----------------
if not exist ".venv\Scripts\python.exe" (
    echo Setting up for first use, please wait...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

set "VPY=.venv\Scripts\python.exe"

REM --- Install dependencies once ----------------------------------
if not exist ".venv\.installed" (
    echo Installing dependencies, this happens only once...
    "%VPY%" -m pip install --upgrade pip
    "%VPY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency installation failed. Check your internet connection.
        pause
        exit /b 1
    )
    echo done> ".venv\.installed"
)

REM --- Launch the picker (passes a dragged file if there is one) --
"%VPY%" -m form26as.gui "%~1"
if errorlevel 1 pause

endlocal
