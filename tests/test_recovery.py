"""Tests for standalone RecoveryStore lease state helpers."""
from __future__ import annotations

from chemcompute.recovery import RecoveryStore
from chemcompute.tasks import Step, TaskSpec, TaskStore


def create_running_task(task_store: TaskStore, node_id: str = "node-alpha") -> tuple[str, str]:
    spec = TaskSpec(
        name="test_task",
        steps=[Step(subcommand="version")],
        node_id=node_id,
    )
    created = task_store.create(spec)
    task_id = created["id"]
    # Directly set task to running on node_id to test recovery helpers
    with task_store.connect() as db:
        db.execute(
            "UPDATE tasks_v2 SET status='running', node=? WHERE id=?",
            (node_id, task_id),
        )
    return task_id, node_id


def test_migration_idempotent(tmp_path):
    db_path = tmp_path / "tasks.db"
    # TaskStore initializes the base schema
    TaskStore(db_path)

    # Initialize RecoveryStore, which performs migration
    recovery = RecoveryStore(db_path)

    with recovery.connect() as db:
        cols = {row["name"] for row in db.execute("PRAGMA table_info(tasks_v2)").fetchall()}
        assert "attempt" in cols
        assert "lease_token" in cols
        assert "lease_until" in cols
        assert "checkpoint_hash" in cols
        assert "recovery_count" in cols

    # Run migration again to verify idempotency
    recovery.migrate()
    with recovery.connect() as db:
        cols_after = {row["name"] for row in db.execute("PRAGMA table_info(tasks_v2)").fetchall()}
        assert cols == cols_after


def test_start_lease_and_stale_token_rejection(tmp_path):
    db_path = tmp_path / "tasks.db"
    task_store = TaskStore(db_path)
    recovery = RecoveryStore(db_path)

    task_id, node_id = create_running_task(task_store, "node-1")

    # Start initial lease at simulated time t=100.0
    lease1 = recovery.start_lease(task_id, node_id, seconds=90, now=100.0)
    assert lease1 is not None
    assert lease1["attempt"] == 1
    assert isinstance(lease1["lease_token"], str) and len(lease1["lease_token"]) > 0
    assert lease1["lease_until"] == 190.0

    token1 = lease1["lease_token"]
    assert recovery.authorized(task_id, node_id, token1) is True

    # Re-starting lease simulates re-lease/fencing bump
    lease2 = recovery.start_lease(task_id, node_id, seconds=90, now=120.0)
    assert lease2 is not None
    assert lease2["attempt"] == 2
    assert lease2["lease_until"] == 210.0
    token2 = lease2["lease_token"]
    assert token1 != token2

    # Verify stale token1 is rejected and token2 is authorized
    assert recovery.authorized(task_id, node_id, token1) is False
    assert recovery.authorized(task_id, node_id, token2) is True
    assert recovery.renew(task_id, node_id, token1, seconds=90, now=130.0) is False
    assert recovery.renew(task_id, node_id, token2, seconds=90, now=130.0) is True


def test_start_lease_only_running_assigned_task(tmp_path):
    db_path = tmp_path / "tasks.db"
    task_store = TaskStore(db_path)
    recovery = RecoveryStore(db_path)

    spec = TaskSpec(name="queued_task", steps=[Step(subcommand="version")])
    created = task_store.create(spec)
    task_id = created["id"]

    # Cannot start lease on a queued task
    assert recovery.start_lease(task_id, "node-1", seconds=90, now=100.0) is None

    # Cannot start lease on wrong node
    task_id_running, _ = create_running_task(task_store, "node-1")
    assert recovery.start_lease(task_id_running, "wrong-node", seconds=90, now=100.0) is None


def test_renew_even_when_lease_expired_same_node(tmp_path):
    db_path = tmp_path / "tasks.db"
    task_store = TaskStore(db_path)
    recovery = RecoveryStore(db_path)

    task_id, node_id = create_running_task(task_store, "node-1")
    lease = recovery.start_lease(task_id, node_id, seconds=60, now=100.0)
    token = lease["lease_token"]

    # Fast forward simulated time past lease deadline (expired) without sleeping
    now = 200.0  # lease was until 160.0
    # Same-node reconnect allowed to renew even if lease expired
    assert recovery.renew(task_id, node_id, token, seconds=60, now=now) is True

    record = recovery.get(task_id)
    assert record["lease_until"] == 260.0

    # Wrong node or wrong token rejected
    assert recovery.renew(task_id, "node-2", token, seconds=60, now=now) is False
    assert recovery.renew(task_id, node_id, "wrong-token", seconds=60, now=now) is False


