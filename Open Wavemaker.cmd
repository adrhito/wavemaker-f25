@echo off
REM ---------------------------------------------------------------------------
REM  Wavemaker System Control
REM
REM  Double-click this. That is the whole procedure.
REM
REM  It does NOT open Studio 5000 and does NOT wait for a keypress. The
REM  application talks to the PLC directly over EtherNet/IP and opens its own
REM  session; Studio 5000 "Go Online" connects Studio 5000 to the controller,
REM  not this application.
REM
REM  The one thing that does matter is that the controller is in RUN, because
REM  the ladder logic has to be scanning. That is persistent -- it stays in Run
REM  until somebody changes it -- so it is not a per-launch step. If the
REM  application cannot reach the controller it says so on screen and offers
REM  Reconnect and Open Studio 5000 buttons.
REM
REM  Pass --simulate to run the interface with no machine at all, for training.
REM ---------------------------------------------------------------------------

setlocal
cd /d "%~dp0"

REM --- Optional: the analytics database ---------------------------------------
REM  Only used to store analytics runs. The application works without it and
REM  writes analytics to analytics\<date>.txt either way, so this never blocks
REM  startup and a failure here is not reported.
call :start_mongo

REM --- Start the application --------------------------------------------------
REM  pythonw.exe / pyw.exe are the windowless Windows interpreters: no console
REM  window sits behind the interface. That is also why a failure on the way up
REM  used to be invisible. A windowless interpreter has no stdout and no stderr,
REM  so a traceback went nowhere and all the operator saw was this window flash
REM  and close. main.py now catches that and writes logs\startup-error.txt and
REM  a message box, and 'Diagnose Wavemaker.cmd' shows the lot in a window that
REM  stays open. If this ever flashes and nothing appears, run that.
REM
REM  ORDER MATTERS, and it is not the obvious one. The lab PC is Windows 7,
REM  where Python 3.9 and newer will not run AT ALL -- they fail before printing
REM  anything. The py launcher's plain -3 picks the NEWEST Python installed. So
REM  on a Windows 7 machine that also has a 3.9+ lying around, "pyw -3" chooses
REM  an interpreter that dies instantly and silently, which looks exactly like
REM  the application being broken. Hence: a known-good 3.8/3.7 by full path
REM  first, then the launcher asked for 3.8 or 3.7 BY NAME, and only then the
REM  generic newest-wins fallbacks, which are right on a modern machine.
REM
REM  Plain "python" stays last -- on lab machines it is often the Microsoft
REM  Store stub, which fails with "Python was not found".
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
REM  Console fallbacks: these leave a window open behind the interface, but
REM  starting is better than not starting.
where py >nul 2>&1 && set "PYW=py" && set "PYWARG=-3"
if defined PYW goto :launch
where python >nul 2>&1 && set "PYW=python"
if defined PYW goto :launch
goto :nopython

:launch
start "" "%PYW%" %PYWARG% "%~dp0main.py" %*
goto :eof

:pin
REM  %1 is a version such as 3.8. py.exe (console) is used only to TEST that
REM  the version exists and actually starts; pyw.exe (windowless) is what then
REM  launches it. Testing with the console build is the point -- a 3.9 on
REM  Windows 7 fails here, quietly, instead of being launched and vanishing.
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
echo Windows 7 supports Python up to 3.8. If Python is installed but not on
echo PATH, run it directly, for example:
echo     C:\Python37\pythonw.exe "%~dp0main.py"
echo.
echo Or double-click 'Diagnose Wavemaker.cmd', which lists every Python it
echo can find and says what is wrong.
pause
goto :eof


:start_mongo
REM Already running? Leave it alone.
tasklist /fi "imagename eq mongod.exe" 2>nul | find /i "mongod.exe" >nul && goto :eof
set "MONGOD=C:\Program Files\MongoDB\Server\7.0\bin\mongod.exe"
if not exist "%MONGOD%" goto :eof
if not exist "C:\data\db" goto :eof
start "MongoDB" /min "%MONGOD%" --dbpath="C:\data\db"
goto :eof
