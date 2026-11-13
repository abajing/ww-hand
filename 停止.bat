@echo off
title WW-HAND - STOP  (this copy: port 8794 / instance fuse1)
setlocal
cd /d "%~dp0"
set "WW_PORT=8794"
set "WW_INSTANCE=fuse1"
set "PY=python.exe"
if exist "%~dp0_ops\python.txt" set /p PY=<"%~dp0_ops\python.txt"
echo Stopping THIS copy only. Processes are matched by full path under:
echo   %~dp0_ops
"%PY%" "%~dp0_ops\reset.py" --kill
echo.
powershell -NoProfile -Command "try{ Invoke-RestMethod 'http://127.0.0.1:8794/health' -TimeoutSec 2 | Out-Null; Write-Host 'still responding on 8794' }catch{ Write-Host 'stopped - 8794 no longer answering' }"
echo A production instance on 8792, if any, was NOT touched.
pause