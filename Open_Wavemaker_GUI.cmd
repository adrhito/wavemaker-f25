@echo off
REM ---------------------------------------------------------------------------
REM  Wavemaker System Control -- launcher
REM
REM  Works from wherever this folder happens to live: %~dp0 is the directory
REM  this script is in.  The old launcher lived on the admin account's Desktop
REM  and hard-coded "C:\Users\admin\Desktop\WaveMaker_F25", so it had to be
REM  edited by hand on every machine and broke whenever the folder moved --
REM  which is what the install notes in the old README were working around.
REM
REM  Pass --simulate to run the interface without the machine, for training.
REM ---------------------------------------------------------------------------

setlocal
cd /d "%~dp0"

REM --- Step 1: open the PLC project so the operator can go online ------------
set "ACD=%~dp0..\WaveMaker Programs\Studio 5000\Wavemaker_for_Python.ACD"
if exist "%ACD%" (
    echo Opening the Studio 5000 project...
    start "" "%ACD%"
    echo.
    echo   In Studio 5000: Go Online, then put the controller in Rem Run.
    echo.
    pause
) else (
    echo NOTE: Studio 5000 project not found at:
    echo   %ACD%
    echo Open it yourself and go online before continuing.
    echo.
    pause
)

REM --- Step 2: start the interface -------------------------------------------
REM  py.exe is the Windows Python launcher and is what is actually installed;
REM  plain "python" resolves to the Microsoft Store stub on this machine and
REM  fails with "Python was not found".
echo Starting Wavemaker System Control...
where py >nul 2>&1
if %ERRORLEVEL%==0 (
    py -3 main.py %*
) else (
    python main.py %*
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo The application exited with an error. The log for today is in:
    echo   %~dp0logs
    pause
)
endlocal
