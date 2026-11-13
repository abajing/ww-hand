@echo off
title WW-HAND - LICENSE STATUS
setlocal
cd /d "%~dp0"
set "PY=python.exe"
if exist "%~dp0_ops\python.txt" set /p PY=<"%~dp0_ops\python.txt"
"%PY%" "%~dp0_ops\ww_license.py"
echo.
echo Send the machine ID above to the author to get your license code,
echo then replace the code line in _ops\license.key and restart the start .bat
echo.
pause