"""`praxis govern`: what the system refused to tell you, and why.

Its own module for the reason `praxis.cli_fuse` and `praxis.cli_calibrate` are:
`cli.py` is already at the size the style guide calls a file, and it stays the
one place the command surface is enumerable.

**This is where a refusal becomes readable.** Every other Phase 9 component
produces records or recomputes a routing; this is the only place a person sees
the three numbers the phase exists to produce — how often the challenger
conceded, how much of the memory was retired, and how often the system declined
to conclude at all.

**It leads with the abstentions, and does not apologise for them.** The stance
this project has taken on every refusal since `BiasDetective` declined to speak
below `n = 5`: a `NEEDS_HUMAN` is the product working. So the abstention section
comes first, is grouped by which rule fired, and names what would answer each
one — a count of `low_confidence / 4` tells a reader nothing they can act on,
while "the component that filed it was under half confident" tells them where to
look.

**It distinguishes a concession from an abstention, because they look alike and
are not.** A finding the challenger overturned was *decided* — an argument
defeated it. A finding the gate withheld was *not decided* — nobody was willing
to conclude. Printing them together would let a reader take a working challenger
for an uncertain one.

**A concede rate against the mock is not evidence about the challenger**, and
the command says so on the line where the number appears rather than in a
footnote. `praxis.llm.synthesis` answers a bare boolean true seventy per cent of
the time, so an offline concede rate is a fact about schema synthesis. Printing
it without that sentence would be the most misleading line this CLI could emit.

`--dry-run` computes through the same three agents the pass uses, so it cannot
disagree with what a real run would write.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from praxis.agents.abstention import AbstentionGate, Insufficiency, Routing
from praxis.agents.challenger import ChallengerAgent
from praxis.agents.curator import CuratorAgent, Declined, Refusal
from praxis.agents.governance import GovernanceRun, govern_store
from praxis.cli_eval import configured_store
from praxis.config.settings import ProviderName, get_settings
from praxis.domain.records import Finding
from praxis.llm.factory import provider_for
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


def govern(
    dry_run: Annotated[
        bool,
        typer.Option(help="Report what governance would do without writing anything."),
    ] = False,
    idle_days: Annotated[
        int | None,
        typer.Option(help="Retire assumptions unsettled for this many days. Default 60."),
    ] = None,
    quiet: Annotated[
        bool, typer.Option(help="Print the totals only, without naming any finding.")
    ] = False,
) -> None:
    """Argue against every finding, curate the memory, and say what needs a person.

    Three stages. `ChallengerAgent` makes the case against each finding and
    records what survived it. `CuratorAgent` collapses assumptions that say the
    same thing and withdraws ones nothing has ever settled — writing a
    `supersedes` edge and a retraction, never a deletion. `AbstentionGate` then
    routes what is left, and refuses to conclude where the evidence is thin.

    The abstentions are the point rather than a shortfall, and they are printed
    first.

    `--dry-run` writes nothing, so the question can be asked as often as anyone
    likes.
    """
    settings = get_settings()
    configure_logging(settings)

    try:
        with configured_store(settings) as repository:
            run = (
                _asked(repository, idle_days)
                if dry_run
                else govern_store(
                    repository,
                    provider_for(settings),
                    run_id=new_run_id(),
                    idle_days=idle_days,
                )
            )
    except StoreError as exc:
        console.print(f"[bold red]praxis govern: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    _report(run, provider=settings.llm_provider, dry_run=dry_run, quiet=quiet)


def _asked(repository: Repository, idle_days: int | None) -> GovernanceRun:
    """All three stages, computed and not written.

    The same three agents the pass uses, so the answer a `--dry-run` prints is
    the answer a real run would write -- rather than a second, simpler code path
    that could disagree with it.

    Two deliberate departures, both in the direction of saying less rather than
    guessing more.

    **The challenger is not called.** Arguing against a finding costs a
    `reason`-tier call, and a dry run that spent money to say what it would have
    written is not a dry run. So the verdicts reported are the ones already
    standing, which is what a person asking "where does this currently stand"
    wants anyway.

    **The would-be retirements are not fed to the gate as withdrawals.** Found
    by running the command against a seeded store: passing them made the gate
    print *"A-0003 has been retracted"* about a record that was still standing,
    which is a dry run asserting something untrue about the store. The
    retirement is still reported, in the curation section, where it correctly
    reads as something that *would* happen.
    """
    now = datetime.now(UTC)
    standing = repository.list_all(Finding)
    curator = (
        CuratorAgent(repository)
        if idle_days is None
        else CuratorAgent(repository, idle_days=idle_days)
    )
    return GovernanceRun(
        challenge=ChallengerAgent(None).challenge(standing),
        curation=curator.curate(at=now),
        gate=AbstentionGate().route_all(standing),
    )


def _report(run: GovernanceRun, *, provider: ProviderName, dry_run: bool, quiet: bool) -> None:
    """Abstentions first, then the argument, then curation, then what was written."""
    _abstentions(run, quiet=quiet)
    _argument(run, provider=provider)
    _curation(run, quiet=quiet)
    _written(run, dry_run=dry_run)


def _abstentions(run: GovernanceRun, *, quiet: bool) -> None:
    """What the system declined to conclude, grouped by the rule that fired."""
    gate = run.gate
    if not gate.considered:
        console.print(
            "No findings in the store, so there is nothing to govern.\n"
            "Findings are written by [bold]praxis monitor[/bold] and [bold]praxis fuse[/bold], "
            "not here.\n"
            "[dim]Offline this is the expected result even after those have run: the mock "
            "provider quotes text it was never shown, so the citation gate refuses most claims "
            "and few assumptions survive to be breached. It is the gate working, not a "
            "failure here.[/dim]"
        )
        return
    console.print(
        f"[bold]{len(gate.abstained)} of {gate.considered} finding(s) were not concluded[/bold] "
        "— [italic]this is the gate working, not a gap[/italic]"
    )
    counted = gate.by_insufficiency()
    table = Table(show_header=True, title_justify="left")
    table.add_column("withheld because")
    table.add_column("findings", justify="right")
    table.add_column("what would answer it")
    for insufficiency, count in sorted(counted.items(), key=lambda pair: (-pair[1], pair[0].value)):
        if count:
            table.add_row(insufficiency.value, str(count), _ANSWERS[insufficiency])
    console.print(table)
    if not quiet:
        _named(gate.abstained)


def _named(abstained: tuple[Routing, ...]) -> None:
    """The first few, in the gate's own words."""
    for routing in abstained[:MAX_LISTED]:
        console.print(f"  {routing.reason}")
    _elided(len(abstained))


