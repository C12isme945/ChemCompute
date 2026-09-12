@echo off
chcp 65001 >nul
title ChemCompute - 普通计算节点 (Worker Node)
echo ================================================================================
echo   💻 ChemCompute - 普通计算节点 (Worker Node) 启动向导
echo ================================================================================
echo 本程序将本机接入 ChemCompute 分布式计算集群，贡献 CPU/GPU 算力。
echo.

cd /d "%~dp0"

set /p CTRL_URL="请输入主控端地址 [默认: http://127.0.0.1:8000]: "
if "%CTRL_URL%"=="" set CTRL_URL=http://127.0.0.1:8000

set /p INVITE_CODE="请输入管理员密钥 / 邀请码 (如 CC-XXXXXX): "

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "scripts\start_agent.py" --controller "%CTRL_URL%" --invite "%INVITE_CODE%"
) else (
    where uv >nul 2>nul
    if %errorlevel% equ 0 (
        uv run python "scripts\start_agent.py" --controller "%CTRL_URL%" --invite "%INVITE_CODE%"
    ) else (
        python "scripts\start_agent.py" --controller "%CTRL_URL%" --invite "%INVITE_CODE%"
    )
)

pause
