"""CuratorAgent: what the memory should stop carrying, and how it stops carrying it.

An append-only store accumulates. Two agents reading the same revised topic
write two assumptions; an assumption nothing can ever settle sits `UNVERIFIED`
forever and is re-evaluated on every monitoring pass. Neither is a bug in the
agent that wrote it, and both make the store worse to read. This is the
component that says so.

**Nothing is deleted, and nothing new was invented to avoid deleting.** Both
mechanisms already existed and both were built for exactly this:

- **Merging is `LinkType.SUPERSEDES`**, which Phase 1 defined as "a newer record
  replaces an older one of the same kind", pointing newer to older, matching the
  ADR convention already in the corpus. `endpoints_are_valid` additionally
  requires the two kinds to match for this type alone, so an assumption can only
  supersede an assumption.
- **Retiring is `Repository.retract`**, whose own docstring states the property
  the job needs: *"Not a delete. The withdrawn versions stay readable, so a
  retraction is a statement about a record rather than the disappearance of
  one."* It writes a new version carrying a flag plus an `AuditAction.RETRACTED`
  row, which is a status change on a new version and not a removal.

So this phase costs no migration, and `AssumptionStatus` gains no member. See
ADR 0031.

**It makes no model call.** Everything it decides is already a record: a
`CONTRADICTS` edge `ContradictionDetector` wrote in Phase 5, a predicate
`praxis.predicates` parses, `created_at` ordering the store keeps, and an audit
trail Phase 5's monitor writes. "Are these two claims about the same thing" *is*
judgement -- and it is `ContradictionDetector`'s judgement, paid for once,
recorded as an edge, in exactly the pattern ADR 0026 describes for the fusion
bridge. Eighth consecutive component on the arithmetic side.

## What "never fires" means, precisely

An assumption is **idle** when all three hold:

1. its status is still `UNVERIFIED` -- nothing has ever settled it;
2. its audit trail carries no event written by `AssumptionMonitor`; and
3. it was created at least `IDLE_DAYS` before the moment being asked about.

Queried through `Repository.audit_for`, which Phase 5 already exposes. No
parallel bookkeeping, no "times seen" counter, no second table.

**The known limitation is stated rather than hidden.** `praxis.monitor.run`
writes only on a change, so a monitoring pass that decided nothing leaves no
audit row -- which means this cannot distinguish "monitored repeatedly and never
settleable" from "never monitored at all". The reason that does not matter is
that `UNVERIFIED` means *nothing has ever settled it* in both cases, and an
assumption nothing can settle is dead weight whichever way it got there. ADR
0031 assumption 3 expires the day that stops being true.

## The refusal that matters most

**An idle assumption a live decision rests on is not retired.** It is an
unverifiable belief underneath a live decision, which is a finding for a person
rather than dead weight for a curator. Retiring it would quietly remove the
thing that makes the decision worth re-reading, and the blast radius of that is
the whole product's claim. Every refusal is reported rather than dropped, for
the reason every refusal in this system is: which beliefs nobody can check is a
fact somebody can act on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from praxis.domain.enums import AssumptionStatus
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Link
from praxis.obs.logging import get_logger
from praxis.predicates.errors import PredicateSyntaxError
from praxis.predicates.intervals import constraints_of
from praxis.predicates.parser import parse
from praxis.store.repository import Repository

_log = get_logger(__name__)

CURATOR_NAME: Final = "CuratorAgent"
"""Spelled as `praxis.config.models.NON_LLM_AGENTS` spells it, so the set that
refuses to route this agent and the agent itself cannot drift apart."""

MONITOR_ACTOR: Final = "AssumptionMonitor"
"""Spelled as `praxis.monitor.run.MONITOR_ACTOR` spells it.

Imported by value rather than by reference to avoid an import cycle -- the
monitor imports the store and this imports the store, but a curator importing
the monitor would tie a governance pass to a monitoring one. `tests/agents/
test_curator.py` asserts the two spellings agree, so drift is a failing test
rather than a silent one.
"""

IDLE_DAYS: Final = 60
"""How long an assumption must have gone unsettled before it counts as idle.

