"""The Praxis command line.

`doctor` earns its place early: the project's central claim is that it runs with
no credentials, and `doctor` is how that claim gets checked on a fresh clone
instead of assumed. Phase 1 adds `init` and `store stats`, which are the two
commands that make the store something an owner can see rather than infer.

Every command that touches the store goes through `_opened`, so a store that is
missing, locked or corrupt is a sentence rather than a traceback. The difference
matters here more than it usually does: ADR 0010's failure modes are
environmental, and a stack trace about a sync client tells the person reading it
nothing they can act on.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

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
from praxis.domain.enums import GRAPH_KINDS, RecordKind
from praxis.domain.links import LinkType
from praxis.llm.errors import ProviderError
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.llm.types import LLMRequest, Message, MessageRole, ResponseSchema
from praxis.obs.logging import configure_logging
from praxis.store.errors import StoreError
from praxis.store.location import sync_warning
from praxis.store.reports import StoreStats
from praxis.store.repository import Repository, open_repository

app = typer.Typer(
    name="praxis",
    help=(
        "Organizational memory and calibration engine. Decisions that "
        "invalidate themselves; estimates that learn your bias."
    ),
    no_args_is_help=True,
    add_completion=False,
)

store_app = typer.Typer(
    help="Inspect the record store.",
    no_args_is_help=True,
)
app.add_typer(store_app, name="store")

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


@contextmanager
def _opened(settings: Settings, *, create: bool) -> Iterator[Repository]:
    """Open the store, reporting a failure as a message rather than a traceback.

    Args:
        settings: Where the store lives.
        create: Whether a missing database may be brought into existence. Only
            `init` passes true; a reading command that created one would answer
            a question about a store the owner does not have.

    Yields:
        An open repository, closed when the block ends.
    """
    try:
        repository = open_repository(settings, create=create)
    except StoreError as exc:
        console.print(f"[bold red]praxis: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc
    try:
        yield repository
    finally:
        repository.close()


def _warn_about_sync(settings: Settings) -> None:
    """Print ADR 0010's warning if the store sits under a sync client.

    Warns rather than fails. A deliberate override is legitimate, and a tool
    that refuses to run is a tool that gets worked around.
    """
    warning = sync_warning(settings.data_dir)
    if warning is not None:
        console.print(f"[bold yellow]warning[/bold yellow] {warning}")


@app.command()
def init() -> None:
    """Create the store, or bring an existing one up to the current schema.

    Safe to run repeatedly: migrations are forward-only and skip whatever is
    already applied.
    """
    settings = get_settings()
    configure_logging(settings)
    _warn_about_sync(settings)

    existed = settings.db_path.exists()
    with _opened(settings, create=True) as repository:
        stats = repository.stats()

    console.print("[bold green]praxis init: OK[/bold green]")
    console.print(f"  store      {settings.db_path} ({'existing' if existed else 'created'})")
    console.print(f"  schema     version {stats.schema_version}")
    console.print(f"  records    {sum(stats.records.values())} in {stats.versions} versions")


@store_app.command(name="stats")
def store_stats() -> None:
    """Show what the store holds, by record kind and by edge type."""
    settings = get_settings()
    configure_logging(settings)
    _warn_about_sync(settings)

    with _opened(settings, create=False) as repository:
        stats = repository.stats()

    console.print(_records_table(stats))
    console.print(_links_table(stats))
    console.print(f"schema version {stats.schema_version}")
    # The gap between these two is the store's history: every version that was
    # ever current and is now superseded.
    console.print(f"{stats.versions} versions written, {stats.audit_events} audit events")


def _records_table(stats: StoreStats) -> Table:
    """Current, unretracted records per kind, zeros included."""
    table = Table(title="Records", show_header=True, header_style="bold")
    table.add_column("Kind")
    table.add_column("Current", justify="right")

    for kind in RecordKind:
        if kind in GRAPH_KINDS:
            table.add_row(kind.value, str(stats.records.get(kind, 0)))
    return table


def _links_table(stats: StoreStats) -> Table:
    """Current, unretracted edges per type, zeros included."""
    table = Table(title="Edges", show_header=True, header_style="bold")
    table.add_column("Type")
    table.add_column("Current", justify="right")

    for link_type in LinkType:
        table.add_row(link_type.value, str(stats.links.get(link_type, 0)))
    return table


@app.command()
def doctor() -> None:
    """Verify that this installation can run with no credentials.

    Exits non-zero if anything would stop a fresh clone from working, so it is
    usable as a smoke test in CI and in a demo script. Since Phase 2 that
    includes making one structured model call offline, because a configuration
    that *looks* credential-free and a pipeline that actually answers without a
    key are two different claims.
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

    # The offline claim, checked rather than inferred from configuration.
    offline_call = _probe_offline_call(settings)
    if offline_call.problem is not None:
        problems.append(offline_call.problem)

    # ADR 0010: a warning, never a failure. The default data directory is off
    # the synced tree, but %LOCALAPPDATA% can itself be redirected into OneDrive
    # by an enterprise known-folder policy -- which is the case where the safe
    # default is silently unsafe, and this line is the only thing that says so.
    warnings = [warning for warning in (sync_warning(settings.data_dir),) if warning is not None]

    _report(settings, problems, warnings, offline_call)


