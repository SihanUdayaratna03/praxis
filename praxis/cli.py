"""The Praxis command line.

Phase 0 ships three commands and no business logic. `doctor` is the one that
earns its place early: the project's central claim is that it runs with no
credentials, and `doctor` is how that claim gets checked on a fresh clone
instead of assumed.
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.table import Table

from praxis import __version__
from praxis.config.models import (
    NON_LLM_AGENTS,
    ModelRole,
    resolve,
    role_for_agent,
    routed_agents,
)
from praxis.config.settings import ProviderName, Settings, get_settings
from praxis.obs.logging import configure_logging

app = typer.Typer(
    name="praxis",
    help=(
        "Organizational memory and calibration engine. Decisions that "
        "invalidate themselves; estimates that learn your bias."
    ),
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


@app.command()
def version() -> None:
    """Print the Praxis version."""
    console.print(__version__)


@app.command(name="config")
def show_config() -> None:
    """Show the resolved configuration and the model routing table.

    Secrets are reported as present or absent and never printed.
    """
    settings = get_settings()

    table = Table(title="Configuration", show_header=True, header_style="bold")
    table.add_column("Setting")
    table.add_column("Value")

    key_state = "set" if settings.anthropic_api_key is not None else "not set"
    for name, value in (
        ("llm_provider", settings.llm_provider.value),
        ("anthropic_api_key", key_state),
        ("offline", str(settings.is_offline)),
        ("data_dir", str(settings.data_dir)),
        ("db_path", str(settings.db_path)),
        ("trace_dir", str(settings.trace_dir)),
        ("seed", str(settings.seed)),
        ("cost_ceiling_usd", f"{settings.cost_ceiling_usd:.2f}"),
        ("log_level", settings.log_level),
        ("log_format", settings.log_format.value),
    ):
        table.add_row(name, value)

    console.print(table)
    console.print(_routing_table())


def _routing_table() -> Table:
    table = Table(title="Model routing", show_header=True, header_style="bold")
    table.add_column("Role")
    table.add_column("Model")
    table.add_column("USD / Mtok in")
    table.add_column("USD / Mtok out")
    table.add_column("Agents")

    for role in ModelRole:
        spec = resolve(role)
        agents = sorted(a for a in routed_agents() if role_for_agent(a) is role)
        table.add_row(
            role.value,
            spec.model_id,
            f"{spec.input_usd_per_mtok}",
            f"{spec.output_usd_per_mtok}",
            "\n".join(agents) or "-",
        )
    return table


@app.command()
def doctor() -> None:
    """Verify that this installation can run with no credentials.

    Exits non-zero if anything would stop a fresh clone from working, so it is
    usable as a smoke test in CI and in a demo script.
    """
    settings = get_settings()
    configure_logging(settings)

    problems: list[str] = []

    if settings.llm_provider is ProviderName.ANTHROPIC:
        problems.append(
            "llm_provider is 'anthropic', so this install needs a network and a "
            "key. Set PRAXIS_LLM_PROVIDER=mock to verify the offline path."
        )

    try:
        settings.ensure_directories()
    except OSError as exc:
        problems.append(f"cannot create {settings.trace_dir}: {exc}")

    # A deterministic agent that also carries a model route is the exact drift
    # NON_LLM_AGENTS exists to prevent, and it would silently make the
    # calibration maths non-reproducible.
    both = sorted(routed_agents() & NON_LLM_AGENTS)
    if both:
        problems.append(
            f"agents declared deterministic but also routed to a model: {', '.join(both)}"
        )

    unpriced = sorted(
        agent for agent in routed_agents() if not resolve(role_for_agent(agent)).model_id
    )
    if unpriced:
        problems.append(f"agents routed to a role with no model: {', '.join(unpriced)}")

    _report(settings, problems)


def _report(settings: Settings, problems: list[str]) -> None:
    if problems:
        console.print("[bold red]praxis doctor: FAIL[/bold red]")
        for problem in problems:
            console.print(f"  - {problem}")
        raise typer.Exit(code=1)

    console.print("[bold green]praxis doctor: OK[/bold green]")
    console.print(f"  provider   {settings.llm_provider.value} (offline={settings.is_offline})")
    console.print(f"  python     {sys.version.split()[0]}")
    console.print(f"  version    {__version__}")
    console.print(f"  data dir   {settings.data_dir}")
