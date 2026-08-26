"""The four commands that run Half A's memory: formalize, monitor, contradictions, why.

Their own module rather than more of `praxis/cli.py`, for the reason
`praxis.cli_eval` is: `cli.py` is already at the size the style guide calls a
file, and it stays the one place the command surface is enumerable.

These four are the first commands that operate on what the store *remembers*
rather than on what it read. Three decisions follow from that.

**Each is safe to run again, and says what it did not pay for.** An assumption
whose predicate already parses is not re-compiled, a verdict that has not
changed is not re-written, and a contradiction already asserted is not asserted
twice. Every one of those is answered from state the store already holds, so the
counts a person sees are the counts, not an estimate -- and a second run
reporting all zeros is the correct output rather than a broken one.

**`monitor` takes the facts as a file.** A predicate is evaluated against
measurements, and measurements come from somewhere a person maintains. Typing
them as flags would put invariant 4's problem straight into the shell: a rate
read off a command line is a string, and the only safe thing to do with it is
what `praxis.monitor.facts` already does with a file.

**`why` prints the record's own words and never the model's.** The agent
selects; the store speaks. The `note` explaining *why this decision answers the
question* is the model's and is printed under a heading that says so, because
splicing it into the answer is exactly the failure the agent was built to make
impossible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from praxis.agents.archaeologist import ArchaeologistAgent, Excavation, RestingAssumption
from praxis.agents.detection import DetectionRun, detect_in_store
from praxis.agents.formalization import FormalizationRun, formalize_store
from praxis.cli_eval import configured_store
from praxis.config.settings import Settings, get_settings
from praxis.llm.errors import ProviderError
from praxis.llm.factory import provider_for
from praxis.llm.trace import new_run_id
from praxis.monitor.facts import load_facts
from praxis.monitor.run import MonitoringRun, decisions_resting_on, monitor_store
from praxis.obs.logging import configure_logging
from praxis.store.errors import StoreError
from praxis.store.repository import Repository
from praxis.store.traces import SqliteTraceSink

console = Console()

MAX_LISTED: int = 5
"""How many examples a command lists before it stops naming them.