Two months, and it is a judgement rather than a derivation -- the same honesty
ADR 0024 applies to `MINIMUM_SAMPLE`. It is long enough that an assumption
written this quarter is never swept up, and short enough that a corpus a year
old is genuinely curated. There is no data to fit it to: this project has one
corpus and it was generated. Reported beside every retirement so a reader who
disagrees can re-derive without re-running anything.
"""


class MergeReason(StrEnum):
    """Why two assumptions were collapsed into one."""

    DUPLICATE = "duplicate"
    """The two predicates constrain the same quantity in the same way. Nobody
    revised anything; two agents read the same claim twice."""

    REVISION = "revision"
    """A `CONTRADICTS` edge joins them and the later one was written in a
    different document. The organisation changed its mind, and the newer claim
    is the one that stands."""


@dataclass(frozen=True, slots=True)
class Merge:
    """One pair of assumptions the curator would collapse.

    Attributes:
        survivor: The assumption that stands. Always the later of the two.
        superseded: The assumption it replaces.
        reason: Which rule fired.
        rationale: What the `supersedes` edge will say, in a reader's words.
    """

    survivor: Assumption
    superseded: Assumption
    reason: MergeReason
    rationale: str


class Refusal(StrEnum):
    """Why the curator declined to touch a record it had looked at."""

    RESTS_ON_A_DECISION = "rests_on_a_decision"
    """Idle, and a live decision assumes it. A finding, not dead weight."""

    NOT_YET_IDLE = "not_yet_idle"
    """Unverified, but written too recently to call abandoned."""

    ALREADY_EVALUATED = "already_evaluated"
    """The monitor has settled it at least once, so it is memory that works."""

    ALREADY_SUPERSEDED = "already_superseded"
    """A `supersedes` edge between the pair already stands. Idempotence."""

    SAME_DOCUMENT = "same_document"
    """A contradiction inside one document is not a revision of anything."""


@dataclass(frozen=True, slots=True)
class Retirement:
    """One idle assumption the curator would withdraw.

    Attributes:
        assumption: What would be retracted.
        idle_days: How long it has gone unsettled, so the threshold is visible
            beside the decision rather than only in this module.
    """

    assumption: Assumption
    idle_days: int


@dataclass(frozen=True, slots=True)
class Declined:
    """One record the curator looked at and left alone.

    Reported rather than dropped. "Which of my beliefs can nobody check, and
    which decisions rest on them" is the question a governance layer exists to
    answer, and a result listing only its writes could not answer it.

    Attributes:
        assumption_id: What was considered.
        refusal: Why it was left alone.
        detail: The specifics, in a reader's words.
    """

    assumption_id: str
    refusal: Refusal
    detail: str


@dataclass(frozen=True, slots=True)
class CurationResult:
    """Everything one curation pass concluded, writes and refusals alike.

    Attributes:
        merges: Pairs to collapse, in a fixed order.
        retirements: Idle assumptions to withdraw.
        declined: What was looked at and left alone, and why.
        considered: How many live assumptions were read.
    """

    merges: tuple[Merge, ...] = ()
    retirements: tuple[Retirement, ...] = ()
    declined: tuple[Declined, ...] = ()
    considered: int = 0

    @property
    def retirement_rate(self) -> float:
        """Share of live assumptions this pass would retire.

        Zero when nothing was considered, which is no measurement rather than a
        low rate -- `praxis.eval.governance` reports the denominator beside it
        for the same reason the concede rate carries one.
        """
        return len(self.retirements) / self.considered if self.considered else 0.0


class CuratorAgent:
    """Collapses duplicated assumptions and withdraws abandoned ones."""

    name: Final = CURATOR_NAME

    def __init__(self, repository: Repository, *, idle_days: int = IDLE_DAYS) -> None:
        """Wire the curator to the store it reads.

        Args:
            repository: An open, migrated store.
            idle_days: How long an assumption must go unsettled to count as
                idle.

        Raises:
            ValueError: if the idle window is negative, which would retire
                assumptions written in the future.
        """
        if idle_days < 0:
            message = f"an idle window is a number of days, got {idle_days}"
            raise ValueError(message)
        self._repository = repository
        self._idle_days = idle_days

    def curate(self, *, at: datetime) -> CurationResult:
        """Read every live assumption and decide what should stop standing.

        Args:
            at: The moment being asked about. Timezone-aware, invariant 5 --
                the idle window is a comparison against wall-clock time.

        Returns:
            What to merge, what to retire, and what was left alone.
        """
        live = self._repository.list_all(Assumption)
        merges, merge_refusals = self._merges(live, at=at)
        # Anything about to be superseded is not also a retirement candidate:
        # one record cannot be both replaced and abandoned, and reporting it as
        # both would double-count it in two different rates.
        merged = {merge.superseded.id for merge in merges}
        retirements, retire_refusals = self._retirements(
            [record for record in live if record.id not in merged], at=at
        )

        _log.info(
            "curation_pass",
            agent=self.name,
            considered=len(live),
            merges=len(merges),
            retirements=len(retirements),
            declined=len(merge_refusals) + len(retire_refusals),
        )
        return CurationResult(
            merges=tuple(merges),
            retirements=tuple(retirements),
            declined=(*merge_refusals, *retire_refusals),
            considered=len(live),
        )

    def _merges(
        self, live: Sequence[Assumption], *, at: datetime
    ) -> tuple[list[Merge], list[Declined]]:
        """Every pair to collapse, and the pairs refused.

        Ordered by the pair's ids so two passes over one store consider the
        same pairs in the same sequence.
        """
        by_id: dict[str, Assumption] = {record.id: record for record in live}
        merges: list[Merge] = []
        declined: list[Declined] = []
        for pair in sorted(self._pairs(live)):
            left, right = by_id[pair[0]], by_id[pair[1]]
            outcome = self._merge_of(left, right, at=at)
            if isinstance(outcome, Merge):
                merges.append(outcome)
            elif outcome is not None:
                declined.append(outcome)
        return merges, declined

    def _pairs(self, live: Sequence[Assumption]) -> set[tuple[str, str]]:
        """Candidate pairs, from the edges the store already holds and from predicates.

        Two sources, and neither is a search over every pair: a `CONTRADICTS`
        edge is a judgement Phase 5 already made and paid for, and a duplicate
        is settled by comparing parsed constraints, which is arithmetic.
        """
        pairs: set[tuple[str, str]] = set()
        ids = {record.id for record in live}
        for record in live:
            for link in self._repository.links_touching(record.id, types=(LinkType.CONTRADICTS,)):
                other = link.target_id if link.source_id == record.id else link.source_id
                if other in ids and other != record.id:
                    pairs.add(_ordered(record.id, other))
        by_shape: dict[tuple[str, ...], list[str]] = {}
        for record in live:
            shape = _shape_of(record.predicate)
            if shape is not None:
                by_shape.setdefault(shape, []).append(record.id)
        for group in by_shape.values():
            pairs.update(
                _ordered(one, other)
                for index, one in enumerate(group)
                for other in group[index + 1 :]
            )
        return pairs

    def _merge_of(
        self, left: Assumption, right: Assumption, *, at: datetime
    ) -> Merge | Declined | None:
        """What to do about one candidate pair.

        Returns:
            The merge, a refusal worth reporting, or `None` when the pair is
            simply not a merge -- a contradiction between two live claims
            somebody should look at is `ContradictionDetector`'s output and not
            this agent's business.
        """
        if self._already_superseded(left, right):
            return Declined(
                left.id,
                Refusal.ALREADY_SUPERSEDED,
                f"{left.id} and {right.id} already carry a supersedes edge",
            )
        survivor, superseded = _by_age(left, right)
        shape = _shape_of(left.predicate)
        if shape is not None and shape == _shape_of(right.predicate):
            return Merge(
                survivor,
                superseded,
                MergeReason.DUPLICATE,
                (
                    f"{survivor.id} and {superseded.id} constrain the same quantity in the "
                    f"same way (`{survivor.predicate}`); the later reading stands"
                ),
            )
        if _same_document(self._repository, left, right):
            return Declined(
                left.id,
                Refusal.SAME_DOCUMENT,
                (
                    f"{left.id} and {right.id} were read from one document, so the later "
                    f"is not a revision of the earlier"
                ),
            )
        # A revision needs one claim to have come *after* the other. Two
        # conflicting assumptions written at the same moment are two live
        # claims somebody should look at, which is `ContradictionDetector`'s
        # output and not this agent's business -- and a claim dated after the
        # moment being asked about has not been written yet as far as this pass
        # is concerned. Neither is reported as a refusal, because neither is a
        # candidate the curator declined; they were never candidates.
        if survivor.created_at == superseded.created_at or survivor.created_at > at:
            return None
        return Merge(
            survivor,
            superseded,
            MergeReason.REVISION,
            (
                f'{survivor.id} states "{survivor.statement}", which cannot hold alongside '
                f"{superseded.id}, and was written later in a different document"
            ),
        )

    def _already_superseded(self, left: Assumption, right: Assumption) -> bool:
        """Whether a `supersedes` edge between this pair already stands.

        Checked in both directions. Re-running a curation pass over a store it
        has already curated must propose nothing, and the edge is the record of
        the previous pass having run.
        """
        return any(
            link.link_type is LinkType.SUPERSEDES
            and {link.source_id, link.target_id} == {left.id, right.id}
            for link in self._repository.links_touching(left.id)
        )

    def _retirements(
        self, live: Sequence[Assumption], *, at: datetime
    ) -> tuple[list[Retirement], list[Declined]]:
        """Every idle assumption to withdraw, and every one refused."""
        retirements: list[Retirement] = []
        declined: list[Declined] = []
        for record in live:
            outcome = self._retirement_of(record, at=at)
            if isinstance(outcome, Retirement):
                retirements.append(outcome)
            elif outcome is not None:
                declined.append(outcome)
        return retirements, declined

    def _retirement_of(self, record: Assumption, *, at: datetime) -> Retirement | Declined | None:
        """What to do about one assumption, or `None` when it is simply live.

        The three idleness conditions in order, and the order is the argument:
        an assumption the monitor has settled is working memory and is never
        looked at further, one written this month is too young to call
        abandoned, and only then does the decision-resting check run -- which is
        a refusal rather than a skip, because it is the interesting one.
        """
        first_written, monitored = self._trail_of(record)
        if record.status is not AssumptionStatus.UNVERIFIED:
            return Declined(
                record.id,
                Refusal.ALREADY_EVALUATED,
                f"{record.id} is {record.status.value}; the monitor settles it",
            )
        if monitored:
            return None
        idle = (at - first_written).days
        if idle < self._idle_days:
            return Declined(
                record.id,
                Refusal.NOT_YET_IDLE,
                (
                    f"{record.id} has gone {idle} days unsettled, short of the "
                    f"{self._idle_days} this retires at"
                ),
            )
        resting = _resting_on(self._repository, record)
        if resting:
            return Declined(
                record.id,
                Refusal.RESTS_ON_A_DECISION,
                (
                    f"{record.id} has gone {idle} days unsettled and "
                    f"{', '.join(decision.id for decision in resting)} still rests on it. "
                    f"An unverifiable belief under a live decision is a finding, not dead weight"
                ),
            )
        return Retirement(record, idle_days=idle)

    def _trail_of(self, record: Assumption) -> tuple[datetime, bool]:
        """When this assumption was first written, and whether the monitor ever spoke.

        One pass over Phase 5's audit trail answers both, and there is no second
        bookkeeping anywhere.

        **The clock starts at the first version, not the current one.** A
        revision overwrites `created_at` -- `Repository.revise` says so, because
        a new version carrying the old timestamp would be claiming to have been
        written at a time it was not. Measuring idleness off the current version
        would therefore let any unrelated write reset the window, so an
        assumption a formalizer keeps rewriting would never be idle no matter how
        long nothing settled it. The trail is the only place the original
        timestamp survives.

        What the trail cannot see is a monitoring pass that decided nothing,
        because `praxis.monitor.run` writes only on a change -- see the module
        docstring and ADR 0031 assumption 3.

        Returns:
            The first write's timestamp, falling back to the record's own when
            the trail is empty, and whether any event names the monitor.
        """
        events = self._repository.audit_for(record.id)
        first = events[0].occurred_at if events else record.created_at
        return first, any(event.actor == MONITOR_ACTOR for event in events)


def supersedes_link(merge: Merge, *, at: datetime) -> Link:
    """The edge that records one assumption replacing another.

    Points survivor to superseded, which is the direction `LinkType.SUPERSEDES`
    documents and the direction the ADR corpus already uses. Free rather than a
    method so a caller can see the edge a merge implies without holding a store.

    Args:
        merge: The pair to collapse.
        at: When this was decided. Timezone-aware, invariant 5.

    Returns:
        The `supersedes` edge.
    """
    return Link.between(
        LinkType.SUPERSEDES,
        merge.survivor.id,
        merge.superseded.id,
        rationale=merge.rationale,
        # Certain by construction for a duplicate -- two parsed predicates
        # either constrain the same quantity the same way or they do not, and
        # two runs agree about that. A revision inherits the confidence of the
        # `contradicts` edge underneath it, which is the judgement being relied
        # on, so it is the survivor's own confidence that travels rather than a
        # number this agent invented.
        confidence=1.0 if merge.reason is MergeReason.DUPLICATE else merge.survivor.confidence,
        created_by=CURATOR_NAME,
        created_at=at,
    )


def _resting_on(repository: Repository, record: Assumption) -> tuple[Decision, ...]:
    """The live decisions that rest on this assumption, in read order.

    A reverse walk over `assumes` only, the same narrowing
    `praxis.agents.fusion_pass` makes: the question is what rests on *this
    assumption*, and widening it to the whole impact DAG would pull in decisions
    that reached it by another route.

    Retracted decisions are excluded, and that is load-bearing rather than
    tidy -- `Repository.get` returns a retracted record rather than `None`, so a
    curator testing only for `None` would refuse to retire an assumption whose
    only dependant was itself withdrawn.
    """
    edges = repository.links_to(record.id, types=(LinkType.ASSUMES,))
    found = (repository.get(Decision, edge.source_id) for edge in edges)
    return tuple(decision for decision in found if decision is not None and not decision.retracted)


def _same_document(repository: Repository, left: Assumption, right: Assumption) -> bool:
    """Whether both assumptions were read from one document.

    Through the spans they cite, because a record carries a `span_id` and
    nothing else about where it was read from -- which is invariant 6 doing its
    job rather than a lookup this module could avoid.
    """
    from praxis.domain.records import Span  # noqa: PLC0415 -- import cycle at module scope

    spans = [repository.get(Span, record.span_id) for record in (left, right)]
    if any(span is None for span in spans):
        return False
    return spans[0].doc_id == spans[1].doc_id  # type: ignore[union-attr]


def _shape_of(predicate: str) -> tuple[str, ...] | None:
    """A predicate reduced to what it constrains, or `None` if unreadable.

    Two predicates with the same shape place the same constraints on the same
    quantities, whatever they are spelled like. `None` means arithmetic has
    nothing to say -- an unparseable predicate, or one `constraints_of` declines
    to read, is never a duplicate of anything, because "I cannot read either of
    these" is not evidence that they agree.
    """
    if not predicate.strip():
        return None
    try:
        constraints = constraints_of(parse(predicate))
    except PredicateSyntaxError:
        return None
    return tuple(sorted(repr(constraint) for constraint in constraints)) or None


def _by_age(left: Assumption, right: Assumption) -> tuple[Assumption, Assumption]:
    """The pair as (later, earlier), with the id breaking a tie.

    A tie is broken rather than refused so that two assumptions written in the
    same ingestion run -- which share a timestamp -- still collapse, and collapse
    the same way on every pass.
    """
    ordered = sorted((left, right), key=lambda record: (record.created_at, record.id))
    return ordered[1], ordered[0]


def _ordered(left: str, right: str) -> tuple[str, str]:
    """A pair as a canonical key, so one pair is never two."""
    return (left, right) if left <= right else (right, left)


def idle_before(at: datetime, idle_days: int = IDLE_DAYS) -> datetime:
    """The latest creation time an assumption can have and still count as idle.

    Exported so a caller -- the CLI's explanation, a test's fixture -- can say
    what the window means without recomputing `timedelta` arithmetic that would
    then be able to disagree with the agent's.
    """
    return at - timedelta(days=idle_days)
