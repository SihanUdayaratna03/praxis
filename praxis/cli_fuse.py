"""`praxis fuse`: where the two halves argue, made readable by a person.

Its own module for the reason `praxis.cli_calibrate` is: `cli.py` is already at
the size the style guide calls a file, and it stays the one place the command
surface is enumerable.

**This is the demo surface.** Everything else in Phase 8 produces records; this
is where the sentence `ARCHITECTURE.md` promises actually gets printed, and it
is the only place a judge will see it. So the output is arranged around the two
things a reader needs to distinguish and would otherwise merge:

**A projection is printed differently from a measurement.** A calibrated flip
says *"nothing has been measured yet"* in the header of its own section, because
a reader who takes it for an observed breach will over-react to it. ADR 0028
made them different `FindingKind`s so a queue cannot rank one above the other;
this makes them look different so a person cannot misread one as the other.

**It leads with what it refused to say.** Against this project's own corpus, and
against any small one, nearly every priced edge lands on `no_factor` -- the
group is below `BiasDetective`'s threshold of five. A command that printed only
the flips would show an empty screen and look broken, when the correct reading
is "eleven edges priced, none with enough history behind them yet, and here is
how many outcomes the closest one still needs". The refusal vocabulary is the
output most of the time and it is not an error path.

**It prints what it did not write.** A second run over an unchanged store
reports every finding as unchanged and writes nothing. That is the correct
output rather than a broken one -- the same argument `praxis estimates` makes
about its four kinds of skip and `praxis calibrate` makes about its refusals.

`--dry-run` answers the question without recording that it was asked. Reading
what the fusion layer currently alleges is something a person should be able to
do repeatedly without every look adding a version to the store.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from praxis.agents.collateral import CollateralAgent, CollateralDamage
from praxis.agents.fusion import FusionBridge, FusionVerdict, PricedAssumption
from praxis.agents.fusion_pass import FusionRun, fuse_store
from praxis.cli_eval import configured_store
from praxis.config.settings import get_settings
from praxis.llm.trace import new_run_id
from praxis.obs.logging import configure_logging
from praxis.store.errors import StoreError
from praxis.store.repository import Repository

console = Console()

MAX_LISTED: int = 5
"""How many examples the command names before it stops naming them.

