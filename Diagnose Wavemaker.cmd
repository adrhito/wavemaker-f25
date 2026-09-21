@echo off
REM ---------------------------------------------------------------------------
REM  Wavemaker System Control -- diagnose a machine that will not start it
REM
REM  Double-click this when 'Open Wavemaker.cmd' flashes up and disappears.
REM
REM  That flash is not the fault. It is the launcher doing its job: it starts
REM  the application with pythonw.exe -- the WINDOWLESS interpreter, so that no
REM  console sits behind the interface -- and then exits, which closes its own
REM  window. A windowless interpreter has no stdout and no stderr, so if Python
REM  or the application fails on the way up, the error is written nowhere and
REM  the operator sees exactly this: a flash, and no application.
REM
REM  This window stays open and shows what that error actually was.
REM
REM  Nothing here contacts the PLC unless you answer Y at step 5.
REM ---------------------------------------------------------------------------

setlocal
cd /d "%~dp0"
title Wavemaker - startup diagnosis

echo ===========================================================
echo   Wavemaker System Control - startup diagnosis
echo ===========================================================
echo   Folder:  %~dp0
echo   Machine: %OS% %PROCESSOR_ARCHITECTURE%
echo.

REM --- Step 1: what Pythons are on this machine? -----------------------------
echo --- Step 1: Python installations ---------------------------
echo.
echo Versions the 'py' launcher knows about:
py -0p 2>nul || echo    (no py launcher, or one too old to list them)
echo.
echo Interpreters at the usual install paths:
call :look "%LOCALAPPDATA%\Programs\Python\Python38\python.exe"
call :look "%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe"
call :look "%LOCALAPPDATA%\Programs\Python\Python37\python.exe"
call :look "%LOCALAPPDATA%\Programs\Python\Python37-32\python.exe"
call :look "C:\Python38\python.exe"
call :look "C:\Python37\python.exe"
call :look "C:\Program Files\Python38\python.exe"
call :look "C:\Program Files\Python37\python.exe"
call :look "C:\Program Files (x86)\Python38-32\python.exe"
call :look "C:\Program Files (x86)\Python37-32\python.exe"
echo.

REM --- Step 2: pick one that can actually run here ---------------------------
REM  Newest-first is wrong on Windows 7. Python 3.9 and up will not run there
REM  at all, and the py launcher's plain -3 picks the newest it can find -- so
REM  on a Windows 7 machine that also has a 3.9+ installed, -3 selects an
REM  interpreter that dies before it prints anything. 3.8 and 3.7 are therefore
REM  preferred explicitly, by full path first.
set "PYEXE="
set "PYARG="
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python38\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python37\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python37-32\python.exe"
    "C:\Python38\python.exe"
    "C:\Python37\python.exe"
    "C:\Program Files\Python38\python.exe"
    "C:\Program Files\Python37\python.exe"
    "C:\Program Files (x86)\Python38-32\python.exe"
    "C:\Program Files (x86)\Python37-32\python.exe"
) do (
    if not defined PYEXE if exist %%P set "PYEXE=%%~P"
)
if defined PYEXE goto :got_python

if not exist "%SystemRoot%\py.exe" goto :try_path
call :try_launcher 3.8
if defined PYEXE goto :got_python
call :try_launcher 3.7
if defined PYEXE goto :got_python
call :try_launcher 3
if defined PYEXE goto :got_python

:try_path
where py >nul 2>&1 && set "PYEXE=py" && set "PYARG=-3"
if defined PYEXE goto :got_python
where python >nul 2>&1 && set "PYEXE=python"
if defined PYEXE goto :got_python
goto :nopython

:got_python
echo --- Step 2: the interpreter this check will use ------------
echo.
echo    %PYEXE% %PYARG%
echo.
"%PYEXE%" %PYARG% -c "import sys;print('   version: '+sys.version.split()[0]);print('   exe:     '+sys.executable)"
if errorlevel 1 goto :dead_python
echo.

REM --- Step 3: the environment check -----------------------------------------
echo --- Step 3: environment check ------------------------------
echo.
"%PYEXE%" %PYARG% "%~dp0docs\check_python.py"
echo.

REM --- Step 4: anything the application already recorded ---------------------
echo --- Step 4: last recorded startup failure ------------------
echo.
if exist "%~dp0logs\startup-error.txt" goto :show_error
echo    No logs\startup-error.txt. Either the application has never got far
echo    enough to record a crash, or it has never crashed. Step 5 will show
echo    any error as it happens.
goto :step5

:show_error
echo    logs\startup-error.txt says:
echo.
type "%~dp0logs\startup-error.txt"

:step5
echo.
echo --- Step 5: start the application in THIS window -----------
echo.
echo    This runs the real application with a console attached, so any error
echo    is printed here instead of being thrown away. It behaves exactly as
echo    'Open Wavemaker.cmd' does otherwise, INCLUDING talking to the PLC and
echo    moving pistons. Answer N if the machine must not be touched.
echo.
set "ANSWER="
set /p "ANSWER=   Start it now? [Y/N] "
if /i "%ANSWER%"=="Y" goto :run
echo.
echo    Not started.
goto :done

:run
echo.
echo -----------------------------------------------------------
"%PYEXE%" %PYARG% "%~dp0main.py" %*
echo -----------------------------------------------------------
echo    The application exited with code %ERRORLEVEL%.
goto :done

:dead_python
echo.
echo    *** That interpreter could not run at all.
echo    ***
echo    *** On Windows 7 this is exactly what Python 3.9 or newer does: it is
echo    *** not supported there, and it fails before printing anything. If a
echo    *** 3.9+ is installed on this machine, the py launcher's -3 will keep
echo    *** choosing it. Install Python 3.8 -- the last version with a
echo    *** Windows 7 installer -- and tick 'tcl/tk and IDLE' in the installer.
echo    ***
echo    *** See docs\OPERATING.md, section 'Running on Windows 7'.
goto :done

:nopython
echo --- No Python found ----------------------------------------
echo.
echo    No Python interpreter could be found on this machine.
echo.
echo    Windows 7 supports Python up to 3.8. Install Python 3.8 or 3.7 and
echo    tick BOTH 'tcl/tk and IDLE' and 'py launcher' in the installer.
echo.
echo    If Python is installed somewhere unusual, run the application
echo    directly to see the error:
echo        C:\your\path\python.exe "%~dp0main.py"
goto :done

REM --- subroutines -----------------------------------------------------------

:try_launcher
REM  %1 is a version such as 3.8. Sets PYEXE/PYARG only if that version both
REM  exists and starts. Kept out of the main flow because an 'if' block cannot
REM  test the errorlevel of a command in the same block without surprises.
"%SystemRoot%\py.exe" -%1 -c "pass" >nul 2>&1
if errorlevel 1 goto :eof
set "PYEXE=%SystemRoot%\py.exe"
set "PYARG=-%1"
goto :eof

:look
REM  %1 stays quoted throughout. Echoing it unquoted would put the ')' of
REM  'Program Files (x86)' into the middle of a batch block and end it early,
REM  which is a parse error, not a runtime one -- the whole file stops.
if exist %1 goto :look_found
echo    -       %1
goto :eof
:look_found
echo    FOUND   %1
goto :eof

:done
echo.
echo ===========================================================
echo   Diagnosis finished.
echo.
echo   Sending this to somebody? Right-click the title bar,
echo   Edit ^> Select All, then Enter to copy the whole window,
echo   and attach logs\startup-error.txt if it exists.
echo ===========================================================
echo.
pause
endlocal