def test_expire_transitions_to_recovering_without_requeue(tmp_path):
    db_path = tmp_path / "tasks.db"
    task_store = TaskStore(db_path)
    recovery = RecoveryStore(db_path)

    task_id1, node_id1 = create_running_task(task_store, "node-1")
    task_id2, node_id2 = create_running_task(task_store, "node-2")
    task_id3, node_id3 = create_running_task(task_store, "node-3")

    recovery.start_lease(task_id1, node_id1, seconds=50, now=100.0)  # expires at 150.0
    recovery.start_lease(task_id2, node_id2, seconds=150, now=100.0) # expires at 250.0
    # task_id3 has lease_until = 0.0 (never started lease)

    # At t=160.0, only task1 has expired
    expired = recovery.expire(now=160.0)
    assert expired == [task_id1]

    # Task 1 must be 'recovering', NOT 'queued', and node must NOT be cleared
    rec1 = recovery.get(task_id1)
    assert rec1["status"] == "recovering"
    assert rec1["node"] == node_id1

    # Task 2 remains running
    rec2 = recovery.get(task_id2)
    assert rec2["status"] == "running"

    # Task 3 with lease_until=0 remains running
    rec3 = recovery.get(task_id3)
    assert rec3["status"] == "running"

    # No automatic cross-node reclaim: claiming node cannot claim a recovering task
    candidate_node = {
        "node_id": "node-4",
        "status": "online",
        "software_info": {"gromacs": {"found": True, "cuda_enabled": False}},
        "hardware_info": {"cpu_count_logical": 8, "ram_available_mb": 16384, "gpus": []},
    }
    claim_result = task_store.claim(candidate_node)
    assert claim_result is None


def test_resume_transitions_recovering_and_running(tmp_path):
    db_path = tmp_path / "tasks.db"
    task_store = TaskStore(db_path)
    recovery = RecoveryStore(db_path)

    task_id, node_id = create_running_task(task_store, "node-1")
    lease = recovery.start_lease(task_id, node_id, seconds=50, now=100.0)
    token = lease["lease_token"]
    initial_attempt = lease["attempt"]

    # Expire to recovering
    recovery.expire(now=160.0)
    assert recovery.get(task_id)["status"] == "recovering"

    # Resume back to running with new lease; same token, same attempt
    res = recovery.resume(task_id, node_id, token, seconds=90, now=170.0)
    assert res is True

    record = recovery.get(task_id)
    assert record["status"] == "running"
    assert record["attempt"] == initial_attempt
    assert record["lease_token"] == token
    assert record["lease_until"] == 260.0

    # Resume also works directly on a running task
    res_again = recovery.resume(task_id, node_id, token, seconds=60, now=270.0)
    assert res_again is True
    assert recovery.get(task_id)["lease_until"] == 330.0


def test_resume_with_cancelled_flag(tmp_path):
    db_path = tmp_path / "tasks.db"
    task_store = TaskStore(db_path)
    recovery = RecoveryStore(db_path)

    task_id, node_id = create_running_task(task_store, "node-1")
    lease = recovery.start_lease(task_id, node_id, seconds=50, now=100.0)
    token = lease["lease_token"]

    # Mark task cancelled
    with task_store.connect() as db:
        db.execute("UPDATE tasks_v2 SET cancelled=1 WHERE id=?", (task_id,))

    recovery.expire(now=160.0)
    assert recovery.get(task_id)["status"] == "recovering"

    # Resume must succeed so worker can see cancellation flag
    assert recovery.resume(task_id, node_id, token, seconds=60, now=170.0) is True
    rec = recovery.get(task_id)
    assert rec["status"] == "running"
    assert rec["cancelled"] == 1


def test_confirm_stopped_requeues_or_cancels(tmp_path):
    db_path = tmp_path / "tasks.db"
    task_store = TaskStore(db_path)
    recovery = RecoveryStore(db_path)

    # 1. Normal task: transitions to queued, node and lease cleared, recovery_count bumped
    task_id1, node_id1 = create_running_task(task_store, "node-1")
    lease1 = recovery.start_lease(task_id1, node_id1, seconds=50, now=100.0)
    token1 = lease1["lease_token"]
    recovery.expire(now=160.0)

    # Rejects invalid token or node
    assert recovery.confirm_stopped(task_id1, "node-wrong", token1, now=170.0) is False
    assert recovery.confirm_stopped(task_id1, node_id1, "bad-token", now=170.0) is False

    # Succeeds with correct token
    assert recovery.confirm_stopped(task_id1, node_id1, token1, now=170.0) is True
    rec1 = recovery.get(task_id1)
    assert rec1["status"] == "queued"
    assert rec1["node"] == ""
    assert rec1["lease_token"] == ""
    assert rec1["lease_until"] == 0.0
    assert rec1["recovery_count"] == 1

    # 2. Cancelled task: transitions to terminal 'cancelled'
    task_id2, node_id2 = create_running_task(task_store, "node-2")
    lease2 = recovery.start_lease(task_id2, node_id2, seconds=50, now=100.0)
    token2 = lease2["lease_token"]
    with task_store.connect() as db:
        db.execute("UPDATE tasks_v2 SET cancelled=1 WHERE id=?", (task_id2,))
    recovery.expire(now=160.0)

    assert recovery.confirm_stopped(task_id2, node_id2, token2, now=170.0) is True
    rec2 = recovery.get(task_id2)
    assert rec2["status"] == "cancelled"
    assert rec2["node"] == ""
    assert rec2["lease_token"] == ""
    assert rec2["lease_until"] == 0.0
    assert rec2["recovery_count"] == 1
