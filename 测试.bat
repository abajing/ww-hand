@echo off
title WW-HAND - ASSERTIONS (31 items)
setlocal
cd /d "%~dp0"
set "WW_PORT=8794"
set "WW_INSTANCE=fuse1"
set "PY=python.exe"
if exist "%~dp0_ops\python.txt" set /p PY=<"%~dp0_ops\python.txt"
"%PY%" "%~dp0_test\assert_lab.py"
pause