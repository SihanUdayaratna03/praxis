"""`praxis demo seed`: fill a store with Praxis's own history.

Its own module for the reason `praxis.cli_fuse` is -- `cli.py` is already the
size the style guide calls a file.

Run from a checkout, because the corpus is this repository: `docs/adr/` and
`docs/dogfood/`. There is no second hand-made dataset, which is the point. The
numbers a demo shows are numbers somebody actually wrote down.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from praxis.config.settings import get_settings
from praxis.demo.seed import SeedReport, seed
from praxis.domain.enums import RecordKind
from praxis.monitor.facts import load_facts
from praxis.monitor.run import monitor_store
from praxis.obs.logging import configure_logging
from praxis.store.errors import StoreError
from praxis.store.repository import Repository, open_repository

console = Console()

DEFAULT_ADRS = Path("docs/adr")
DEFAULT_DOGFOOD = Path("docs/dogfood")
FACTS_NAME = "facts.json"


def demo_seed(
    adrs: Annotated[Path, typer.Option(help="Where the ADRs are.")] = DEFAULT_ADRS,
    dogfood: Annotated[
        Path, typer.Option(help="Where estimates.jsonl and outcomes.jsonl are.")
    ] = DEFAULT_DOGFOOD,
    monitor: Annotated[
        bool, typer.Option(help="Evaluate the seeded predicates against docs/dogfood/facts.json.")
    ] = True,
) -> None:
    """Seed the store from this repository's ADRs and dogfood corpus.

    Run it from a checkout. Refuses a store that already holds records — the
    store is append-only, so point `PRAXIS_DATA_DIR` somewhere empty to redo it.
    """
    settings = get_settings()
    configure_logging(settings)
    for directory in (adrs, dogfood):
        if not directory.is_dir():
            console.print(f"[bold red]praxis: {directory} is not a directory[/bold red]")
            console.print("  the demo seeds from this repository, so run it from a checkout")
            raise typer.Exit(code=1)

    try:
        repository = open_repository(settings, create=True)
    except StoreError as exc:
        console.print(f"[bold red]praxis: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    try:
        _refuse_a_populated_store(repository)
        report = seed(repository, adr_dir=adrs, dogfood_dir=dogfood)
        _report(report)
        if monitor:
            _monitor(repository, dogfood)
    finally:
        repository.close()

    console.print("\n[dim]next:[/dim] praxis serve   [dim]# the dashboard[/dim]")
    console.print(
        "[dim]     [/dim] praxis fuse    [dim]# price the assumptions that are estimates[/dim]"
    )


def _refuse_a_populated_store(repository: Repository) -> None:
    """Stop before writing a second copy of everything."""
    held = repository.stats().records.get(RecordKind.DECISION, 0)
    if held:
        console.print(f"[bold red]praxis: the store already holds {held} decision(s)[/bold red]")
        console.print("  the store is append-only, so seeding again would duplicate them")
        console.print("  point PRAXIS_DATA_DIR at an empty directory and run praxis init")
        raise typer.Exit(code=1)


def _report(report: SeedReport) -> None:
    """What was written, by kind."""
    table = Table(title="Seeded from this repository", title_justify="left")
    table.add_column("kind")
    table.add_column("written", justify="right")
    for label, count in (
        ("documents", report.documents),
        ("spans", report.spans),
        ("decisions", report.decisions),
        ("assumptions", report.assumptions),
        ("estimates", report.estimates),
        ("outcomes", report.outcomes),
        ("links", report.links),
    ):
        table.add_row(label, str(count))
    console.print(table)
    if report.unresolved:
        # An open estimate is the honest state of a phase still being worked on.
        console.print(
            f"[dim]{report.unresolved} estimate(s) have no outcome yet — "
            f"a phase in flight, not a gap[/dim]"
        )


def _monitor(repository: Repository, dogfood: Path) -> None:
    """Evaluate the seeded predicates against the measurements on file."""
    facts_path = dogfood / FACTS_NAME
    if not facts_path.is_file():
        console.print(f"[dim]no {facts_path}, so nothing was evaluated[/dim]")
        return
    run = monitor_store(repository, supplied=load_facts(facts_path))
    console.print(
        f"\n[bold]{len(run.verdicts)} predicate(s) evaluated[/bold] against "
        f"{facts_path} — [bold red]{len(run.breached)} breached[/bold red]"
    )
    for finding in run.findings:
        console.print(f"  {finding.prosecution}")
