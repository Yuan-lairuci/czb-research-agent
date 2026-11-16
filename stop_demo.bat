@echo off
echo 正在停止前后端服务...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8000 "') do taskkill /F /PID %%p >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":4173 "') do taskkill /F /PID %%p >nul 2>&1
echo 已停止。
timeout /t 2 >nul
