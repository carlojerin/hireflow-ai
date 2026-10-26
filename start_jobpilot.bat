@echo off

title JobPilot AI

echo ==========================================
echo        JOBPILOT AI - STARTING
echo ==========================================
echo.

cd /d D:\jobpilot-ai


echo Starting Backend...
start "JobPilot AI - Backend" cmd /k "venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload"


timeout /t 3 /nobreak >nul


echo Starting Frontend...
start "JobPilot AI - Frontend" cmd /k "cd /d D:\jobpilot-ai\frontend && ..\venv\Scripts\python.exe -m http.server 5600"


timeout /t 3 /nobreak >nul


echo.
echo ==========================================
echo       JOBPILOT AI IS RUNNING
echo ==========================================
echo.
echo Backend:
echo http://127.0.0.1:8000
echo.
echo Swagger:
echo http://127.0.0.1:8000/docs
echo.
echo Frontend:
echo http://127.0.0.1:5600
echo.
echo ==========================================


start http://127.0.0.1:5600/index.html

pause