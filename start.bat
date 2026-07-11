@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo [1/4] Installing Python dependencies...
python -m venv .venv 2>nul
call .venv\Scripts\activate
pip install -r requirements.txt -q

echo [2/4] Starting PostgreSQL via Docker...
docker ps --filter name=agenthub-pg --format {{.Names}} 2>nul | findstr agenthub-pg >nul
if not errorlevel 1 goto pgready
docker ps -a --filter name=agenthub-pg --format {{.Names}} 2>nul | findstr agenthub-pg >nul
if not errorlevel 1 (
    echo   Found stopped container, starting it...
    docker start agenthub-pg >nul
    goto pgready
)
docker run -d --name agenthub-pg -e POSTGRES_USER=agenthub -e POSTGRES_PASSWORD=agenthub -e POSTGRES_DB=agenthub -p 5435:5432 postgres:16 >nul
if errorlevel 1 (
    echo   Port 5435 is occupied. Removing old container and retrying...
    docker rm -f agenthub-pg >nul 2>&1
    timeout /t 2 /nobreak >nul
    docker run -d --name agenthub-pg -e POSTGRES_USER=agenthub -e POSTGRES_PASSWORD=agenthub -e POSTGRES_DB=agenthub -p 5435:5432 postgres:16 >nul
)
if errorlevel 1 (
    echo   Failed to start PostgreSQL. Is Docker running?
    pause
    exit /b 1
)
echo   Waiting for PostgreSQL...
:waitpg
docker exec agenthub-pg pg_isready -U agenthub >nul 2>&1
if errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto waitpg
)
echo   PostgreSQL ready on port 5435.
goto backend

:pgready
echo   PostgreSQL already running.

:backend
echo [3/4] Starting Python backend (port 8000)...
start "AgentHub Backend" cmd /k "call .venv\Scripts\activate && uvicorn main:app --reload --host 0.0.0.0 --port 8000"
timeout /t 3 /nobreak >nul

echo [4/4] Starting frontend (port 3000, legacy mode)...
echo   Cleaning port 3000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul
cd /d "%~dp0\frontend"
set API_BACKEND=legacy
call npm install --silent
npm run dev