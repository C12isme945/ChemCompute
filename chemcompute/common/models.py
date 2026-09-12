"""
Pydantic data models for ChemCompute.
"""

from enum import Enum
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
import time


class NodeRole(str, Enum):
    WORKER = "worker"
    CONTROLLER = "controller"
    STANDALONE = "standalone"


class NodeState(str, Enum):
    OFFLINE = "offline"
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"


class HardwareGPU(BaseModel):
    name: str = "N/A"
    driver_version: str = "N/A"
    memory_total_mb: float = 0.0
    memory_free_mb: float = 0.0
    memory_used_mb: float = 0.0
    utilization_gpu_pct: float = 0.0
    temperature_c: float = 0.0
    cuda_version: Optional[str] = None


class HardwareCPU(BaseModel):
    model: str = "Unknown CPU"
    physical_cores: int = 1
    logical_cores: int = 1
    utilization_pct: float = 0.0
    frequency_mhz: float = 0.0


class HardwareRAM(BaseModel):
    total_mb: float = 0.0
    available_mb: float = 0.0
    used_pct: float = 0.0


class NodeTelemetry(BaseModel):
    cpu: HardwareCPU = Field(default_factory=HardwareCPU)
    ram: HardwareRAM = Field(default_factory=HardwareRAM)
    gpu: Optional[HardwareGPU] = None
    timestamp: float = Field(default_factory=time.time)


class SoftwareGromacs(BaseModel):
    available: bool = False
    version: str = "Unknown"
    is_wsl: bool = False
    path: str = ""
    gpu_acceleration: bool = False


class SoftwareOrca(BaseModel):
    available: bool = False
    version: str = "Unknown"
    path: str = ""


class SoftwareComsol(BaseModel):
    available: bool = False
    version: str = "Unknown"
    path: str = ""


class SoftwareCatalog(BaseModel):
    gromacs: Optional[SoftwareGromacs] = None
    orca: Optional[SoftwareOrca] = None
    comsol: Optional[SoftwareComsol] = None
    python_version: str = ""
    installed_packages: List[str] = Field(default_factory=list)


class ReleaseChannel(str, Enum):
    CANARY = "canary"
    BETA = "beta"
    STABLE = "stable"


class UpdatePolicy(str, Enum):
    WAIT_FOR_JOB = "wait_for_job"
    CHECKPOINT = "checkpoint"
    FORCE = "force"


class ComponentType(str, Enum):
    AGENT = "agent"
    ADAPTER = "adapter"
    CONFIG = "config"


class UpdateStatus(str, Enum):
    UP_TO_DATE = "up_to_date"
    UPDATE_AVAILABLE = "update_available"
    UPDATING = "updating"
    FAILED = "failed"


class ReleasePackageInfo(BaseModel):
    url: str
    sha256: str
    size_bytes: int = 0
    filename: Optional[str] = None


class ReleaseManifest(BaseModel):
    version: str
    component: ComponentType = ComponentType.AGENT
    adapter_name: Optional[str] = None
    channel: ReleaseChannel = ReleaseChannel.STABLE
    package: ReleasePackageInfo
    minimum_agent_version: str = "0.1.0"
    changelog: str = ""
    created_at: float = Field(default_factory=time.time)
    is_active: bool = True


class UpdateCheckRequest(BaseModel):
    node_id: str
    agent_version: str = "0.1.0"
    channel: ReleaseChannel = ReleaseChannel.STABLE
    platform: str = "windows-x64"


class UpdateCheckResponse(BaseModel):
    update_available: bool
    version: Optional[str] = None
    component: ComponentType = ComponentType.AGENT
    adapter_name: Optional[str] = None
    mandatory: bool = False
    url: Optional[str] = None
    sha256: Optional[str] = None
    size_bytes: int = 0
    changelog: str = ""


class DynamicNodeConfig(BaseModel):
    max_cpu_percent: float = 90.0
    max_gpu_jobs: int = 1
    allowed_hours: List[str] = Field(default_factory=list)
    update_channel: ReleaseChannel = ReleaseChannel.STABLE
    update_policy: UpdatePolicy = UpdatePolicy.WAIT_FOR_JOB
    software_enabled: Dict[str, bool] = Field(default_factory=lambda: {"gromacs": True, "orca": True, "comsol": True})


class NodeInfo(BaseModel):
    node_id: str
    node_name: str
    ip_address: str = "127.0.0.1"
    role: NodeRole = NodeRole.WORKER
    status: NodeState = NodeState.IDLE
    telemetry: NodeTelemetry = Field(default_factory=NodeTelemetry)
    software: SoftwareCatalog = Field(default_factory=SoftwareCatalog)
    last_heartbeat: float = Field(default_factory=time.time)
    registered_at: float = Field(default_factory=time.time)
    active_job_id: Optional[str] = None
    agent_version: str = "0.1.0"
    channel: ReleaseChannel = ReleaseChannel.STABLE
    update_status: UpdateStatus = UpdateStatus.UP_TO_DATE


class EnrollmentRequest(BaseModel):
    invite_code: str
    node_name: str
    role: NodeRole = NodeRole.WORKER
    ip_address: str = "127.0.0.1"
    software: SoftwareCatalog = Field(default_factory=SoftwareCatalog)
    telemetry: NodeTelemetry = Field(default_factory=NodeTelemetry)
    agent_version: str = "0.1.0"
    channel: ReleaseChannel = ReleaseChannel.STABLE


class EnrollmentResponse(BaseModel):
    success: bool
    node_id: Optional[str] = None
    node_token: Optional[str] = None
    message: str = ""
    assigned_channel: ReleaseChannel = ReleaseChannel.STABLE


class HeartbeatRequest(BaseModel):
    node_id: str
    node_token: str
    status: NodeState
    telemetry: NodeTelemetry
    active_job_id: Optional[str] = None
    agent_version: Optional[str] = None
    channel: Optional[ReleaseChannel] = None
    update_status: Optional[UpdateStatus] = None


class HeartbeatResponse(BaseModel):
    acknowledged: bool = True
    assigned_job_id: Optional[str] = None
    command: Optional[str] = None
    update_available: Optional[UpdateCheckResponse] = None
    config_patch: Optional[Dict[str, Any]] = None


class JobRequirements(BaseModel):
    software: str = "gromacs"
    require_gpu: bool = False
    min_vram_mb: float = 0.0
    min_ram_mb: float = 0.0


class JobStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobSubmission(BaseModel):
    name: str
    adapter: str = "gromacs"
    requirements: JobRequirements = Field(default_factory=JobRequirements)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 1


class JobDetail(BaseModel):
    job_id: str
    name: str
    adapter: str
    requirements: JobRequirements
    parameters: Dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    assigned_node_id: Optional[str] = None
    assigned_node_name: Optional[str] = None
    progress_pct: float = 0.0
    log_tail: str = ""
    error_message: Optional[str] = None
    submitted_at: float = Field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    bundle_path: Optional[str] = None
    result_archive: Optional[str] = None


class JobProgressUpdate(BaseModel):
    job_id: str
    node_id: str
    status: JobStatus
    progress_pct: float = 0.0
    log_chunk: str = ""
    error_message: Optional[str] = None