def _argument(run: GovernanceRun, *, provider: ProviderName) -> None:
    """What the challenger did, and whose number the concede rate really is.

    Printed whenever there was anything to argue, including the case where
    nothing was: a dry run makes no call, so its whole argument section is
    zeros — and a reader needs to be told that no verdict was reached rather
    than left to infer it from a missing heading.
    """
    challenge = run.challenge
    if not run.gate.routings:
        return
    decided = len(challenge.decided)
    console.print(
        f"\n[bold]Challenged[/bold] {len(challenge.challenges)}, "
        f"decided {decided}, conceded {len(challenge.conceded)}, "
        f"skipped {challenge.skipped} already argued, {challenge.unjudged} unargued."
    )
    if not decided:
        console.print(
            "  [dim]No verdict was reached, so there is no concede rate — which is not the "
            "same as a concede rate of zero.[/dim]"
        )
        return
    console.print(
        f"  Concede rate: [bold]{challenge.concede_rate:.0%}[/bold] of {decided} decided."
    )
    if provider is ProviderName.MOCK:
        console.print(
            "  [dim]Against the mock this number is a property of schema synthesis rather "
            "than of any reasoning — a bare boolean comes back true seven times in ten. It is "
            "not evidence about the challenger.[/dim]"
        )


def _curation(run: GovernanceRun, *, quiet: bool) -> None:
    """What the memory would stop carrying, and the far longer list of what it keeps."""
    curation = run.curation
    if not curation.considered:
        return
    console.print(
        f"\n[bold]Curated[/bold] {curation.considered} assumption(s): "
        f"{len(curation.merges)} to merge, {len(curation.retirements)} to retire "
        f"({curation.retirement_rate:.0%}), {len(curation.declined)} left alone."
    )
    if quiet:
        return
    for merge in curation.merges[:MAX_LISTED]:
        console.print(f"  {merge.survivor.id} supersedes {merge.superseded.id}: {merge.rationale}")
    _resting(curation.declined)


