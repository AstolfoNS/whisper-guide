"""Explicit recovery of disconnected sessions without exposing speech content."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.session import now_ms, runtimes
from app.domain.session import SessionState

router = APIRouter(tags=["sessions"])


@router.get("/sessions/recoverable")
async def recoverable_sessions(request: Request) -> dict[str, list[dict[str, Any]]]:
    # This async route reads the registry on the WebSocket event loop. Do not
    # offload it to the thread pool or await while iterating live runtimes.
    with request.app.state.model_config_lock:
        sessions = [
            {
                "session_id": runtime.session.id,
                "script_name": runtime.script_name,
                "state": runtime.session.state.value,
                "config_name": getattr(runtime, "model_config_name", ""),
            }
            for runtime in runtimes(request.app).values()
            if runtime.socket is None and runtime.session.state != SessionState.TERMINATED
        ]
    return {"sessions": sessions}


@router.post("/sessions/{identifier}/terminate")
async def terminate_disconnected_session(request: Request, identifier: str) -> dict[str, str]:
    registry = runtimes(request.app)
    with request.app.state.model_config_lock:
        runtime = registry.get(identifier)
        if runtime is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "SESSION_NOT_FOUND", "message": "会话已结束或不存在。"},
            )
    # Never retain the synchronous configuration lock across an await. A stream
    # operation may hold runtime.lock while a reconnect establishes ownership.
    async with runtime.lock:
        with request.app.state.model_config_lock:
            if registry.get(identifier) is not runtime:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "SESSION_NOT_FOUND", "message": "会话已结束或不存在。"},
                )
            if runtime.socket is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "SESSION_CONNECTED",
                        "message": "会话正在其他页面使用，请在所属工作台结束演讲。",
                    },
                )
            runtime.session.handle("end", now_ms())
            runtime.cancel_prompt()
            runtime.release_stream()
            registry.pop(identifier)
    return {"session_id": identifier, "state": SessionState.TERMINATED.value}
