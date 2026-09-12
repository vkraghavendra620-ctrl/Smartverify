@echo off
setlocal
cd /d "%~dp0"

echo ======================================================================
echo   SmartVerify - Autonomous AI Loan Verification System
echo ======================================================================
echo.

rem Check if virtual environment python exists
if exist "%~dp0backend\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0backend\.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

rem Run unified single-project runner
"%PYTHON_EXE%" run.py %*

if errorlevel 1 (
    echo.
    echo [!] Server terminated or encountered an error.
    pause
)
