"""
ChemCompute Smart Job Scheduler.
Matches pending chemistry computing jobs to optimal idle nodes based on hardware & software specs.
"""

import time
from typing import Optional, List
from chemcompute.common.models import NodeInfo, NodeState, JobDetail
from chemcompute.controller.database import Database


class Scheduler:
    """Smart scheduler for ChemCompute cluster."""

    def __init__(self, db: Database):
        self.db = db

    def find_best_node_for_job(self, job: JobDetail, available_nodes: List[NodeInfo]) -> Optional[NodeInfo]:
        """Filter and rank nodes that satisfy job requirements."""
        req = job.requirements
        candidates = []

        now = time.time()
        for node in available_nodes:
            # 1. Must be online and idle
            if node.status != NodeState.IDLE:
                continue
            if (now - node.last_heartbeat) > 20.0:
                continue

            # 2. Software match
            req_soft = req.software.lower()
            if req_soft == "gromacs":
                if not (node.software.gromacs and node.software.gromacs.available):
                    continue
            elif req_soft == "orca":
                if not (node.software.orca and node.software.orca.available):
                    continue
            elif req_soft == "comsol":
                if not (node.software.comsol and node.software.comsol.available):
                    continue

            # 3. GPU match
            if req.require_gpu:
                if not node.telemetry.gpu:
                    continue
                if req.min_vram_mb > 0 and node.telemetry.gpu.memory_free_mb < req.min_vram_mb:
                    continue

            # 4. RAM match
            if req.min_ram_mb > 0 and node.telemetry.ram.available_mb < req.min_ram_mb:
                continue

            candidates.append(node)

        if not candidates:
            return None

        # Rank candidates:
        # If GPU required: prefer node with highest free VRAM
        # If CPU: prefer node with lowest CPU utilization and highest free RAM
        if req.require_gpu:
            candidates.sort(
                key=lambda n: n.telemetry.gpu.memory_free_mb if n.telemetry.gpu else 0,
                reverse=True
            )
        else:
            candidates.sort(
                key=lambda n: (-n.telemetry.cpu.utilization_pct, n.telemetry.ram.available_mb),
                reverse=True
            )

        return candidates[0]

    def schedule_cycle(self) -> int:
        """Run one scheduling round. Returns number of jobs dispatched."""
        pending_jobs = self.db.get_pending_jobs()
        if not pending_jobs:
            return 0

        all_nodes = self.db.list_nodes()
        assigned_count = 0

        # Maintain a local tracking set of nodes assigned in this cycle
        assigned_node_ids = set()

        for job in pending_jobs:
            available_nodes = [n for n in all_nodes if n.node_id not in assigned_node_ids]
            target_node = self.find_best_node_for_job(job, available_nodes)

            if target_node:
                self.db.assign_job(job.job_id, target_node.node_id)
                assigned_node_ids.add(target_node.node_id)
                assigned_count += 1

        return assigned_count
