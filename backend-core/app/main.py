from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.config import Settings, get_settings
from app.database import create_database_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(configuration)
        application.state.database_engine = engine
        try:
            yield
        finally:
            engine.dispose()

    application = FastAPI(
        title="Whisper Guide API",
        description="Server-side speech tracking and prompting application.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = configuration
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configuration.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    application.include_router(health_router, prefix="/api")
    return application


app = create_app()
