@echo off
setlocal
cd /d "%~dp0"
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
start "AgentHub Backend" cmd /k "call .venv\Scripts\activate && uvicorn main:app --reload --host 0.0.0.0 --port 8000"
cd frontend
npm install
npm run dev
