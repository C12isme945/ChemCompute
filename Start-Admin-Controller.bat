@echo off
chcp 65001 >nul
title ChemCompute - 管理员主控中心 (Controller Server)
echo ================================================================================
echo   👑 ChemCompute - 管理员主控中心 (Controller Server) 启动向导
echo ================================================================================
echo 正在启动主控调度中心与 Web 控制台...
echo.

cd /d "%~dp0"

REM 优先使用已激活的虚拟环境或 uv
if exist ".venv\Scripts\python.exe" (
    start "" http://127.0.0.1:8000
    ".venv\Scripts\python.exe" "scripts\start_controller.py"
) else (
    where uv >nul 2>nul
    if %errorlevel% equ 0 (
        start "" http://127.0.0.1:8000
        uv run python "scripts\start_controller.py"
    ) else (
        start "" http://127.0.0.1:8000
        python "scripts\start_controller.py"
    )
)

pause
