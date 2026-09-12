"""
Tests for Task-Aware Update Deferral and Configuration Hot-Push.
"""

from pathlib import Path
from chemcompute.agent.daemon import AgentDaemon
from chemcompute.agent.update_client import UpdateClient
from chemcompute.common.models import NodeState, ReleaseChannel


def test_task_aware_update_deferral(tmp_path):
    config_file = tmp_path / "node_config.json"
    workspace = tmp_path / "workspace"

    daemon = AgentDaemon(
        controller_url="http://127.0.0.1:8000",
        config_path=config_file,
        workspace_dir=workspace,
        node_name="TestNode",
        agent_version="0.1.0",
        channel=ReleaseChannel.STABLE,
        agent_root_dir=tmp_path
    )
    daemon.node_id = "node-test-123"
    daemon.node_token = "mock-token"

    client = UpdateClient(daemon)

    # 1. Simulate node is BUSY running a 48h GROMACS simulation
    daemon.status = NodeState.BUSY
    daemon.active_job_id = "job-gromacs-md-48h"
    daemon.active_job_adapter = "gromacs"

    update_event = {
        "type": "agent.update_available",
        "version": "0.2.0",
        "url": "http://127.0.0.1:8000/releases/0.2.0.zip",
        "sha256": "fakehash"
    }

    # Intercept execute_agent_update to verify it is NOT called during calculation
    executed_updates = []
    client.execute_agent_update = lambda ev: executed_updates.append(ev)

    # 2. Receive update event while calculating
    client.handle_event(update_event)

    # Verify update was deferred and NOT immediately executed!
    assert len(executed_updates) == 0, "Update must NOT be executed while calculation is running!"
    assert daemon.pending_update is not None
    assert daemon.pending_update["version"] == "0.2.0"

    # 3. Simulate job completion: daemon finishes job and triggers deferred update
    daemon.status = NodeState.IDLE
    daemon.active_job_id = None
    daemon.active_job_adapter = None

    if daemon.pending_update:
        to_exec = daemon.pending_update
        daemon.pending_update = None
        client.execute_agent_update(to_exec)

    assert len(executed_updates) == 1
    assert executed_updates[0]["version"] == "0.2.0"
    print("\n[OK] Task-aware update deferral successfully verified!")


def test_configuration_hot_push(tmp_path):
    config_file = tmp_path / "node_config.json"
    workspace = tmp_path / "workspace"

    daemon = AgentDaemon(
        controller_url="http://127.0.0.1:8000",
        config_path=config_file,
        workspace_dir=workspace,
        node_name="TestNode",
        agent_version="0.1.0",
        channel=ReleaseChannel.STABLE,
        agent_root_dir=tmp_path
    )
    client = UpdateClient(daemon)

    # Check initial config default
    assert daemon.config_manager.config.max_cpu_percent == 90.0

    # Push hot configuration patch
    config_event = {
        "type": "config.push",
        "config": {
            "max_cpu_percent": 75.0,
            "max_gpu_jobs": 2,
            "allowed_hours": ["00:00-08:00"]
        }
    }
    client.handle_event(config_event)

    # Verify config updated dynamically in memory
    assert daemon.config_manager.config.max_cpu_percent == 75.0
    assert daemon.config_manager.config.max_gpu_jobs == 2
    assert daemon.config_manager.config.allowed_hours == ["00:00-08:00"]
    print("\n[OK] Configuration hot-push verified successfully!")
