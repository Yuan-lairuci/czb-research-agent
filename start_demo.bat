@echo off
chcp 65001 >nul
set "NODE=C:\Users\yuanl\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
if not exist "%NODE%" set "NODE=node"
echo [1/2] 启动后端 :8000 ...
start "CZB-后端" cmd /k "cd /d D:\deep\czb_research_agent\backend && ..\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
echo [2/2] 启动前端 :4173 ...
start "CZB-前端" cmd /k "cd /d D:\deep\czb_research_agent\frontend && "%NODE%" node_modules\vite\bin\vite.js preview --port 4173 --host 127.0.0.1"
timeout /t 3 >nul
echo.
echo 完成！浏览器打开 http://127.0.0.1:4173
echo （若端口被占用启动失败，请先双击 stop_demo.bat 再重试）
timeout /t 3 >nul
