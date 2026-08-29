"""`praxis estimates`: the command that runs Half B over a store.

Its own module rather than more of `praxis/cli.py`, for the reason
`praxis.cli_eval` and `praxis.cli_monitor` are: `cli.py` is already at the size
the style guide calls a file, and it stays the one place the command surface is
enumerable.

One command rather than three, and that is the substance. The three agents are
not independently useful: there is nothing to classify until an estimate exists
and nothing to match until it has been classified, so three commands would be
three ways to run the same sequence in the wrong order. `praxis extract` makes
the same choice about the three agents of Half A.

Two things this reports that a naive summary would not.

**It says what it did not pay for.** A document already read, an estimate
already classified, one this agent has already failed to classify, and one that
already has an outcome are four different skips, and a second run reporting four
counts and zero calls is the correct output rather than a broken one.

**It prints the match rate with its denominator, and the unmatched rows by
cause.** The rate alone is unreadable: two matches out of two and two out of
forty are different facts, and a low rate means four different things depending
on which stage lost the pairing -- only two of which are about the model. An
estimate nothing resolved is not a failure, it is the ordinary case, and the
command says so rather than colouring it red.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from praxis.agents.estimation import EstimationRun, estimate_store
from praxis.agents.matcher import Unmatched
from praxis.cli_eval import configured_store
from praxis.config.settings import Settings, get_settings
from praxis.llm.errors import ProviderError
from praxis.llm.factory import provider_for
from praxis.llm.trace import new_run_id
from praxis.obs.logging import configure_logging
from praxis.store.errors import StoreError
from praxis.store.traces import SqliteTraceSink

console = Console()

MAX_LISTED: int = 5
"""How many examples the command names before it stops naming them.

Spelled here rather than imported from `praxis.cli_monitor`: the two modules
agree on the number by coincidence rather than by dependency, and importing one
constant across two command files to save a line would make a change to either
a change to both.
"""

BENIGN: frozenset[Unmatched] = frozenset({Unmatched.MODEL_FOUND_NONE, Unmatched.NO_CANDIDATES})
"""Causes that are an answer rather than a failure.

Most estimates in a real corpus are never resolved, and a command that printed
every one of them in red would train a reader to ignore the colour. The other
four causes -- an unusable answer, a citation that failed the gate, units that
cannot be reconciled, a record the store refused -- are things somebody might
act on, so those are the ones marked.
"""


def estimates(
    quiet: Annotated[
        bool,
        typer.Option(help="Print the totals only, without naming any example."),
    ] = False,
) -> None:
    """Pull estimates out of the store's documents, classify and resolve them.

    Three agents in the only order that works: `EstimateExtractor` reads each
    document for the quantities it predicts, `WorkClassifier` puts each estimate
    on the axis calibration is computed along, and `OutcomeMatcher` looks for
    what actually happened to it.

    Cheap to run again. An estimate already classified is skipped, one this
    agent has already failed to classify is skipped off the audit trail, and one
    that already has an outcome is skipped, so those two stages cost nothing on
    a second pass.

    A document is skipped only once it has *produced* an estimate. One that
    yielded none is read again, because nothing in an append-only store records
    an attempt that wrote nothing -- the same limitation `praxis extract` has
    about a document holding no decision, and the same reason.

    An estimate nothing resolved still gets a row: an `unresolved` outcome,
    which is what keeps it visible in the calibration data instead of quietly
    dropping out of it.
    """
    settings = get_settings()
    configure_logging(settings)
    run_id = new_run_id()

    try:
        with configured_store(settings) as repository:
            provider = provider_for(
                settings, sink=SqliteTraceSink(repository.connection), run_id=run_id
            )
            run = estimate_store(repository, provider, run_id=run_id)
    except (ProviderError, StoreError) as exc:
        console.print(f"[bold red]praxis estimates: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    _report(run, settings, quiet=quiet)


def _report(run: EstimationRun, settings: Settings, *, quiet: bool) -> None:
    """What one Half B pass extracted, classified, resolved and skipped."""
    console.print("[bold green]praxis estimates: OK[/bold green]")
    console.print(f"  extracted  {run.estimates} estimates from {len(run.documents)} documents")
    console.print(
        f"  classified {len(run.classified)} onto a work class, "
        f"{len(run.classification_refusals)} said nothing usable"
    )
    console.print(
        f"  matched    {len(run.matched)} of {run.outcomes} "
        f"(match rate {run.match_rate}), {len(run.unmatched)} unresolved"
    )
    console.print(
        f"  skipped    {run.already_classified} already classified, "
        f"{run.already_attempted} attempted before, "
        f"{run.already_matched} already answered"
    )
    console.print(f"  model      {run.calls} calls via {settings.llm_provider.value}")
    if run.refused:
        console.print(f"  [yellow]refused    {len(run.refused)} estimates lost to a citation")
    if run.blind_windows:
        console.print(f"  [yellow]blind      {run.blind_windows} windows never answered about")
    if quiet:
        return
    _name_examples(run)


def _name_examples(run: EstimationRun) -> None:
    """Name a few of each, so a number has something behind it.

    Matched estimates are named with the band the arithmetic computed rather
    than with a judgement, because that is what the record holds. Unmatched ones
    are named with the cause and only the actionable causes are marked -- an
    estimate nobody ever wrote an actual for is a correct answer, and colouring
    it as a problem would teach a reader to ignore the colour.
    """
    for found in run.matched[:MAX_LISTED]:
        # The band is printed unbracketed: rich reads `[partial]` as console
        # markup and swallows it, which would drop the one field on this line
        # that is arithmetic rather than prose.
        console.print(
            f"    {found.outcome.id} resolves {found.estimate.id} "
            f"as {found.quality.value} -- {found.estimate.subject}"
        )
    for lost in run.unmatched[:MAX_LISTED]:
        marker = "" if lost.reason in BENIGN else "[yellow]"
        console.print(f"    {marker}{lost.estimate.id} unresolved: {lost.reason.value}")
