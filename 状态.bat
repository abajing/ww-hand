@echo off
title WW-HAND - STATUS
setlocal
cd /d "%~dp0"
set "WW_PORT=8794"
set "WW_INSTANCE=fuse1"
set "PY=python.exe"
if exist "%~dp0_ops\python.txt" set /p PY=<"%~dp0_ops\python.txt"
echo ==== this copy (8794 / instance fuse1) ====
"%PY%" "%~dp0ww_op.py" status
echo.
echo ==== anything on 8792? read-only check ====
powershell -NoProfile -Command "try{ $h=Invoke-RestMethod 'http://127.0.0.1:8792/health' -TimeoutSec 2; Write-Host ('online  pid=' + $h.pid + '  build=' + $h.build) }catch{ Write-Host 'not running' }"
pause