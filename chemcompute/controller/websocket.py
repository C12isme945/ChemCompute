"""
WebSocket Gateway for ChemCompute Controller.
Handles real-time bi-directional messaging between Controller, Agents, and Web Consoles.
"""

import asyncio
import json
from typing import Dict, Set, Any
from fastapi import WebSocket


class ConnectionManager:
    """Manages active WebSocket connections from nodes and consoles."""

    def __init__(self):
        # node_id -> WebSocket
        self.node_connections: Dict[str, WebSocket] = {}
        # Active console subscriber websockets
        self.console_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect_node(self, node_id: str, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.node_connections[node_id] = websocket

    async def disconnect_node(self, node_id: str):
        async with self._lock:
            if node_id in self.node_connections:
                del self.node_connections[node_id]

    async def connect_console(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.console_connections.add(websocket)

    async def disconnect_console(self, websocket: WebSocket):
        async with self._lock:
            self.console_connections.discard(websocket)

    async def send_to_node(self, node_id: str, message: Dict[str, Any]) -> bool:
        """Send JSON message directly to a specific connected node."""
        ws = self.node_connections.get(node_id)
        if ws:
            try:
                await ws.send_text(json.dumps(message))
                return True
            except Exception:
                await self.disconnect_node(node_id)
        return False

    async def broadcast_to_nodes(self, message: Dict[str, Any]):
        """Broadcast message to all connected worker nodes."""
        payload = json.dumps(message)
        dead = []
        for nid, ws in list(self.node_connections.items()):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(nid)
        for nid in dead:
            await self.disconnect_node(nid)

    async def broadcast_to_consoles(self, message: Dict[str, Any]):
        """Broadcast an event update to all connected Web Consoles."""
        payload = json.dumps(message)
        dead = set()
        for ws in list(self.console_connections):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            await self.disconnect_console(ws)


ws_manager = ConnectionManager()
