"""
FastAPI application entrypoint for ChemCompute Controller.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from chemcompute.controller.database import Database
from chemcompute.controller.scheduler import Scheduler
from chemcompute.controller.routes.nodes import router as nodes_router
from chemcompute.controller.routes.enroll import router as enroll_router
from chemcompute.controller.routes.jobs import router as jobs_router
from chemcompute.controller.routes.updates import router as updates_router
from chemcompute.controller.routes.templates import router as templates_router
from chemcompute.controller.websocket import ws_manager
from chemcompute.common.models import NodeState, JobStatus
from fastapi import WebSocket, WebSocketDisconnect


DATA_DIR = Path.cwd() / "controller_data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "chemcompute.db"
STORAGE_ROOT = DATA_DIR / "storage"
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

db = Database(DB_PATH)
db.seed_official_releases()
scheduler = Scheduler(db)


async def background_scheduler_loop():
    """Periodically execute scheduler cycles to match pending jobs."""
    while True:
        try:
            scheduler.schedule_cycle()
        except Exception:
            pass
        await asyncio.sleep(2.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup background task
    if not hasattr(app.state, "db") or app.state.db is None:
        app.state.db = db
    if not hasattr(app.state, "storage_root") or app.state.storage_root is None:
        app.state.storage_root = STORAGE_ROOT
    task = asyncio.create_task(background_scheduler_loop())
    yield
    task.cancel()


app = FastAPI(
    title="ChemCompute Controller",
    description="Distributed Chemical Computation Node Grid & Scheduler",
    version="0.3.0",
    lifespan=lifespan
)

app.state.db = db
app.state.storage_root = STORAGE_ROOT


def create_app(cfg=None) -> FastAPI:
    return app

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(nodes_router)
app.include_router(enroll_router)
app.include_router(jobs_router)
app.include_router(updates_router)
app.include_router(templates_router)


@app.websocket("/ws/nodes/{node_id}")
async def websocket_node_endpoint(websocket: WebSocket, node_id: str):
    await ws_manager.connect_node(node_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Can process incoming agent ping or status ack
    except WebSocketDisconnect:
        await ws_manager.disconnect_node(node_id)
    except Exception:
        await ws_manager.disconnect_node(node_id)


@app.websocket("/ws/console")
async def websocket_console_endpoint(websocket: WebSocket):
    await ws_manager.connect_console(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect_console(websocket)
    except Exception:
        await ws_manager.disconnect_console(websocket)


# Mount static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def serve_index():
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "ChemCompute Controller is running. Web console static files not found."}


@app.get("/api/cluster/stats")
def get_cluster_stats():
    """Get high-level summary metrics of nodes and jobs."""
    nodes = db.list_nodes()
    jobs = db.list_jobs()

    online_nodes = [n for n in nodes if n.status != NodeState.OFFLINE]
    running_jobs = [j for j in jobs if j.status in [JobStatus.RUNNING, JobStatus.ASSIGNED]]
    completed_jobs = [j for j in jobs if j.status == JobStatus.COMPLETED]

    return {
        "total_nodes": len(nodes),
        "online_nodes": len(online_nodes),
        "total_jobs": len(jobs),
        "running_jobs": len(running_jobs),
        "completed_jobs": len(completed_jobs)
    }
