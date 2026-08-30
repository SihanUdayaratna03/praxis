"""`praxis calibrate`: what the store's own history says about its estimators.

Its own module for the reason `praxis.cli_estimate` is: `cli.py` is already at
the size the style guide calls a file, and it stays the one place the command
surface is enumerable.

**One command with two modes, and the mode is the object.** With no arguments it
calibrates the *store*: every group is read, graded and any bias worth alleging
is written as a finding. Given an owner, a class and a quantity it calibrates
*that estimate*: what should be written down, and why. Both are the same verb on
different objects, the way `git log` and `git log <path>` are, and splitting them
into two commands would put "calibrate" and "calibrate" in the same help text.

The second mode **writes nothing**. Asking what a correction would be is not the
same as recording that one was made, and a question that quietly mutates a store
is one nobody can ask twice.

Three things this reports that a naive summary would not.

**It leads with the refusals.** Most groups sit below the threshold most of the
time, and against this project's own history every group does. A command that
printed only the measured ones would show an empty table and look broken, when
the correct reading is "four classes, none with enough history yet, and here is
which one is closest".

**It says how many outcomes each refusing group still needs.** "One short" is
the sentence that makes somebody go and close an outcome; "insufficient sample"
is the sentence that makes them close the terminal.

**It prints what it did not write.** A second run over an unchanged store
reports every finding as unchanged and writes nothing, and that is the correct
output rather than a broken one -- the same argument `praxis estimates` makes
about its four kinds of skip.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated

import typer
from rich.console import Console

from praxis.agents.bias import BiasVerdict
from praxis.agents.calibration import CalibrationRun, calibrate_store
from praxis.agents.calibrator import CalibratorAgent
from praxis.cli_eval import configured_store
from praxis.config.settings import get_settings
from praxis.domain.enums import Unit
from praxis.llm.trace import new_run_id
from praxis.obs.logging import configure_logging
from praxis.store.errors import StoreError
from praxis.store.repository import Repository

console = Console()

MAX_LISTED: int = 5
"""How many examples the command names before it stops naming them.

Spelled here rather than imported from `praxis.cli_estimate` or
`praxis.cli_monitor`: the three agree on the number by coincidence rather than
by dependency, and importing one constant across three command files to save two
lines would make a change to any of them a change to all three.
"""

CLOSEST_FIRST: frozenset[BiasVerdict] = frozenset(
    {BiasVerdict.INSUFFICIENT_SAMPLE, BiasVerdict.NO_RESOLVED_OUTCOMES}
)
"""Verdicts worth naming a group for, when nothing was measured.

