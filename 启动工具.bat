@echo off
title 文档核验中心（离线保密版）— 全息渐变版

REM =========================================================================
REM  文档核验中心 —— 本地一键启动脚本（FastAPI 后端 + React 全息渐变前端）
REM  作用：以离线模式启动本机服务并自动打开浏览器
REM  注意：本脚本不执行任何联网操作（不含 pip install / 不含更新检查）
REM  前端：web/（React 源码）→ vite build → dist → 自动同步到 static/
REM =========================================================================

cd /d "%~dp0"

echo ============================================================
echo   文档核验中心  全息渐变版（React UI）  离线保密
echo   运行模式：纯本地离线 ^| 零联网 ^| 零数据外发
echo ============================================================
echo.

REM ---- 前端产物同步：web\dist 更新时自动复制到 static\（无需手动拷贝）----
if exist "web\dist\index.html" (
    powershell -NoProfile -Command "$d=Get-Item 'web\dist\index.html'; $s=Get-Item 'static\index.html' -ErrorAction SilentlyContinue; if(-not $s -or $d.LastWriteTime -gt $s.LastWriteTime){Copy-Item 'web\dist\*' 'static\' -Recurse -Force; Write-Host '[前端] 检测到新构建产物，已同步到 static\'}" 2>nul
)

REM ---- 服务已在运行则直接打开浏览器退出（幂等，可反复双击）----
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul
if %errorlevel%==0 (
    echo 服务已在运行，正在打开浏览器…
    start "" "http://127.0.0.1:8501"
    exit /b 0
)

set PYTHONIOENCODING=utf-8

REM ---- 按优先级选择 Python 运行环境 ----
REM 1) 便携版（打包分发场景）
if exist "python\python.exe" (
    set "PY=python\python.exe"
    goto :RUN
)
REM 2) 项目虚拟环境
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
    goto :RUN
)
REM 3) WorkBuddy 托管 venv（本机实际运行环境）
if exist "C:\Users\86135\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
    set "PY=C:\Users\86135\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
    goto :RUN
)
REM 4) 系统 PATH 中的 Python
where python >nul 2>nul
if %errorlevel%==0 (
    set "PY=python"
    goto :RUN
)

echo [错误] 未找到 Python 运行环境。
echo        请先安装 Python 3.9+ ，或将便携版 Python 放到本目录的 python\ 文件夹下。
echo.
pause
exit /b 1

:RUN
echo 正在启动本地服务，请稍候…
echo 启动后浏览器将自动打开：http://127.0.0.1:8501
echo.
echo 【关闭方式】直接关闭本窗口即可停止服务。
echo ============================================================
echo.

REM 延迟 3 秒打开浏览器，等待服务就绪（独立进程，不阻塞）
start "" powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8501'"

"%PY%" app.py

echo.
echo 服务已停止。
pause
