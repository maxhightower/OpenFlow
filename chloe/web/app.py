"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chloe.web.config import WebConfig
from chloe.web.deps import init_dependencies, shutdown_dependencies
from chloe.web.routers import (
    projects,
    dag,
    scheduler,
    budget,
    analytics,
    runs,
    cross_deps,
    events_sse,
)


def create_app(config: WebConfig | None = None) -> FastAPI:
    if config is None:
        config = WebConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        init_dependencies(config)
        yield
        shutdown_dependencies()

    app = FastAPI(
        title="Chloe",
        description="Resource optimization scheduler for Claude Code subscriptions",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(projects.router)
    app.include_router(dag.router)
    app.include_router(scheduler.router)
    app.include_router(budget.router)
    app.include_router(analytics.router)
    app.include_router(runs.router)
    app.include_router(cross_deps.router)
    app.include_router(events_sse.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    return app
