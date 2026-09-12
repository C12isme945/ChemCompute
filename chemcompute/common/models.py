"""ChemCompute Pydantic 数据模型定义"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GpuDevice(BaseModel):
    """单个 GPU 设备信息"""
    index: int = 0
    name: str = "Unknown GPU"
    total_memory_mb: int = 0
    used_memory_mb: int = 0
    free_memory_mb: int = 0
    temperature_c: int | None = None
    utilization_gpu_percent: int | None = None
    utilization_mem_percent: int | None = None
    driver_version: str | None = None
    cuda_version: str | None = None


class HardwareInfo(BaseModel):
    """节点硬件详细资产"""
    cpu_count_logical: int = 1
    cpu_count_physical: int = 1
    cpu_percent: float = 0.0
    cpu_frequency_mhz: float | None = None
    ram_total_mb: int = 0
    ram_available_mb: int = 0
    ram_percent: float = 0.0
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0
    disk_percent: float = 0.0
    gpus: list[GpuDevice] = Field(default_factory=list)


class GromacsSoftwareInfo(BaseModel):
    """GROMACS 软件探测信息"""
    found: bool = False
    executable_path: str | None = None
    version: str | None = None
    precision: str | None = None  # single, double
    mpi_enabled: bool = False
    cuda_enabled: bool = False
    simd: str | None = None
    probe_output: str | None = None


class SoftwareInfo(BaseModel):
    """节点软件资产总览"""
    hostname: str = ""
    os_name: str = ""
    os_release: str = ""
    os_version: str = ""
    architecture: str = ""
    python_version: str = ""
    gaussian: dict = Field(default_factory=dict)
    gromacs: GromacsSoftwareInfo = Field(default_factory=GromacsSoftwareInfo)


# ------------------- 注册模型 -------------------

class NodeRegisterRequest(BaseModel):
    """节点向控制端请求注册报文"""
    invite_code: str
    node_name: str
    hostname: str
    os: str
    hardware: HardwareInfo
    software: SoftwareInfo


class NodeRegisterResponse(BaseModel):
    """节点注册成功响应 (包含仅返回一次的节点令牌)"""
    node_id: str
    node_token: str
    controller_version: str
    heartbeat_interval_seconds: int = 15


# ------------------- 心跳模型 -------------------

class JobSpec(BaseModel):
    """分发给节点的作业任务规范"""
    job_id: str
    subcommand: str
    arguments: list[str] = Field(default_factory=list)
    timeout_seconds: int = 300
    description: str | None = None


class NodeHeartbeatRequest(BaseModel):
    """节点定期心跳报文"""
    node_id: str
    status: str = "online"  # online, busy
    hardware: HardwareInfo
    software: SoftwareInfo
    active_job_id: str | None = None


class NodeHeartbeatResponse(BaseModel):
    """心跳响应报文"""
    status: str = "ok"
    server_time: str
    assigned_jobs: list[JobSpec] = Field(default_factory=list)


# ------------------- 邀请码模型 -------------------

class InviteCreateRequest(BaseModel):
    """创建邀请码请求"""
    expires_in_hours: int = 24
    note: str | None = None


class InviteCreateResponse(BaseModel):
    """创建邀请码响应 (明文代码仅在此次输出)"""
    invite_code: str
    expires_at: str
    note: str | None = None


class InviteItem(BaseModel):
    """邀请码展示项 (不含完整明文或哈希)"""
    id: int
    code_prefix: str
    created_at: str
    expires_at: str
    is_used: bool
    used_at: str | None = None
    used_by_node_id: str | None = None
    note: str | None = None
    is_expired: bool = False


# ------------------- 作业模型 -------------------

class JobCreateRequest(BaseModel):
    """管理员创建 GROMACS 作业请求"""
    node_id: str
    subcommand: str
    arguments: list[str] = Field(default_factory=list)
    timeout_seconds: int = 300
    description: str | None = None


class JobStatusUpdateRequest(BaseModel):
    """节点回传作业执行状态报文"""
    job_id: str
    status: str  # running, completed, failed, timeout
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    error_message: str | None = None


class JobItem(BaseModel):
    """作业详细信息项"""
    job_id: str
    node_id: str
    node_name: str | None = None
    subcommand: str
    arguments: list[str] = Field(default_factory=list)
    status: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    error_message: str | None = None
    description: str | None = None


# ------------------- 节点列表模型 -------------------

class NodeItem(BaseModel):
    """节点信息展示项"""
    node_id: str
    name: str
    status: str  # online, offline, busy
    registered_at: str
    last_seen: str
    ip_address: str | None = None
    hostname: str
    os: str
    hardware_info: HardwareInfo
    software_info: SoftwareInfo


class AuditLogItem(BaseModel):
    """审计日志项"""
    id: int
    timestamp: str
    actor: str
    action: str
    details: str | None = None
