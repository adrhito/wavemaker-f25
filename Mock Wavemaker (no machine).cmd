@echo off
REM ---------------------------------------------------------------------------
REM  Wavemaker System Control -- MOCK
REM
REM  Runs a simulated wavemaker. No PLC is contacted and nothing physical can
REM  move, so this is safe to run anywhere: a laptop, at home, in a lecture.
REM
REM  The pistons in the mock really do stroke between Position 1 and Position 2
REM  at the speeds you set, homing really takes a moment, and a Curve Offset
REM  staggered front to back really does produce a wave that travels along the
REM  chamber. Use it to learn the interface and to build presets before you go
REM  anywhere near the tank.
REM
REM  It does NOT predict how the real machine behaves. It moves rectangles.
REM
REM  For the real machine, use "Open Wavemaker.cmd" instead.
REM ---------------------------------------------------------------------------

setlocal
cd /d "%~dp0"

REM  Same interpreter search as 'Open Wavemaker.cmd', and for the same reason:
REM  on Windows 7 the py launcher's -3 picks the newest Python installed, and
REM  anything from 3.9 up cannot run there at all -- it dies before printing
REM  anything, which looks exactly like this launcher being broken. Known-good
REM  3.8/3.7 by full path first, then the launcher asked for a version by name,
REM  then the generic fallbacks. If this window flashes and nothing appears,
REM  run 'Diagnose Wavemaker.cmd'.
set "PYW="
set "PYWARG="
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python38\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python38-32\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python37\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python37-32\pythonw.exe"
    "C:\Python38\pythonw.exe"
    "C:\Python37\pythonw.exe"
    "C:\Program Files\Python38\pythonw.exe"
    "C:\Program Files\Python37\pythonw.exe"
    "C:\Program Files (x86)\Python38-32\pythonw.exe"
    "C:\Program Files (x86)\Python37-32\pythonw.exe"
) do (
    if not defined PYW if exist %%P set "PYW=%%~P"
)
if defined PYW goto :launch

call :pin 3.8
if defined PYW goto :launch
call :pin 3.7
if defined PYW goto :launch

if exist "%SystemRoot%\pyw.exe" (
    set "PYW=%SystemRoot%\pyw.exe"
    set "PYWARG=-3"
)
if defined PYW goto :launch
where pyw >nul 2>&1 && set "PYW=pyw" && set "PYWARG=-3"
if defined PYW goto :launch
where pythonw >nul 2>&1 && set "PYW=pythonw"
if defined PYW goto :launch
where py >nul 2>&1 && set "PYW=py" && set "PYWARG=-3"
if defined PYW goto :launch
where python >nul 2>&1 && set "PYW=python"
if defined PYW goto :launch
goto :nopython

:launch
start "" "%PYW%" %PYWARG% "%~dp0main.py" --mock %*
goto :eof

:pin
if not exist "%SystemRoot%\py.exe" goto :eof
if not exist "%SystemRoot%\pyw.exe" goto :eof
"%SystemRoot%\py.exe" -%1 -c "pass" >nul 2>&1
if errorlevel 1 goto :eof
set "PYW=%SystemRoot%\pyw.exe"
set "PYWARG=-%1"
goto :eof

:nopython
echo Could not find Python on this machine.
echo.
echo Install Python 3 from python.org. Windows 7 supports up to 3.8;
echo anything from 3.7 onwards works.
echo.
echo Or double-click 'Diagnose Wavemaker.cmd', which lists every Python it
echo can find and says what is wrong.
pause
goto :eof
