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
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from praxis import __version__
from praxis.cli_eval import eval_corpus, extract
from praxis.cli_tables import configuration_table, links_table, records_table, routing_table
from praxis.config.models import (
    NON_LLM_AGENTS,
    resolve,
    role_for_agent,
    routed_agents,
)
from praxis.config.settings import ProviderName, Settings, get_settings
from praxis.corpus.generator import (
    DEFAULT_DOCUMENTS,
    DEFAULT_REVISIONS,
    CorpusError,
    generate_corpus,
)
from praxis.corpus.groundtruth import CorpusGroundTruth
from praxis.domain.links import LinkType
from praxis.ingest.pipeline import IngestionPipeline, IngestionRun
from praxis.llm.errors import ProviderError
from praxis.llm.factory import provider_for
from praxis.llm.mock import MockProvider
from praxis.llm.trace import MemoryTraceSink, new_run_id
from praxis.llm.types import LLMRequest, Message, MessageRole, ResponseSchema
from praxis.obs.logging import configure_logging
from praxis.store.errors import StoreError
from praxis.store.location import sync_warning
from praxis.store.repository import Repository, open_repository
from praxis.store.traces import SqliteTraceSink

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

corpus_app = typer.Typer(
    help="Build the synthetic corpus and its answer key.",
    no_args_is_help=True,
)
app.add_typer(corpus_app, name="corpus")

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

    console.print(configuration_table(settings))
    console.print(routing_table())


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

    console.print(records_table(stats))
    console.print(links_table(stats))
    console.print(f"schema version {stats.schema_version}")
    # The gap between these two is the store's history: every version that was
    # ever current and is now superseded.
    console.print(f"{stats.versions} versions written, {stats.audit_events} audit events")


@app.command()
def ingest(
    paths: Annotated[
        list[Path],
        typer.Argument(help="Files or directories to read. Directories are walked in full."),
    ],
) -> None:
    """Read sources into the store as documents and verified spans.

    The whole ingestion pipeline: normalise, segment, verify, write. Every
    model call is traced into the store, so a run can be accounted for
    afterwards without having been watched.

    Exits non-zero if any source failed or any citation was refused. A refused
    citation should be impossible -- the segmenter answers in block numbers and
    the offsets are read off the grid -- so one appearing is worth a non-zero
    exit rather than a line of output nobody reads.
    """
    settings = get_settings()
    configure_logging(settings)
    _warn_about_sync(settings)

    with _opened(settings, create=False) as repository:
        provider = provider_for(
            settings, sink=SqliteTraceSink(repository.connection), run_id=new_run_id()
        )
        run = IngestionPipeline(repository, provider).ingest_paths(_files_under(paths))

    _report_ingestion(run, settings)


def _files_under(paths: list[Path]) -> list[Path]:
    """Expand directories, sorted, so ids are allocated in the same order twice.

    Document ids are sequential, so the order sources arrive in is the order
    they are numbered in -- and filesystem order is not the same on two
    machines.
    """
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(child for child in path.rglob("*") if child.is_file()))
        else:
            found.append(path)
    return found


def _report_ingestion(run: IngestionRun, settings: Settings) -> None:
    """Print what a run did, and exit non-zero if any of it went wrong."""
    recognised = len(run.ingested) - run.documents_written
    console.print(
        "[bold green]praxis ingest: OK[/bold green]"
        if run.ok
        else "[bold red]praxis ingest: problems[/bold red]"
    )
    console.print(f"  documents  {run.documents_written} written, {recognised} already present")
    console.print(f"  spans      {run.spans_written} written, {run.spans_rejected} refused")
    console.print(f"  model      {run.calls} calls via {settings.llm_provider.value}")
    degraded = sum(1 for result in run.ingested if result.degraded)
    if degraded:
        console.print(f"  [yellow]degraded   {degraded} documents fell back to one span per block")
    for failure in run.failed:
        console.print(f"  [bold red]failed[/bold red]     {failure.source_uri}: {failure.reason}")
    if not run.ok:
        raise typer.Exit(code=1)


# Registered rather than defined here: both live in `praxis.cli_eval`, which
# keeps this file inside the size the style guide asks for, while the command
# surface stays enumerable in one place. `eval` is spelled out as a name
# because the function cannot be -- it is a builtin.
app.command(name="extract")(extract)
app.command(name="eval")(eval_corpus)


@corpus_app.command(name="generate")
def corpus_generate(
    root: Annotated[Path, typer.Argument(help="Directory to write the corpus into.")],
    documents: Annotated[
        int, typer.Option(help="How many documents to write.")
    ] = DEFAULT_DOCUMENTS,
    revisions: Annotated[
        int,
        typer.Option(help="Revision notes overturning an earlier assumption."),
    ] = DEFAULT_REVISIONS,
    seed: Annotated[int | None, typer.Option(help="Overrides PRAXIS_SEED for this corpus.")] = None,
) -> None:
    """Write the synthetic corpus and the answer key Phase 10 grades against.

    Deterministic: the same seed produces the same bytes, so regenerating is
    safe and a corpus in version control has a legible diff. The generated
    corpus verifies against itself before this returns.

    Revision notes are written after the main pass and each overturns an
    assumption an earlier document stated, which is the `contradicts` ground
    truth `ContradictionDetector` is graded against. `--revisions 0` writes the
    Phase 3 corpus and nothing else.
    """
    settings = get_settings()
    configure_logging(settings)

    try:
        truth = generate_corpus(
            root,
            documents=documents,
            revisions=revisions,
            seed=settings.seed if seed is None else seed,
            generated_at=datetime.now(UTC),
        )
    except (CorpusError, ValueError, OSError) as exc:
        console.print(f"[bold red]praxis corpus generate: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    counts = truth.counts()
    distractors = sum(1 for item in truth.items if item.is_distractor)
    console.print("[bold green]praxis corpus generate: OK[/bold green]")
    console.print(f"  corpus     {root}")
    console.print(f"  documents  {len(truth.documents)}, seed {truth.seed}")
    console.print(f"  items      {', '.join(f'{n} {k.value}' for k, n in counts.items())}")
    console.print(f"  negatives  {distractors} distractors")
    console.print(f"  edges      {_contradictions(truth)} contradicts pairs planted")


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


def _contradictions(truth: CorpusGroundTruth) -> int:
    """How many `contradicts` edges the answer key asserts.

    Reported because it is the number that decides whether a contradiction
    metric means anything: before the revision notes existed the corpus had
    none, and a detector finding nothing scored the same as one finding
    everything.
    """
    return sum(
        1 for item in truth.items for link in item.links if link.link_type is LinkType.CONTRADICTS
    )
