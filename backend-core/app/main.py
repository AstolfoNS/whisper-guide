import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.recovery import router as recovery_router
from app.api.scripts import router as scripts_router
from app.api.session import router as session_router
from app.api.settings import router as settings_router
from app.config import Settings, clone_model_settings, get_settings, load_model_settings
from app.database import Base, create_database_engine
from app.repositories.model_configs import ModelConfigRepository


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = load_model_settings(settings if settings is not None else get_settings())
    model_configs = ModelConfigRepository(configuration)
    configuration = clone_model_settings(configuration, model_configs.active_snapshot()["settings"])

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(configuration)
        application.state.database_engine = engine
        try:
            Base.metadata.create_all(engine)
            yield
        finally:
            pending = []
            for runtime in getattr(application.state, "sessions", {}).values():
                runtime.cancel_prompt()
                runtime.release_stream()
                if runtime.socket:
                    pending.append(runtime.socket.close(code=1001))
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            engine.dispose()

    application = FastAPI(
        title="芯创客无障碍语音提示助手 API",
        description="Server-side speech tracking and prompting application.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = configuration
    application.state.model_configs = model_configs
    application.state.model_config_lock = model_configs.lock
    application.add_middleware(
        CORSMiddleware,
        allow_origins=configuration.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    application.include_router(health_router, prefix="/api")
    application.include_router(recovery_router, prefix="/api")
    application.include_router(scripts_router, prefix="/api")
    application.include_router(session_router, prefix="/api")
    application.include_router(settings_router, prefix="/api")
    return application


app = create_app()
