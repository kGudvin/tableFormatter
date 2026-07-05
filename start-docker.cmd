@echo off
setlocal
cd /d "%~dp0"

if not exist "tmp" mkdir "tmp"
set LOG=tmp\docker-start.log
echo === %DATE% %TIME% === > "%LOG%"

if not exist ".env" (
  copy ".env.example" ".env" >nul
)

if not exist "secrets\google-service-account.json" (
  echo Missing secrets\google-service-account.json
  echo Missing secrets\google-service-account.json >> "%LOG%"
  pause
  exit /b 1
)

echo Checking Docker... >> "%LOG%"
docker version >> "%LOG%" 2>&1
if errorlevel 1 (
  echo Docker is not available. See %LOG%
  type "%LOG%"
  pause
  exit /b 1
)

echo Validating compose... >> "%LOG%"
docker compose config --quiet >> "%LOG%" 2>&1
if errorlevel 1 (
  echo Compose config failed. See %LOG%
  type "%LOG%"
  pause
  exit /b 1
)

echo Building image... >> "%LOG%"
docker compose build --progress plain >> "%LOG%" 2>&1
if errorlevel 1 (
  echo Docker build failed. See %LOG%
  type "%LOG%"
  pause
  exit /b 1
)

echo Starting containers... >> "%LOG%"
docker compose up -d >> "%LOG%" 2>&1
if errorlevel 1 (
  echo Docker startup failed. See %LOG%
  type "%LOG%"
  pause
  exit /b 1
)

echo Container status: >> "%LOG%"
docker compose ps -a >> "%LOG%" 2>&1

echo App logs: >> "%LOG%"
docker compose logs --tail=80 app >> "%LOG%" 2>&1

echo Postgres logs: >> "%LOG%"
docker compose logs --tail=40 postgres >> "%LOG%" 2>&1

type "%LOG%"
echo.
echo Open http://127.0.0.1:8080/ui/
pause
