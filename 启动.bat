@echo off
title WW-HAND - START  (this copy: port 8794 / instance fuse1)
setlocal
cd /d "%~dp0"
set "WW_PORT=8794"
set "WW_INSTANCE=fuse1"
if not exist "%~dp0_ops\worker_guard.py" (
  echo [ERROR] _ops\worker_guard.py not found
  pause & exit /b 1
)
echo Starting: WW_PORT=8794  WW_INSTANCE=fuse1
wscript.exe "%~dp0_ops\start-hand.vbs"
echo Waiting for the broker on 8794 ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0;$i -lt 30;$i++){ Start-Sleep -Milliseconds 700; try{ $h=Invoke-RestMethod 'http://127.0.0.1:8794/health' -TimeoutSec 2; $ok=$true; break }catch{} }; if($ok){ Write-Host ('OK   pid=' + $h.pid + '  build=' + $h.build + '  instance=' + $h.instance + '  admin=' + $h.admin) } else { Write-Host 'FAIL no answer on 8794'; exit 1 }"
if errorlevel 1 (
  echo.
  echo ---- tail of _log\guard.log ----
  if exist "%~dp0_log\guard.log" powershell -NoProfile -Command "Get-Content '%~dp0_log\guard.log' -Tail 15"
)
echo.
echo A production instance on 8792, if any, was NOT touched.
pause