@dataclass(frozen=True, slots=True)
class _OfflineCall:
    """What one probe call proved, or why it could not."""

    problem: str | None = None
    tokens: int = 0


def _probe_offline_call(settings: Settings) -> _OfflineCall:
    """Make one structured call through the mock and check the answer.

    `doctor` used to read the configuration and conclude that a credential-free
    run was possible. This makes the call instead, because the two are not the
    same claim: a routing table, a schema walk and a trace write all have to
    work before "runs with no credentials" is true, and none of them is visible
    in a setting.

    The mock is built directly rather than through `provider_for`, so the
    answer does not depend on how *this* machine happens to be configured. The
    question `doctor` is asked is whether a fresh clone works, and on a machine
    set to `replay` the honest answer to that is still about the offline path.
    """
    request = LLMRequest(
        agent="DecisionScout",
        task="doctor_probe",
        system="You find decisions in engineering documents.",
        messages=(
            Message(
                role=MessageRole.USER,
                content="We chose SQLite over Postgres for the graph store.",
            ),
        ),
        schema=ResponseSchema(
            name="DoctorProbe",
            json_schema={
                "type": "object",
                "properties": {"quote": {"type": "string"}},
                "required": ["quote"],
                "additionalProperties": False,
            },
        ),
    )
    try:
        response = MockProvider(sink=MemoryTraceSink(), settings=settings).complete(request)
        answer = json.loads(response.text)
    except (ProviderError, ValueError) as exc:
        return _OfflineCall(problem=f"the offline provider could not answer a call: {exc}")
    if not answer.get("quote"):
        return _OfflineCall(problem="the offline provider answered without filling its schema")
    return _OfflineCall(tokens=response.usage.total)


def _report(
    settings: Settings,
    problems: list[str],
    warnings: list[str],
    offline_call: _OfflineCall,
) -> None:
    for warning in warnings:
        console.print(f"[bold yellow]warning[/bold yellow] {warning}")

    if problems:
        console.print("[bold red]praxis doctor: FAIL[/bold red]")
        for problem in problems:
            console.print(f"  - {problem}")
        raise typer.Exit(code=1)

    console.print("[bold green]praxis doctor: OK[/bold green]")
    console.print(f"  provider   {settings.llm_provider.value} (offline={settings.is_offline})")
    console.print(f"  offline    one structured call answered, {offline_call.tokens} tokens")
    console.print(f"  python     {sys.version.split()[0]}")
    console.print(f"  version    {__version__}")
    console.print(f"  data dir   {settings.data_dir}")
