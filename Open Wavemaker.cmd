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
REM  pyw.exe is the windowless Windows Python launcher: no console window sits
REM  behind the interface. py.exe is the fallback (it shows a console).
REM  Plain "python" is deliberately last -- on lab machines it is often the
REM  Microsoft Store stub, which fails with "Python was not found".
REM  Tried in order: windowless launcher, windowless interpreter, then the
REM  console versions. The lab PC is Windows 7 and may not have the py
REM  launcher on PATH, so common install locations are checked directly.
if exist "%SystemRoot%\pyw.exe" (
    start "" "%SystemRoot%\pyw.exe" -3 "%~dp0main.py" %*
    goto :eof
)
where pyw >nul 2>&1 && (
    start "" pyw -3 "%~dp0main.py" %*
    goto :eof
)
where pythonw >nul 2>&1 && (
    start "" pythonw "%~dp0main.py" %*
    goto :eof
)
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python38\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python37\pythonw.exe"
    "C:\Python38\pythonw.exe"
    "C:\Python37\pythonw.exe"
    "C:\Program Files\Python38\pythonw.exe"
    "C:\Program Files\Python37\pythonw.exe"
) do (
    if exist %%P (
        start "" %%P "%~dp0main.py" %*
        goto :eof
    )
)
REM  Console fallbacks: these leave a window open behind the interface, but
REM  starting is better than not starting.
where py >nul 2>&1 && (
    start "" py -3 "%~dp0main.py" %*
    goto :eof
)
where python >nul 2>&1 && (
    start "" python "%~dp0main.py" %*
    goto :eof
)

echo Could not find Python on this machine.
echo.
echo Windows 7 supports Python up to 3.8. If Python is installed but not on
echo PATH, run it directly, for example:
echo     C:\Python37\pythonw.exe "%~dp0main.py"
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
