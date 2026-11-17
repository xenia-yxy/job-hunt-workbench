@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   求职工作台 · 启动中
echo ============================================
where node >nul 2>nul
if errorlevel 1 (
  echo [x] 没找到 Node.js。装一个 18 以上的版本再来：https://nodejs.org
  pause
  exit /b 1
)

if not exist "config\local\app.json" (
  echo [!] 还没初始化，先跑一次配置
  python tools\setup.py
  if errorlevel 1 (
    echo [x] 初始化失败：需要 Python 3.9+ 与 Node.js 18+
    pause
    exit /b 1
  )
)

if not exist "app.html" (
  echo [!] 主应用页面还没生成，补一次构建
  node tools\build.js
)

start "" http://localhost:8787/workbench
node server.js
pause
