"""WebSocket for real-time progress updates."""

import socketio
from fastapi import FastAPI

sio = socketio.AsyncServer(cors_allowed_origins="*", async_mode="asgi")


async def setup_websocket(app: FastAPI) -> None:
    """Attach Socket.IO to FastAPI app."""
    asgi_app = socketio.ASGIApp(sio, other_asgi_app=app)
    # In a real setup we'd mount this, but for now we just set up handlers

    @sio.event
    async def connect(sid, environ):
        print(f"Client connected: {sid}")

    @sio.event
    async def disconnect(sid):
        print(f"Client disconnected: {sid}")

    @sio.event
    async def subscribe_progress(sid, data):
        project_id = data.get("project_id")
        await sio.enter_room(sid, f"project:{project_id}")
        await sio.emit("subscribed", {"project_id": project_id}, to=sid)


async def emit_progress(project_id: str, stage: str, percent: int, message: str = "") -> None:
    """Emit progress update to project room."""
    await sio.emit("progress", {
        "project_id": project_id,
        "stage": stage,
        "percent": percent,
        "message": message,
    }, room=f"project:{project_id}")
