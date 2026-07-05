@echo off
setlocal
cd /d "%~dp0"
if not exist "tmp" mkdir "tmp"
set LOG=tmp\docker-diagnostics.log
echo === %DATE% %TIME% === > "%LOG%"
docker compose ps -a >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo === APP LOGS === >> "%LOG%"
docker compose logs --tail=200 app >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo === POSTGRES LOGS === >> "%LOG%"
docker compose logs --tail=80 postgres >> "%LOG%" 2>&1
type "%LOG%"
pause
