@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

title AgentHub Launcher

echo.
echo   ============================================================
echo     AgentHub - Multi-Agent Platform  Starting...
echo   ============================================================
echo.

:: =======================================================================
:: [1/4] Python venv & dependencies
:: =======================================================================
echo   [+] [1/4] Checking Python environment...

where python >nul 2>&1
if errorlevel 1 (
    echo   [X] Python not found. Please install Python 3.10+
    echo       Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo   [i] Creating virtual environment .venv ...
    python -m venv .venv 2>nul
    if errorlevel 1 (
        echo   [X] Failed to create venv
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat

pip show uvicorn >nul 2>&1
if errorlevel 1 (
    echo   [i] Installing Python dependencies...
    pip install -r requirements.txt -q
    if errorlevel 1 (
        echo   [X] Failed to install dependencies
        pause
        exit /b 1
    )
)
echo   [v] Python environment ready
echo.

:: =======================================================================
:: [2/4] PostgreSQL - Connect & Test
:: =======================================================================
echo   [+] [2/4] Starting PostgreSQL...

:: Read DATABASE_URL from .env (tokens=1* to handle = in query params)
set "DB_URL="
if exist ".env" (
    for /f "tokens=1* delims==" %%a in ('findstr /b "DATABASE_URL=" .env 2^>nul') do (
        set "DB_URL=%%b"
    )
)

if not defined DB_URL (
    echo   [X] DATABASE_URL not set in .env
    echo.
    echo   Please create .env with:
    echo     DATABASE_URL=postgresql://user:pass@host:port/db
    echo.
    echo   Or install Docker Desktop and the script will auto-start PG:
    echo     https://www.docker.com/products/docker-desktop/
    echo.
    pause
    exit /b 1
)

echo   [i] DATABASE_URL: !DB_URL!

:: ---- Step 1: Try direct connection first (fastest) ------------------
echo   [i] Testing database connection...

pip show asyncpg >nul 2>&1
if errorlevel 1 (
    pip install asyncpg -q 2>nul
)

python -c "import asyncio,asyncpg; url='!DB_URL!'; asyncio.run(asyncpg.connect(dsn=url,timeout=5)); print('OK')" >nul 2>&1
if not errorlevel 1 (
    echo   [v] Database connection successful!
    goto backend
)

:: ---- Step 2: Try Docker (start existing or create new container) ----
echo   [i] Direct connection failed, trying Docker...

where docker >nul 2>&1
if errorlevel 1 (
    echo   [!] Docker not found in PATH
    goto docker_fail
)

docker info >nul 2>&1
if errorlevel 1 (
    echo   [!] Docker is not running. Please start Docker Desktop.
    goto docker_fail
)

echo   [i] Docker is available

:: Check if agenthub-pg container exists and is running
docker ps --filter "name=agenthub-pg" --format "{{.Names}}" 2>nul | findstr "agenthub-pg" >nul
if not errorlevel 1 (
    echo   [i] Container agenthub-pg is running
    goto wait_pg
)

:: Check for stopped container
docker ps -a --filter "name=agenthub-pg" --format "{{.Names}}" 2>nul | findstr "agenthub-pg" >nul
if not errorlevel 1 (
    echo   [i] Starting existing container agenthub-pg...
    docker start agenthub-pg >nul 2>&1
    if not errorlevel 1 goto wait_pg
)

:: Create new container
echo   [i] Creating PostgreSQL container (postgres:16)...
docker run -d --name agenthub-pg ^
    -e POSTGRES_USER=agenthub ^
    -e POSTGRES_PASSWORD=agenthub ^
    -e POSTGRES_DB=agenthub ^
    -p 5435:5432 ^
    postgres:16 >nul 2>&1

if errorlevel 1 (
    docker rm -f agenthub-pg >nul 2>&1
    timeout /t 2 /nobreak >nul
    docker run -d --name agenthub-pg ^
        -e POSTGRES_USER=agenthub ^
        -e POSTGRES_PASSWORD=agenthub ^
        -e POSTGRES_DB=agenthub ^
        -p 5435:5432 ^
        postgres:16 >nul 2>&1
    if errorlevel 1 (
        echo   [X] Failed to create Docker container
        goto docker_fail
    )
)

:: Wait for PG to be ready
:wait_pg
echo   [i] Waiting for PostgreSQL to be ready...
set "WAIT_COUNT=0"
:wait_pg_loop
docker exec agenthub-pg pg_isready -U agenthub >nul 2>&1
if not errorlevel 1 (
    echo   [v] PostgreSQL ready (port 5435)
    goto backend
)
set /a WAIT_COUNT+=1
if !WAIT_COUNT! gtr 30 (
    echo   [X] PostgreSQL container startup timed out
    docker logs --tail 20 agenthub-pg 2>nul
    pause
    exit /b 1
)
timeout /t 2 /nobreak >nul
goto wait_pg_loop

:: ---- Docker failed --------------------------------------------------
:docker_fail
echo.
echo   [X] ============================================================
echo   [X]   Cannot connect to PostgreSQL!
echo   [X] ============================================================
echo.
echo   [i] DATABASE_URL = !DB_URL!
echo.
echo   [i] Options:
echo.
echo   [i]   [1] Install Docker Desktop (Recommended)
echo   [i]       https://www.docker.com/products/docker-desktop/
echo   [i]       The script auto-starts a postgres:16 container
echo.
echo   [i]   [2] Install local PostgreSQL
echo   [i]       https://www.postgresql.org/download/windows/
echo   [i]       Port: 5435  User: agenthub  Pass: agenthub  DB: agenthub
echo.
echo   [i]   [3] Use Neon cloud (free tier)
echo   [i]       https://neon.tech/
echo   [i]       Update DATABASE_URL in .env with the Neon connection string
echo.
echo   [X] ============================================================
echo.
pause
exit /b 1

:: =======================================================================
:: [3/4] Start backend
:: =======================================================================
:backend
echo.
echo   [+] [3/4] Starting Python backend (port 8000)...

:: Free port 8000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTENING 2^>nul') do (
    echo   [!] Port 8000 occupied (PID: %%a^), releasing...
    taskkill /F /PID %%a >nul 2>&1
    timeout /t 1 /nobreak >nul
)

start "AgentHub Backend" cmd /k "cd /d %~dp0 && call .venv\Scripts\activate.bat && uvicorn main:app --reload --host 0.0.0.0 --port 8000"
echo   [v] Backend started in new window

timeout /t 3 /nobreak >nul

:: =======================================================================
:: [4/4] Start frontend
:: =======================================================================
echo   [+] [4/4] Starting frontend (port 3000)...

:: Free port 3000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr LISTENING 2^>nul') do (
    echo   [!] Port 3000 occupied (PID: %%a^), releasing...
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

cd /d "%~dp0\frontend"
set API_BACKEND=legacy
call npm install --silent 2>nul

echo.
echo   [v] ============================================================
echo   [v]   AgentHub started successfully!
echo   [v]   Backend:  http://localhost:8000
echo   [v]   Frontend: http://localhost:3000
echo   [v] ============================================================
echo.

npm run dev
