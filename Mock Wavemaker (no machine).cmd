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

if exist "%SystemRoot%\pyw.exe" (
    start "" "%SystemRoot%\pyw.exe" -3 "%~dp0main.py" --mock %*
    goto :eof
)
where pyw >nul 2>&1 && (
    start "" pyw -3 "%~dp0main.py" --mock %*
    goto :eof
)
where pythonw >nul 2>&1 && (
    start "" pythonw "%~dp0main.py" --mock %*
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
        start "" %%P "%~dp0main.py" --mock %*
        goto :eof
    )
)
where py >nul 2>&1 && (
    start "" py -3 "%~dp0main.py" --mock %*
    goto :eof
)
where python >nul 2>&1 && (
    start "" python "%~dp0main.py" --mock %*
    goto :eof
)

echo Could not find Python on this machine.
echo.
echo Install Python 3 from python.org. Windows 7 supports up to 3.8;
echo anything from 3.7 onwards works.
pause
goto :eof
