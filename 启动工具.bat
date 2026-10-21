@echo off
title 文档低级错误检查工具（离线保密版）· 全息渐变

REM =========================================================================
REM  文档核验中心 —— 一键启动脚本（FastAPI 后端 + React 全息渐变前端）
REM  特性：ANSI 编码中文控制台 | 缺少依赖自动安装 | 端口占用就绪检测
REM       服务就绪后自动打开浏览器 | Python 多环境自动探测
REM =========================================================================

cd /d "%~dp0"

echo ============================================================
echo   文档低级错误检查工具  全息渐变 UI（React）  离线保密版
echo   离线模式：规则检测 ^| 本地AI ^| 报告导出
echo ============================================================
echo.

REM ---- 前端产物同步：web\dist 比 static\ 新时自动覆盖（开发后可手动关闭）----
if exist "web\dist\index.html" (
    powershell -NoProfile -Command "$d=Get-Item 'web\dist\index.html'; $s=Get-Item 'static\index.html' -ErrorAction SilentlyContinue; if(-not $s -or $d.LastWriteTime -gt $s.LastWriteTime){Copy-Item 'web\dist\*' 'static\' -Recurse -Force; Write-Host '[前端] 检测到新构建产物，已同步到 static\'}" 2>nul
)

if not exist "static\index.html" (
    echo [错误] 未找到前端产物 static\index.html。
    if exist "web\package.json" (
        echo        请先执行：cd web ^&^& npm install ^&^& npm run build
        echo        再将 web\dist\ 下的文件复制到 static\ 目录。
    )
    echo.
    pause
    exit /b 1
)

REM ---- 端口占用检测：服务已在运行则直接打开浏览器 ----
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul
if errorlevel 1 goto :FIND_PY
echo [提示] 检测到服务已在运行，直接打开浏览器。
start "" "http://127.0.0.1:8501"
exit /b 0

:FIND_PY
REM ---- Python 环境自动探测（按优先级） ----
if exist "%~dp0python\python.exe" (
    set "PY=%~dp0python\python.exe"
    set "PYARGS="
    goto :DEPS_CHECK
)
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
    set "PYARGS="
    goto :DEPS_CHECK
)
if exist "C:\Users\86135\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
    set "PY=C:\Users\86135\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
    set "PYARGS="
    goto :DEPS_CHECK
)
where python >nul 2>nul
if not errorlevel 1 (
    set "PY=python"
    set "PYARGS="
    goto :DEPS_CHECK
)
where py >nul 2>nul
if not errorlevel 1 (
    set "PY=py"
    set "PYARGS=-3"
    goto :DEPS_CHECK
)

echo [错误] 未找到可用的 Python 解释器。
echo        请安装 Python 3.9+，或将便携版 Python 放到本目录的 python\ 文件夹下。
echo.
pause
exit /b 1

:DEPS_CHECK
REM ---- 运行依赖检查：缺失时询问自动安装（仅首次运行需要） ----
echo [检查] 校验运行依赖 ...
"%PY%" %PYARGS% -c "import fastapi,uvicorn,docx,openpyxl,pypdf" >nul 2>nul
if not errorlevel 1 goto :RUN

echo.
echo [提示] 缺少运行依赖，首次运行需要安装（pip install -r requirements.txt）。
choice /c YN /m "现在自动安装依赖？Y=立即安装 / N=退出"
if errorlevel 2 goto :NO_DEPS

echo [安装] 正在安装依赖，请耐心等待（离线机请先准备好 wheels 目录）...
"%PY%" %PYARGS% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败。请检查网络，或在内网使用离线 wheel：
    echo        pip download -r requirements.txt -d wheels --only-binary=:all:
    echo        pip install --no-index --find-links=wheels -r requirements.txt
    echo.
    pause
    exit /b 1
)
echo [安装] 依赖安装完成。
echo.

:RUN
REM ---- 浏览器就绪轮询：服务可访问后再自动打开（冷启动也稳妥） ----
echo [启动] 正在启动本地服务： http://127.0.0.1:8501
echo        关闭本窗口即停止服务 ^（也可直接按 Ctrl+C 中断）。
echo.

start "" powershell -NoProfile -Command "$u='http://127.0.0.1:8501/'; for($n=0;$n -lt 90;$n++){ try{ $r=Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -lt 500){ Start-Process $u; exit } }catch{}; Start-Sleep -Milliseconds 1000 }"

"%PY%" %PYARGS% app.py

echo.
echo [提示] 服务已停止。
pause
exit /b 0

:NO_DEPS
echo 已取消安装。可稍后手动执行： %PY% %PYARGS% -m pip install -r requirements.txt
echo.
pause
exit /b 1