@echo off
cd /d "%~dp0"
title Shorts Maker

echo ==================================================
echo   Starting Shorts Maker...
echo   Your browser will open by itself in a moment.
echo   Keep THIS black window open while you work.
echo   (Closing it stops the app.)
echo ==================================================
echo.

REM --- Clear out any leftover copy still holding the port. ---
REM --- This is what prevents the "127.0.0.1 refused to connect" error. ---
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 5050 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

REM --- Open the browser ONLY after the app is actually ready. ---
REM --- A hidden helper checks every half-second for up to ~20s, then opens the page. ---
start "" powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 40;$i++){try{$null=Invoke-WebRequest -Uri 'http://127.0.0.1:5050' -UseBasicParsing -TimeoutSec 1; Start-Process 'http://127.0.0.1:5050'; break}catch{Start-Sleep -Milliseconds 500}}"

echo If your browser does not open on its own, type this into it:
echo     http://127.0.0.1:5050
echo.

REM --- Start the app engine. This line holds the window open while you use the app. ---
venv\Scripts\python.exe app.py

echo.
echo The app has stopped. You can close this window now.
pause