`UNCLASSIFIED` is left out deliberately: it is reported in its own line with its
own count, because "these estimates are in no group at all" is a different job
from "this group needs one more outcome" and mixing them into one list would
suggest the same fix.
"""


def calibrate(
    owner: Annotated[
        str | None,
        typer.Option(help="Ask about one estimator instead of calibrating the store."),
    ] = None,
    work_class: Annotated[
        str | None,
        typer.Option(help="The kind of work, in the store's own spelling."),
    ] = None,
    quantity: Annotated[
        str | None,
        typer.Option(help="A raw estimate to calibrate. Hands-on effort, not wall clock."),
    ] = None,
    unit: Annotated[Unit, typer.Option(help="What the quantity is measured in.")] = Unit.HOURS,
    quiet: Annotated[
        bool, typer.Option(help="Print the totals only, without naming any group.")
    ] = False,
) -> None:
    """Report what each estimator's history says, or calibrate one new estimate.

    With no arguments this runs over the whole store: every `(owner,
    work_class)` group is read, a factor is computed for the ones with enough
    resolved history, and a bias worth alleging is written as a finding. Running
    it twice over an unchanged store writes nothing the second time.

    Given `--owner`, `--work-class` and `--quantity` it answers the other
    question -- what should this estimate say once the estimator's history is
    taken into account -- and writes nothing at all.

    `BiasDetective` refuses below five resolved estimates in a group, with no
    override, so most groups report no factor. That is the correct answer rather
    than a failure, and it is what this prints first.
    """
    settings = get_settings()
    configure_logging(settings)

    asked = [value for value in (owner, work_class, quantity) if value is not None]
    if asked and len(asked) != 3:  # noqa: PLR2004 -- the three that make one question
        console.print(
            "[bold red]praxis calibrate: --owner, --work-class and --quantity go together."
            "[/bold red]"
        )
        raise typer.Exit(code=1)

    try:
        with configured_store(settings) as repository:
            if owner is not None and work_class is not None and quantity is not None:
                _one_estimate(repository, owner, work_class, quantity, unit)
                return
            run = calibrate_store(repository, run_id=new_run_id())
    except StoreError as exc:
        console.print(f"[bold red]praxis calibrate: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    _report(run, quiet=quiet)


def _one_estimate(
    repository: Repository, owner: str, work_class: str, quantity: str, unit: Unit
) -> None:
    """Answer the question about a single estimate, writing nothing."""
    try:
        raw = Decimal(quantity)
    except InvalidOperation:
        console.print(f"[bold red]praxis calibrate: {quantity!r} is not a quantity[/bold red]")
        raise typer.Exit(code=1) from None
    if raw < 0:
        console.print("[bold red]praxis calibrate: a raw estimate cannot be negative[/bold red]")
        raise typer.Exit(code=1)

    result = CalibratorAgent(repository).calibrate(
        owner=owner, work_class=work_class, quantity=raw, unit=unit
    )
    console.print("[bold green]praxis calibrate: OK[/bold green]")
    console.print(f"  raw        {result.raw} {unit.value}")
    console.print(f"  calibrated {result.corrected} {unit.value}")
    if result.low is not None and result.high is not None:
        console.print(f"  ordinarily {result.low} to {result.high} {unit.value}")
    console.print(f"  basis      n={result.n}, {result.basis.verdict.value}")
    console.print(f"  {result.explanation}")


def _report(run: CalibrationRun, *, quiet: bool) -> None:
    """What one pass over a store found and wrote."""
    console.print("[bold green]praxis calibrate: OK[/bold green]")
    console.print(f"  groups     {len(run.factors)} read, {len(run.measured)} with enough history")
    console.print(
        f"  findings   {len(run.raised)} raised, {len(run.revised)} revised, "
        f"{run.unchanged} already standing"
    )
    grade = run.overall
    if grade.graded:
        console.print(
            f"  backtest   {grade.score} of {grade.scored} corrections landed closer "
            f"(mean log error {grade.raw_error} -> {grade.corrected_error})"
        )
    else:
        console.print(
            f"  backtest   nothing to score: {grade.considered} resolved estimates, "
            f"none in a group that reached the threshold"
        )
    console.print("  model      0 calls -- every component here is arithmetic")
    if quiet:
        return
    _name_examples(run)


def _name_examples(run: CalibrationRun) -> None:
    """Name the measured groups, then the ones closest to being measurable.

    Refusals are named as well as counted, and named with what they still need,
    because the count alone is the number a reader can do nothing with.
    """
    for factor in run.measured[:MAX_LISTED]:
        console.print(f"    {factor.group.owner}: {factor.describe()}")

    waiting = [factor for factor in run.factors if factor.verdict in CLOSEST_FIRST]
    for factor in sorted(waiting, key=lambda item: -item.n)[:MAX_LISTED]:
        console.print(
            f"    [yellow]{factor.group.owner} / {factor.group.work_class}: "
            f"n={factor.n}, {factor.considered} estimates, {factor.resolved} resolved"
        )

    unclassified = [factor for factor in run.factors if factor.verdict is BiasVerdict.UNCLASSIFIED]
    for factor in unclassified[:MAX_LISTED]:
        console.print(
            f"    [yellow]{factor.group.owner}: {factor.considered} estimates carry no "
            f"work class and are in no group"
        )
