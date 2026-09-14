"""FastAPI application factory, error handling and application lifespan.

Responsibilities (and nothing else):

* build the container,
* register routers,
* translate typed application errors into HTTP responses,
* manage the startup/shutdown lifecycle,
* add CORS when explicitly configured.

There is no business logic here, no provider code and no SQL - exactly as the
project architecture requires.

Run it with the factory flag::

    uvicorn src.main:create_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.container import build_container
from src.routes import health, procurement
from src.utils.config import Settings, get_settings
from src.utils.exceptions import AppError
from src.utils.logging import get_logger

__all__ = ["create_app"]

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    container = build_container(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        container.start()
        logger.info("application_started", extra=container.settings.safe_summary())
        try:
            yield
        finally:
            container.shutdown()
            logger.info("application_stopped")

    app = FastAPI(
        title=container.settings.app_name,
        version=container.settings.app_version,
        description=(
            "Multi-agent AI procurement research API. Generates search queries, executes them, "
            "extracts structured product data and produces a procurement report."
        ),
        lifespan=lifespan,
    )
    app.state.container = container

    _configure_cors(app, container.settings)
    _register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(procurement.router, prefix=container.settings.api_prefix)
    return app


# --------------------------------------------------------------------------- #
# Middleware / error handling
# --------------------------------------------------------------------------- #


def _configure_cors(app: FastAPI, settings: Settings) -> None:
    if not settings.cors_allow_origins:
        return
    origins = [origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()]
    if not origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    logger.info("cors_enabled", extra={"origins": origins})


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, error: AppError) -> JSONResponse:
        """Translate a typed application error into a stable HTTP contract."""
        request_id = getattr(request.state, "request_id", None)
        logger.warning(
            "request_failed",
            extra={
                "path": request.url.path,
                "method": request.method,
                "code": error.code,
                "status": error.http_status,
                "request_id": request_id,
                "details": error.context,
            },
        )
        payload: dict[str, Any] = {"error": error.to_dict()}
        if request_id:
            payload["error"]["request_id"] = request_id
        return JSONResponse(status_code=error.http_status, content=payload)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        """Never leak internals; log the full context server side instead."""
        request_id = getattr(request.state, "request_id", None)
        logger.error(
            "unhandled_exception",
            extra={
                "path": request.url.path,
                "method": request.method,
                "error": type(error).__name__,
                "request_id": request_id,
            },
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected internal error occurred.",
                    **({"request_id": request_id} if request_id else {}),
                }
            },
        )

    @app.middleware("http")
    async def add_request_context(request: Request, call_next: Any) -> Any:
        """Attach a request id and timing to every request."""
        import uuid

        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-process-time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
        return response


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    import uvicorn

    _settings = get_settings()
    uvicorn.run(
        create_app(_settings),
        host=_settings.host,
        port=_settings.port,
        log_level=_settings.log_level.lower(),
    )