A run over a real store can breach dozens of assumptions, and a terminal that
scrolls the summary off the top has reported nothing. The count is always
printed; the naming is what is capped.
"""


def formalize(
    force: Annotated[
        bool,
        typer.Option(help="Re-attempt assumptions this agent has already failed to compile."),
    ] = False,
) -> None:
    """Compile the store's assumptions into predicates a machine can check.

    An assumption arrives from the extractor as whatever the document said,
    which is sometimes an expression and usually a paraphrase. This turns each
    into a predicate that parses -- and where it cannot, keeps the best attempt
    and marks it, because the monitor refuses to breach on a predicate it cannot
    read and a person can fix what they can see.

    Costs nothing to run again. One that already parses is skipped, and one this
    agent has already failed at is skipped too, read off the audit trail. Use
    `--force` after a prompt version has changed, which is the one case where
    the same text deserves another attempt.
    """
    settings = get_settings()
    configure_logging(settings)
    run_id = new_run_id()

    try:
        with configured_store(settings) as repository:
            provider = provider_for(
                settings, sink=SqliteTraceSink(repository.connection), run_id=run_id
            )
            run = formalize_store(repository, provider, run_id=run_id, force=force)
    except (ProviderError, StoreError) as exc:
        console.print(f"[bold red]praxis formalize: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    _report_formalization(run, settings)


def monitor(
    facts: Annotated[
        Path | None,
        typer.Option(help="A facts file, or a directory holding facts.json."),
    ] = None,
) -> None:
    """Evaluate every assumption the store holds and record what changed.

    Each predicate is checked against what is measured: the outcomes the store
    itself can assert, and whatever a facts file adds on top. An assumption
    whose predicate evaluates false becomes breached and raises a finding
    against every decision resting on it; one whose expiry condition has fired
    becomes expired. **Those two are never confused** -- a breach is arithmetic
    on facts, and nothing else can produce one.

    Only changes are written, so running this on a schedule costs a version and
    an audit row when something moved and nothing at all when it did not.
    """
    settings = get_settings()
    configure_logging(settings)
    run_id = new_run_id()

    try:
        supplied = None if facts is None else load_facts(facts)
        with configured_store(settings) as repository:
            provider = provider_for(
                settings, sink=SqliteTraceSink(repository.connection), run_id=run_id
            )
            run = monitor_store(
                repository,
                provider=provider,
                supplied=supplied,
                at=datetime.now(UTC),
                run_id=run_id,
            )
            _report_monitoring(run, repository, settings)
    except (ProviderError, StoreError, OSError, ValueError) as exc:
        console.print(f"[bold red]praxis monitor: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc


def contradictions() -> None:
    """Find assumptions and decisions in the store that cannot both hold.

    Three stages and only the last is a model. Blocking proposes the pairs worth
    comparing, interval arithmetic settles every pair whose conflict is a fact
    rather than a judgement, and only what is left is asked about. The stages
    are reported apart because a pair nobody proposed and a pair a model judged
    not to conflict are different results.

    Re-running writes no duplicates: an edge's identity is derived from the two
    records it joins, so the same contradiction found twice is the same edge.
    """
    settings = get_settings()
    configure_logging(settings)
    run_id = new_run_id()

    try:
        with configured_store(settings) as repository:
            provider = provider_for(
                settings, sink=SqliteTraceSink(repository.connection), run_id=run_id
            )
            run = detect_in_store(repository, provider=provider, run_id=run_id)
    except (ProviderError, StoreError) as exc:
        console.print(f"[bold red]praxis contradictions: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    _report_detection(run, settings)


def why(
    question: Annotated[str, typer.Argument(help='A "why not X" question, in your own words.')],
) -> None:
    """Answer a "why not X" question from what the store recorded.

    Retrieval and grounding, not generation. The model is asked only which
    recorded decision and which rejected option the question is about; the
    answer is assembled from the stored reason, the decision's own fields and
    the current status of the assumptions it rested on. An option the decision
    never recorded cannot be answered about, and a question the record does not
    cover is refused rather than guessed at.
    """
    settings = get_settings()
    configure_logging(settings)
    run_id = new_run_id()

    try:
        with configured_store(settings) as repository:
            provider = provider_for(
                settings, sink=SqliteTraceSink(repository.connection), run_id=run_id
            )
            found = ArchaeologistAgent(provider).ask(question, repository)
    except (ProviderError, StoreError) as exc:
        console.print(f"[bold red]praxis why: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    if isinstance(found, Excavation):
        _report_excavation(found)
        return
    console.print(f"[bold yellow]praxis why: {found.detail}[/bold yellow]")
    console.print(f"  refusal    {found.refusal.value}")
    raise typer.Exit(code=1)


def _report_formalization(run: FormalizationRun, settings: Settings) -> None:
    """What one formalization pass compiled, kept and did not pay for twice."""
    console.print("[bold green]praxis formalize: OK[/bold green]")
    console.print(f"  compiled   {len(run.checkable)} now checkable")
    console.print(f"  kept       {len(run.marked)} stored as a best attempt")
    console.print(
        f"  skipped    {run.already_checkable} already parse, "
        f"{run.already_attempted} attempted before"
    )
    console.print(f"  model      {run.calls} calls via {settings.llm_provider.value}")
    if run.refusals:
        console.print(f"  [yellow]refused    {len(run.refusals)} said nothing usable")
    for found in run.marked[:MAX_LISTED]:
        console.print(f"    [yellow]{found.assumption.id} {found.note}")


def _report_monitoring(run: MonitoringRun, repository: Repository, settings: Settings) -> None:
    """What one monitoring pass concluded, and what a breach reached.

    The decisions resting on a breached assumption are named because that is the
    whole point of having written the edge: a breach nobody can trace forward is
    a fact about a predicate rather than about the work.
    """
    console.print("[bold green]praxis monitor: OK[/bold green]")
    console.print(f"  read       {len(run.verdicts)} assumptions")
    console.print(f"  holding    {len(run.holding)}")
    console.print(f"  unchecked  {len(run.unchecked)} nothing has measured")
    console.print(f"  expired    {len(run.aged)} aged out, not violated")
    console.print(f"  breached   {len(run.breached)}")
    console.print(f"  written    {run.revised} new versions, {len(run.findings)} findings")
    console.print(f"  model      {run.calls} calls via {settings.llm_provider.value}")
    for verdict in run.breached[:MAX_LISTED]:
        resting = decisions_resting_on(repository, verdict.assumption.id)
        console.print(f"    [red]{verdict.assumption.id} {verdict.reason}")
        for decision in resting:
            console.print(f"      affects {decision.id} {decision.title}")


def _report_detection(run: DetectionRun, settings: Settings) -> None:
    """What one detection pass proposed, settled and wrote."""
    result = run.result
    console.print("[bold green]praxis contradictions: OK[/bold green]")
    console.print(f"  read       {run.records} records")
    console.print(f"  proposed   {len(result.blocking.candidates)} pairs worth comparing")
    console.print(
        f"  settled    {len(result.by_arithmetic)} by arithmetic, "
        f"{len(result.by_model)} by judgement"
    )
    console.print(f"  written    {len(run.written)} new edges")
    console.print(f"  model      {result.calls} calls via {settings.llm_provider.value}")
    if result.blocking.skipped:
        console.print(
            f"  [yellow]skipped    {len(result.blocking.skipped)} keys too common to discriminate"
        )
    for found in run.contradictions[:MAX_LISTED]:
        left, right = found.pair
        console.print(f"    [red]{left} <> {right} ({found.settled_by.value}) {found.rationale}")


def _report_excavation(found: Excavation) -> None:
    """Print an answer assembled from records, and the model's note apart from it.

    The separation is the agent's whole contract, so it is also the rendering's:
    everything under "answer" is a stored field, and the one line the model
    wrote is under a heading naming it as such.
    """
    console.print("[bold green]praxis why: answered from the record[/bold green]")
    console.print(f"  decision   {found.decision.id} {found.decision.title}")
    console.print(f"  rejected   {found.rejected.option}")
    console.print("")
    console.print(found.answer, soft_wrap=True, markup=False, highlight=False)
    console.print("")
    for resting in found.resting_on:
        console.print(f"  {_status_marker(resting)} {resting.assumption.id} {resting.status.value}")
    console.print(f"  cited      {', '.join(found.citations)}")
    if found.note:
        console.print(f"  [dim]selection note (the model's words, not the record's): {found.note}")


def _status_marker(resting: RestingAssumption) -> str:
    """A colour for an assumption's standing, so a broken one is visible at a glance."""
    return "[green]holds  [/green]" if resting.still_holds else "[yellow]check  [/yellow]"
