@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m uvicorn app.api.main:app --host 0.0.0.0 --port 8080 > "tmp\uvicorn-task.log" 2>&1