Spelled here rather than imported from the other command modules: they agree on
the number by coincidence rather than by dependency, and sharing one constant
would make a change to any of them a change to all.
"""


def fuse(
    dry_run: Annotated[
        bool,
        typer.Option(help="Report what the fusion layer alleges without writing anything."),
    ] = False,
    quiet: Annotated[
        bool, typer.Option(help="Print the totals only, without naming any finding.")
    ] = False,
) -> None:
    """Ask what this team's track record says about the decisions it has made.

    Runs both directions over the store. Forwards: every `estimated_as` edge is
    priced against its estimator's calibration history, and where the calibrated
    number violates a predicate the raw estimate satisfied, that flip is filed
    as a stale-decision finding. Backwards: every estimate an outcome shows to
    have missed is walked back to the decisions that leaned on it.

    Most priced edges report no factor, because `BiasDetective` refuses below
    five resolved estimates in a group with no override. That is the correct
    answer rather than a failure, and it is what this prints first.

    `--dry-run` writes nothing, so the question can be asked as often as anyone
    likes.
    """
    settings = get_settings()
    configure_logging(settings)

    try:
        with configured_store(settings) as repository:
            run = _asked(repository) if dry_run else fuse_store(repository, run_id=new_run_id())
    except StoreError as exc:
        console.print(f"[bold red]praxis fuse: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    _report(run, dry_run=dry_run, quiet=quiet)


def _asked(repository: Repository) -> FusionRun:
    """Both directions, computed and not written.

    The same two agents the pass uses, so the answer a `--dry-run` prints is the
    answer a real run would write -- rather than a second, simpler code path
    that could disagree with it.
    """
    now = datetime.now(UTC)
    return FusionRun(
        priced=FusionBridge(repository).price_all(now=now),
        damages=CollateralAgent(repository).survey(),
    )


def _report(run: FusionRun, *, dry_run: bool, quiet: bool) -> None:
    """Print the refusals, then the two kinds of finding, then what was written."""
    _refusals(run.priced)
    if not quiet:
        _flips(run.flips)
        _collateral(run.damaging)
    _written(run, dry_run=dry_run)


def _refusals(priced: tuple[PricedAssumption, ...]) -> None:
    """What the forward direction could not say, and why. Printed first."""
    if not priced:
        console.print(
            "No [bold]estimated_as[/bold] edges in the store, so there is nothing to price.\n"
            "The edge is written by [bold]praxis extract[/bold], not here — it records that an "
            "assumption turned out to be a quantified forward-looking claim (ADR 0016).\n"
            "[dim]Offline this is the expected result even after extract has run: the mock "
            "provider quotes text it was never shown, so the citation gate refuses most claims "
            "and few assumptions survive to carry an edge. It is the gate working, not a "
            "failure here.[/dim]"
        )
        return
    counted = Counter(result.verdict for result in priced)
    table = Table(title=f"{len(priced)} estimated_as edge(s) priced", title_justify="left")
    table.add_column("verdict")
    table.add_column("edges", justify="right")
    table.add_column("meaning")
    for verdict, count in sorted(counted.items(), key=lambda pair: (-pair[1], pair[0].value)):
        table.add_row(verdict.value, str(count), _MEANINGS[verdict])
    console.print(table)


def _flips(flips: tuple[PricedAssumption, ...]) -> None:
    """The forward findings, labelled as projections in the heading itself."""
    if not flips:
        return
    console.print(
        f"\n[bold]{len(flips)} decision(s) may rest on a mis-estimate[/bold] "
        "— [italic]nothing has been measured yet; this is what the history implies[/italic]"
    )
    for result in flips[:MAX_LISTED]:
        console.print(f"  {result.describe()}")
    _elided(len(flips))


def _collateral(damaging: tuple[CollateralDamage, ...]) -> None:
    """The backward findings. These are measurements and are labelled as such."""
    if not damaging:
        return
    console.print(
        f"\n[bold]{len(damaging)} estimate(s) missed and something rested on them[/bold] "
        "— [italic]this already happened[/italic]"
    )
    for damage in damaging[:MAX_LISTED]:
        console.print(f"  {damage.describe()}")
    _elided(len(damaging))


def _elided(total: int) -> None:
    """Say how many were not named, rather than trailing off."""
    if total > MAX_LISTED:
        console.print(f"  ... and {total - MAX_LISTED} more")


def _written(run: FusionRun, *, dry_run: bool) -> None:
    """What reached the store, including the case where nothing did."""
    if dry_run:
        console.print("\n[dim]--dry-run: nothing was written.[/dim]")
        return
    if run.written == 0:
        console.print(
            f"\nNothing written. {run.unchanged} finding(s) already stood and said the same "
            "thing, which is what a second run over an unchanged store is supposed to report."
        )
        return
    console.print(
        f"\nWrote [bold]{len(run.raised)}[/bold] new finding(s) and revised "
        f"[bold]{len(run.revised)}[/bold]; {run.unchanged} unchanged."
    )


_MEANINGS: dict[FusionVerdict, str] = {
    FusionVerdict.FLIPPED: "calibration turns a satisfied predicate into a violated one",
    FusionVerdict.RELIEVED: "calibration excuses a predicate the raw estimate violated",
    FusionVerdict.UNCHANGED: "a factor applied and the verdict did not move",
    FusionVerdict.UNDECIDED: "the predicate settles nothing either way",
    FusionVerdict.NO_FACTOR: "fewer than five resolved estimates in the group; no override",
    FusionVerdict.UNCLASSIFIED_WORK: "the estimate has no work class, so it forms no group",
    FusionVerdict.NO_SUBJECT: "the predicate names no single quantity to correct",
    FusionVerdict.MISSING_ESTIMATE: "the edge points at an estimate no longer standing",
}
"""One line per verdict, in a person's words rather than the enum's.

A table of `no_factor / 11` tells a reader nothing they can act on. "Fewer than
five resolved estimates in the group" tells them to go and close an outcome,
which is the only thing that would change the answer.
"""
