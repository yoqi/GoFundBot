@echo off
setlocal

echo =========================================
echo   GoFundBot local start
echo =========================================

where python >nul 2>nul
if %errorlevel% neq 0 (
  echo [ERROR] Python was not found. Please install Python 3.8+ and add it to PATH.
  pause
  exit /b 1
)

where npm.cmd >nul 2>nul
if %errorlevel% neq 0 (
  echo [ERROR] npm was not found. Please install Node.js 18+.
  pause
  exit /b 1
)

start "GoFundBot Backend" cmd /k "python Backend\app.py"
start "GoFundBot DataService" cmd /k "cd DataService && npm.cmd run dev"
start "GoFundBot Frontend" cmd /k "cd Frontend && npm.cmd run dev"

echo [OK] Services have been started.
echo     backend: http://127.0.0.1:5000
echo     data service: http://127.0.0.1:3100
echo     frontend: http://127.0.0.1:5173
pause
