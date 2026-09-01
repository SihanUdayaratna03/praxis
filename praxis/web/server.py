"""The only module in Praxis that binds a socket. ADR 0037.

`praxis/llm/anthropic.py` is the other side of the network; this is this side.
Everything else under `praxis.web` is routing and conversion, and
`tests/test_boundaries.py` keeps it that way.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Final

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from praxis.config.settings import Settings
from praxis.obs.logging import get_logger
from praxis.store.repository import Repository
from praxis.web.routes import router

STATIC_ROOT: Final = Path(__file__).parent / "static"
"""The pages, shipped inside the wheel rather than looked up on the filesystem."""

DEFAULT_HOST: Final = "127.0.0.1"
"""Loopback. Serving the store to a network is a decision, not a default."""

DEFAULT_PORT: Final = 8000

log = get_logger(__name__)


def create_app(repository: Repository, *, provider: str) -> FastAPI:
    """Build the application around an already-open store.

    The repository is passed in rather than opened here so a test can hand it
    an in-memory store, and so nothing about the store's location is decided by
    the web layer.

    Args:
        repository: The store to read. Never written to.
        provider: Which LLM provider the traces came from, for the status chip.
    """
    app = FastAPI(
        title="Praxis",
        summary="Decisions that invalidate themselves, estimates that learn your bias.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.repository = repository
    # One store handle, one lock. Starlette hands each request to a
    # threadpool worker; the lock is what keeps two of them off the
    # connection at once. See `routes.store`.
    app.state.lock = threading.Lock()
    app.state.provider = provider
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def landing() -> FileResponse:
        """The landing page."""
        return FileResponse(STATIC_ROOT / "index.html")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard_page() -> FileResponse:
        """The dashboard shell. Every view under it is rendered in the browser."""
        return FileResponse(STATIC_ROOT / "app.html")

    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
    return app


def serve(
    repository: Repository,
    settings: Settings,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Run the dashboard until interrupted.

    Args:
        repository: The store to read.
        settings: Read for the provider name shown in the status chip.
        host: Interface to bind. Loopback unless the caller says otherwise.
        port: Port to bind.
    """
    app = create_app(repository, provider=settings.llm_provider.value)
    log.info("dashboard_serving", host=host, port=port, provider=settings.llm_provider.value)
    uvicorn.run(app, host=host, port=port, log_level="warning")
