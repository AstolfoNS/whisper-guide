"""Public UI defaults, independent of private model configuration."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["application"])


class AppConfigResponse(BaseModel):
    app_title: str
    default_silence_timeout_ms: int
    script_max_file_bytes: int
    script_max_upload_files: int


@router.get("/app-config", response_model=AppConfigResponse)
async def app_config(request: Request) -> AppConfigResponse:
    settings = request.app.state.settings
    return AppConfigResponse(
        app_title=settings.app_title,
        default_silence_timeout_ms=settings.default_silence_timeout_ms,
        script_max_file_bytes=settings.script_max_file_bytes,
        script_max_upload_files=settings.script_max_upload_files,
    )
