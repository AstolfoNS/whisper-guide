"""Public presentation settings, independent of private model configuration."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["application"])


class AppConfigResponse(BaseModel):
    app_title: str


@router.get("/app-config", response_model=AppConfigResponse)
async def app_config(request: Request) -> AppConfigResponse:
    return AppConfigResponse(app_title=request.app.state.settings.app_title)
