<#
.SYNOPSIS
    ChemCompute Windows 一键安装与节点入网 Bootstrapper
.DESCRIPTION
    3步将当前 Windows 电脑部署为 ChemCompute 私有化学计算节点或主控端。
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Clear-Host

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "          ChemCompute 分布式化学计算系统一键部署               " -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Role selection
Write-Host "【第 1 步】请选择本机角色：" -ForegroundColor Yellow
Write-Host "  [1] 计算节点 (Worker Node) - 默认" -ForegroundColor Green
Write-Host "  [2] 主控中心 (Controller Server & Web Console)" -ForegroundColor White
Write-Host "  [3] 混合模式 (Controller + Worker)" -ForegroundColor White
$roleChoice = Read-Host "请输入序号 [1/2/3] (直接回车默认 1)"
if (-not $roleChoice) { $roleChoice = "1" }

$isWorker = ($roleChoice -eq "1" -or $roleChoice -eq "3")
$isController = ($roleChoice -eq "2" -or $roleChoice -eq "3")

# Step 2: Cluster credentials
$controllerUrl = "http://127.0.0.1:8000"
$inviteCode = ""
$nodeName = $env:COMPUTERNAME

if ($isWorker) {
    Write-Host ""
    Write-Host "【第 2 步】加入计算集群配置" -ForegroundColor Yellow
    $inputUrl = Read-Host "主控端地址 [默认: http://127.0.0.1:8000]"
    if ($inputUrl) { $controllerUrl = $inputUrl }

    if (-not $isController) {
        $inviteCode = Read-Host "集群邀请码 (Invite Code, 如 CC-7F4A9K)"
    } else {
        $inviteCode = "LOCAL_INIT"
    }

    $inputName = Read-Host "节点名称 [默认: $nodeName]"
    if ($inputName) { $nodeName = $inputName }
}

# Step 3: Hardware & Environment Detection
Write-Host ""
Write-Host "【第 3 步】正在执行本机环境深度检测..." -ForegroundColor Yellow

$osInfo = Get-CimInstance Win32_OperatingSystem
Write-Host "  ✓ 操作系统: $($osInfo.Caption) ($($osInfo.OSArchitecture))" -ForegroundColor Green

$cpuInfo = Get-CimInstance Win32_Processor | Select-Object -First 1
Write-Host "  ✓ 处理器: $($cpuInfo.Name) ($($cpuInfo.NumberOfCores) 核 / $($cpuInfo.NumberOfLogicalProcessors) 线程)" -ForegroundColor Green

$ramGB = [math]::Round($osInfo.TotalVisibleMemorySize / 1024 / 1024, 1)
Write-Host "  ✓ 物理内存: $ramGB GB" -ForegroundColor Green

# GPU & CUDA Check
$gpuFound = $false
try {
    $gpuOutput = nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null
    if ($gpuOutput) {
        Write-Host "  ✓ 检测到独立显卡: $gpuOutput" -ForegroundColor Green
        Write-Host "  ✓ NVIDIA 驱动与 CUDA 计算加速就绪" -ForegroundColor Green
        $gpuFound = $true
    }
} catch {}

if (-not $gpuFound) {
    Write-Host "  ℹ 未检测到 NVIDIA GPU，将使用高效 CPU 线程池模式" -ForegroundColor Gray
}

# GROMACS Detection
$gmxFound = $false
$gmxPath = (Get-Command gmx -ErrorAction SilentlyContinue).Source
if ($gmxPath) {
    Write-Host "  ✓ 发现 GROMACS (Windows/PATH): $gmxPath" -ForegroundColor Green
    $gmxFound = $true
}

$wslCmd = Get-Command wsl -ErrorAction SilentlyContinue
if ($wslCmd) {
    $wslGmx = wsl gmx --version 2>$null
    if ($wslGmx -and ($wslGmx -match "GROMACS")) {
        Write-Host "  ✓ 发现 GROMACS (WSL2 Linux 加速子系统)" -ForegroundColor Green
        $gmxFound = $true
    }
}

if (-not $gmxFound) {
    Write-Host "  ℹ 当前未直接发现 GROMACS，可在稍后通过 conda 或 WSL 安装" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  环境自检完成！正在启动 ChemCompute 服务..." -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Cyan

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Split-Path -Parent $scriptRoot

if ($isController) {
    Write-Host "[Controller] 启动主控 Web 控制台..." -ForegroundColor Cyan
    Start-Process -FilePath "python" -ArgumentList "$projectRoot\scripts\start_controller.py" -WorkingDirectory $projectRoot
    Start-Sleep -Seconds 2
    Start-Process "http://127.0.0.1:8000"
}

if ($isWorker) {
    Write-Host "[Worker] 注册计算节点守护进程..." -ForegroundColor Cyan
    if ($inviteCode -and $inviteCode -ne "LOCAL_INIT") {
        python "$projectRoot\scripts\start_agent.py" --controller "$controllerUrl" --invite "$inviteCode" --name "$nodeName"
    } else {
        Write-Host "节点就绪，可通过 scripts\start_agent.py 接入集群。" -ForegroundColor Green
    }
}
