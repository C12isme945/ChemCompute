"""ChemCompute 节点代理服务核心循环 (心跳上报、作业接收与执行)"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import httpx

from chemcompute.common.config import NodeConfig, save_yaml_config
from chemcompute.common.models import (
    JobSpec,
    NodeHeartbeatRequest,
    NodeRegisterRequest,
)
from chemcompute.contribution import compute_budget, load_settings, snapshot
from chemcompute.node.adapters.gromacs import GromacsAdapter
from chemcompute.node.hardware import collect_hardware_info
from chemcompute.node.software import collect_software_info

logger = logging.getLogger("chemcompute.node")


class NodeAgent:
    """ChemCompute 计算节点工作代理"""

    def __init__(self, config: NodeConfig, config_path: Path | str | None = None) -> None:
        self.config = config
        self.config_path = Path(config_path) if config_path else None
        self.running = False
        self.execution_lock = threading.Lock()
        self.active_task = False
        self.adapter = GromacsAdapter(
            custom_executable_path=config.gromacs_custom_path,
            workspace_dir=config.workspace_dir,
            max_timeout_seconds=config.max_job_timeout_seconds,
        )

    def register_if_needed(self) -> bool:
        """检查节点认证凭据，如未注册则使用邀请码完成注册入网"""
        if self.config.node_id and self.config.node_token:
            logger.info("节点已持有认证凭据 (Node ID: %s)，跳过注册流程", self.config.node_id)
            return True

        if not self.config.invite_code:
            self.write_connection_status('needs_invite')
            logger.error(
                "节点尚未在控制端登记，且未提供 invite_code。请在配置文件或参数中指定有效邀请码后重试。"
            )
            return False

        logger.info("正在向控制端 %s 发起入网注册...", self.config.controller_url)
        hw_info = collect_hardware_info()
        hw_info.contribution = compute_budget(load_settings(self.config_path), hw_info)
        sw_info = collect_software_info(self.config.gromacs_custom_path, self.config.gaussian_custom_path)

        req_body = NodeRegisterRequest(
            invite_code=self.config.invite_code.strip(),
            node_name=self.config.node_name or sw_info.hostname,
            hostname=sw_info.hostname,
            os=f"{sw_info.os_name} {sw_info.os_release}",
            hardware=hw_info,
            software=sw_info,
        )

        register_url = f"{self.config.controller_url.rstrip('/')}/api/v1/nodes/register"
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(register_url, json=req_body.model_dump())
                if resp.status_code != 200:
                    self.write_connection_status('registration_failed')
                    err_msg = resp.json().get("detail", resp.text) if resp.headers.get("content-type") == "application/json" else resp.text
                    logger.error("向控制端注册失败 (%d): %s", resp.status_code, err_msg)
                    return False

                data = resp.json()
                self.config.node_id = data["node_id"]
                self.config.node_token = data["node_token"]
                self.config.heartbeat_interval_seconds = data.get("heartbeat_interval_seconds", 15)
                # 抹除已被单次消耗的邀请码
                self.config.invite_code = None

                # 持久化回写配置
                if self.config_path:
                    try:
                        save_yaml_config(self.config, self.config_path)
                        logger.info("节点凭证已安全保存至配置文件: %s", self.config_path)
                    except Exception as save_err:
                        logger.warning("保存更新后的节点配置失败: %s", save_err)

                logger.info(">>> 节点注册成功! 分配节点 ID: %s <<<", self.config.node_id)
                self.write_connection_status('registered')
                return True
        except Exception as e:
            self.write_connection_status('waiting')
            logger.error("连接控制端注册异常: %s", e)
            return False

    def send_heartbeat(self, client: httpx.Client) -> list[JobSpec]:
        """向控制端发送一次健康心跳并接收下发的作业"""
        from chemcompute.update_maintenance import local_intake_paused
        if local_intake_paused():
            return []
        if not self.config.node_id or not self.config.node_token:
            return []

        heartbeat_url = f"{self.config.controller_url.rstrip('/')}/api/v1/nodes/{self.config.node_id}/heartbeat"
        headers = {
            "Authorization": f"Bearer {self.config.node_token}",
            "Content-Type": "application/json",
        }

        hw = collect_hardware_info()
        hw.contribution = compute_budget(load_settings(self.config_path), hw)
        sw = collect_software_info(self.config.gromacs_custom_path, self.config.gaussian_custom_path)

        req_body = NodeHeartbeatRequest(
            node_id=self.config.node_id,
            status="busy" if self.active_task or hw.contribution["paused"] else "online",
            hardware=hw,
            software=sw,
        )

        resp = client.post(heartbeat_url, json=req_body.model_dump(), headers=headers)
        if resp.status_code == 401:
            self.write_connection_status('needs_invite')
            logger.error("心跳上报鉴权失败：节点令牌失效，请重新生成邀请码注册")
            return []
        if resp.status_code != 200:
            logger.warning("心跳上报异常 (HTTP %d): %s", resp.status_code, resp.text)
            return []

        data = resp.json()
        self.write_connection_status('connected')
        raw_jobs = data.get("assigned_jobs", [])
        return [JobSpec(**j) for j in raw_jobs]

    def write_connection_status(self, state):
        """Local UI feedback without credentials or remote exception contents."""
        if not self.config_path:
            return
        import json
        try:
            target = self.config_path.with_name('connection-status.json')
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps({'state': state, 'time': time.time()}), encoding='utf-8')
            temporary.replace(target)
        except OSError:
            logger.warning('Cannot save local connection status')

    def execute_and_report_job(self, client: httpx.Client, job: JobSpec) -> None:
        """执行单一受控 GROMACS 计算作业并回传执行成果与日志"""
        logger.info("开始执行 GROMACS 作业 %s: gmx %s %s", job.job_id, job.subcommand, " ".join(job.arguments))

        res = self.adapter.run_bounded(
            subcommand=job.subcommand,
            arguments=job.arguments,
            timeout_seconds=job.timeout_seconds,
            contribution_budget=snapshot(self.config_path),
        )

        job_status = "completed" if res.exit_code == 0 else ("timeout" if res.exit_code == -9 else "failed")
        logger.info("作业 %s 执行完成，状态: %s, 耗时: %ss", job.job_id, job_status, res.duration_seconds)

        status_url = f"{self.config.controller_url.rstrip('/')}/api/v1/jobs/{job.job_id}/status"
        headers = {
            "Authorization": f"Bearer {self.config.node_token}",
            "Content-Type": "application/json",
        }
        report_payload = {
            "job_id": job.job_id,
            "status": job_status,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "error_message": res.error_message,
        }

        try:
            resp = client.post(status_url, json=report_payload, headers=headers)
            if resp.status_code != 200:
                logger.error("回传作业 %s 状态失败 (%d): %s", job.job_id, resp.status_code, resp.text)
        except Exception as e:
            logger.error("上报作业状态连接异常: %s", e)

    def run(self) -> None:
        """启动主循环"""
        if not self.register_if_needed():
            logger.error("节点未能建立合法入网状态，退出代理")
            return

        self.running = True
        from chemcompute.node.task_worker import run_queue
        threading.Thread(target=run_queue, args=(self,), daemon=True).start()
        logger.info("ChemCompute 节点代理已进入运行状态，心跳周期: %d 秒", self.config.heartbeat_interval_seconds)

        with httpx.Client(timeout=15.0) as client:
            while self.running:
                try:
                    jobs = self.send_heartbeat(client)
                    for job in jobs:
                        with self.execution_lock:
                            self.execute_and_report_job(client, job)
                except Exception as e:
                    logger.warning("心跳循环捕获异常: %s", e)

                # 间隔睡眠
                sleep_left = self.config.heartbeat_interval_seconds
                while sleep_left > 0 and self.running:
                    time.sleep(min(1.0, sleep_left))
                    sleep_left -= 1.0

        logger.info("ChemCompute 节点代理已正常停止")

    def stop(self) -> None:
        """退出循环标志"""
        self.running = False
