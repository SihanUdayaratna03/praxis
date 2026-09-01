"""`praxis serve`: the dashboard, over the store this machine already has.

Its own module for the reason `praxis.cli_fuse` is -- `cli.py` is already the
size the style guide calls a file.

The command opens the store read-only and hands it to the app. It never
migrates and never writes, so pointing it at a store from an older schema is a
sentence rather than a silent upgrade.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from praxis.config.settings import get_settings
from praxis.obs.logging import configure_logging
from praxis.store.errors import StoreError
from praxis.store.repository import open_repository
from praxis.web.server import DEFAULT_HOST, DEFAULT_PORT
from praxis.web.server import serve as run_server

console = Console()


def serve(
    host: Annotated[
        str, typer.Option(help="Interface to bind. Loopback unless you mean otherwise.")
    ] = DEFAULT_HOST,
    port: Annotated[int, typer.Option(help="Port to bind.")] = DEFAULT_PORT,
) -> None:
    """Serve the dashboard and the landing page from the local store.

    Read-only. No credentials, no network calls, nothing written.
    """
    settings = get_settings()
    configure_logging(settings)
    try:
        # cross_thread because the server hands each request to a threadpool
        # worker; praxis.web.routes holds a lock across every one. ADR 0037.
        repository = open_repository(settings, create=False, cross_thread=True)
    except StoreError as exc:
        console.print(f"[bold red]praxis: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    if host != DEFAULT_HOST:
        console.print(
            f"[bold yellow]warning[/bold yellow] binding {host} serves this store to the "
            "network. It holds whatever you have ingested."
        )
    console.print(f"[bold green]praxis serve[/bold green] http://{host}:{port}")
    console.print(f"  store      {settings.db_path}")
    console.print(f"  provider   {settings.llm_provider.value}")
    console.print("  press Ctrl+C to stop")
    try:
        run_server(repository, settings, host=host, port=port)
    finally:
        repository.close()