def _resting(declined: tuple[Declined, ...]) -> None:
    """The refusal worth interrupting someone about, printed apart from the rest.

    An idle assumption a live decision rests on is the one curation refusal that
    is a finding rather than housekeeping: nobody can check the belief and
    something is standing on it. Grouping it with "written too recently" would
    bury it.
    """
    resting = [entry for entry in declined if entry.refusal is Refusal.RESTS_ON_A_DECISION]
    if not resting:
        return
    console.print(
        f"\n[bold]{len(resting)} unverifiable belief(s) with a live decision on top[/bold] "
        "— [italic]kept deliberately; nobody can check these[/italic]"
    )
    for entry in resting[:MAX_LISTED]:
        console.print(f"  {entry.detail}")
    _elided(len(resting))


def _elided(total: int) -> None:
    """Say how many were not named, rather than trailing off."""
    if total > MAX_LISTED:
        console.print(f"  ... and {total - MAX_LISTED} more")


def _written(run: GovernanceRun, *, dry_run: bool) -> None:
    """What reached the store, and the three different ways nothing did.

    "Nothing changed" and "there was nothing to change" are different facts and
    the difference is the whole of whether a reader should worry. Printing the
    first over an empty store -- which is what this did until the command was
    run against the real offline pipeline -- tells a person their governance
    pass is settled when in truth it has never had anything to settle.

    The third case is the one a re-run actually lands on: every finding already
    carries a verdict, so the challenger skipped all of them and made no call.
    That is a settled store, and it is worth saying in those words rather than
    reporting a count of zero unchanged findings.
    """
    if dry_run:
        console.print("\n[dim]--dry-run: nothing was written, and no model was called.[/dim]")
        return
    if run.written == 0 and not run.unchanged and not run.curation.considered:
        console.print(
            "\nNothing written, because there was nothing here to govern — no findings and "
            "no assumptions. Run [bold]praxis extract[/bold] and [bold]praxis monitor[/bold] "
            "first."
        )
        return
    if run.written == 0 and run.challenge.skipped:
        console.print(
            f"\nNothing written. All {run.challenge.skipped} finding(s) already stood and said "
            "the same thing, so no model was called — which is what a second run over an "
            "unchanged store is supposed to report."
        )
        return
    if run.written == 0:
        console.print(
            f"\nNothing written. {run.unchanged} finding(s) already stood and said the same "
            "thing, which is what a second run over an unchanged store is supposed to report."
        )
        return
    console.print(
        f"\nWrote [bold]{len(run.decided)}[/bold] verdict(s), "
        f"[bold]{len(run.merged)}[/bold] supersedes edge(s) and "
        f"[bold]{len(run.retired)}[/bold] retraction(s); {run.unchanged} unchanged. "
        f"{run.calls} model call(s)."
    )


_ANSWERS: dict[Insufficiency, str] = {
    Insufficiency.NEVER_CHALLENGED: "run again with a provider, or decide it yourself",
    Insufficiency.LOW_CONFIDENCE: "the component that filed it was under half confident",
    Insufficiency.UNCITED: "it alleges a document says something and cites no span",
    Insufficiency.SUBJECT_WITHDRAWN: "the record it attacks has been retracted",
}
"""One line per rule, in a person's words rather than the enum's.

A table reading `low_confidence / 4` tells a reader nothing they can act on.
Naming what the number means tells them whether to go and look at the finding or
at the component that filed it, which are different jobs.
"""
