"""`BiasDetective`: what an estimator's history says, and when it says nothing.

Deterministic, and named in `NON_LLM_AGENTS` since Phase 0. There is no text to
interpret here -- the input is `Estimate` and `Outcome` rows the store already
holds, and every number out of this module is arithmetic over them. It imports
no provider and therefore cannot reach one, which is invariant 3 made structural
rather than remembered, the same shape `praxis.agents.reconciliation` took in
Phase 6.

**Most of this module is about refusing, and that is not a defensive posture --
it is the product.** Run against this project's own history it declines on every
class: `agent-implementation` at `n = 4`, three others at `n = 1`. A calibration
system that answered anyway would be worse than none, because a factor fitted to
two points is indistinguishable, in a table, from one fitted to two hundred.

**The threshold is `n = 5`, it is the only threshold, and there is no override.**
Not a default, not a parameter, not a keyword argument for the caller who is
sure. A number that can be lowered by whoever wants an answer is not a
threshold. This project's own estimation practice has been simulating this rule
by hand for eight consecutive estimates; from here the rule and the code have to
agree, and `tests/agents/test_bias.py` is where that agreement is proved.

**Dispersion widens the band; it never refuses.** This is the design decision
ADR 0024 records, and it is the one that could reasonably have gone the other
way. Sample size is a fact about how much evidence there is, and too little
evidence means silence. Scatter is a fact about *the estimator* -- and an
estimator who is erratic is precisely the person who needs telling. "1.6x, but
ordinarily anywhere from 0.9x to 2.9x, n=6, confidence 0.31" is more useful than
silence and more honest than a bare 1.6x. So a wide sample produces a wide band
and a low confidence, never a second refusal on a second magic number.

**A factor below the threshold is not computed, not merely withheld.** The
arithmetic is never run for a group that cannot speak, so there is no field
anywhere on the returned object holding a number the caller was not supposed to
see. Printing the factor you are refusing to stand behind is how a threshold
becomes decorative.

**An unclassified estimate is in no group.** `work_class` is a grouping key, not
a label, so `unclassified` is the absence of a key -- pooling those rows would
compute one factor over migrations, refactors and incident response together and
call it a person's bias. They are reported as their own row with a verdict that
says why, because "you have fourteen estimates in no class" is the thing a
person can act on; they are never summarised.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from praxis.agents.classifier import UNCLASSIFIED
from praxis.agents.distribution import Spread, ratio_of, spread_of
from praxis.store.reports import CalibrationRow
from praxis.store.repository import Repository

DETECTIVE_NAME: Final = "BiasDetective"
"""Spelled as `praxis.config.models.NON_LLM_AGENTS` spells it, so the set that
refuses to route this agent and the agent itself cannot drift apart under a
rename."""

MINIMUM_SAMPLE: Final = 5
"""Resolved estimates a group needs before this module will say anything.

The one threshold in the system and the one with no override. Five is a
judgement rather than a derivation -- it is small enough that a real team
reaches it within a quarter on a class of work they do often, and large enough
that a single unusual project cannot set the factor by itself. What matters more
than the number is that it is *a* number, applied everywhere, that nobody can
pass an argument to move.

`praxis.agents.distribution.CONFIDENCE_HALF_AT` is deliberately the same value,
so a group that has only just cleared the bar reports a confidence of at most
one half. A test binds the two rather than leaving them equal by coincidence.
"""

NEUTRAL_BAND: Final = Decimal("0.05")
"""How close to 1.0 counts as no direction at all.

Within five percent either way an estimator is calibrated, and reporting them as
"1.02x under" would be reading noise as a finding. The band exists because the
direction is the part of the output a person repeats out loud, and a direction
asserted about a factor of 1.01 is a sentence that will be quoted back as though
it meant something.
"""


class BiasVerdict(StrEnum):
    """Whether a group has anything to say, and if not, which kind of nothing.

    Four members rather than a boolean, because the four are acted on
    differently and a caller that cannot tell them apart will describe all of
    them as "no data". A class nobody has ever resolved is a fact about
    follow-through; a class of four is a fact about time; a class of
    `unclassified` is a fact about classification and is the only one somebody
    can fix this afternoon.
    """

    MEASURED = "measured"
    """Enough resolved history to state a factor, with its band."""

    INSUFFICIENT_SAMPLE = "insufficient_sample"
    """Fewer than `MINIMUM_SAMPLE` usable ratios. The ordinary answer."""

    NO_RESOLVED_OUTCOMES = "no_resolved_outcomes"
    """Estimates exist and nothing ever answered any of them."""

    UNCLASSIFIED = "unclassified"
    """The rows carry no work class, so they form no group."""


class BiasDirection(StrEnum):
    """Which way an estimator is wrong, in the words a person would use."""

    UNDER = "under"
    """The work runs longer than predicted. The factor is above one."""

    OVER = "over"
    """The work finishes sooner than predicted. The factor is below one."""

    NONE = "none"
    """Inside `NEUTRAL_BAND`. Calibrated, and saying so is a result."""


@dataclass(frozen=True, slots=True)
class CalibrationGroup:
    """The axis calibration is computed along: one estimator, one kind of work."""

    owner: str
    work_class: str

    @property
    def classified(self) -> bool:
        """Whether this group is a group at all rather than the absence of one."""
        return self.work_class != UNCLASSIFIED


@dataclass(frozen=True, slots=True)
class CalibrationFactor:
    """What one group's history says, or why it says nothing.

    Returned in every case, including every refusal: `factor_for` never returns
    `None` and never raises, so "this group has no factor" and "something went
    wrong" are never the same code path in the caller. Phase 8 reads this object
    directly and its whole fusion sentence comes off these fields.

    `spread` is `None` for every verdict but `MEASURED`, and that is stronger
    than it looks: below the threshold the arithmetic is never run, so there is
    no withheld number on this object for a determined caller to reach.

    Attributes:
        group: Whose estimates, and of what kind.
        verdict: Whether this speaks, and if not, which kind of silence.
        considered: Every estimate in the group, answered or not. The
            denominator -- "fourteen of nineteen answered" needs both.
        resolved: Those an outcome actually resolved.
        n: Those that produced a usable ratio. At most `resolved`.
        excluded: Resolved rows carrying no computable ratio, because a zero on
            either side has none. Reported rather than silently dropped, so
            `n` never disagrees with `resolved` without saying why.
        reason: Why this spoke or declined, in a sentence. Never empty.
        spread: The summary, when there is one.
        direction: Which way, when there is one.
    """

    group: CalibrationGroup
    verdict: BiasVerdict
    considered: int
    resolved: int
    n: int
    excluded: int
    reason: str
    spread: Spread | None = None
    direction: BiasDirection | None = None

    @property
    def speaks(self) -> bool:
        """Whether this carries a factor a caller may apply."""
        return self.verdict is BiasVerdict.MEASURED

    @property
    def factor(self) -> Decimal | None:
        """The multiplier to apply to a raw estimate, or `None`.

        `actual / estimated`, so it is applied by multiplication and never needs
        the caller to remember which way round it goes.
        """
        return None if self.spread is None else self.spread.central

    @property
    def low(self) -> Decimal | None:
        """The optimistic end of the band."""
        return None if self.spread is None else self.spread.low

    @property
    def high(self) -> Decimal | None:
        """The pessimistic end of the band."""
        return None if self.spread is None else self.spread.high

    @property
    def confidence(self) -> Decimal | None:
        """How much of a signal this is, from sample size and scatter together."""
        return None if self.spread is None else self.spread.confidence

    @property
    def magnitude(self) -> Decimal | None:
        """The factor as a person says it: a number at or above one.

        The same fact as `factor`, read for prose rather than for arithmetic.
        "0.61x" and "1.64x over" are one measurement, and a system that printed
        the first would be describing an over-estimator with a number below one
        while its own documentation says "1.8x under". Both readings exist here
        with a test binding them, because the alternative is two spellings
        drifting apart in a sentence people repeat.

        Keyed on the factor and **not** on the direction, which is a distinction
        a property test bought. Inside `NEUTRAL_BAND` the direction is `none`
        while the factor is still a little under one, and keying on the direction
        printed "0.95x none" -- a magnitude below one, in the field whose whole
        job is to be above it.
        """
        if self.spread is None:
            return None
        if self.spread.central < Decimal(1):
            return _inverted(self.spread.central)
        return self.spread.central

    def describe(self) -> str:
        """One line, in the shape `ARCHITECTURE.md`'s fusion sentence uses.

        Phase 8 renders "this team's calibration factor for migration work is
        1.8x under, n=14, confidence 0.71" out of this. Written here rather than
        in `FusionBridge` so the shape is fixed by the module that owns the
        numbers, and tested against that exact sentence.
        """
        if not self.speaks:
            return f"{self.group.work_class}: {self.reason}"
        return (
            f"{self.group.work_class} work is {self.magnitude}x {self.direction}, "
            f"n={self.n}, confidence={self.confidence}"
        )


class BiasDetective:
    """Reads calibration history and reports a factor per group, or refuses.

    A read and nothing else: it writes no record, allocates no id and takes no
    provider. `praxis.agents.calibration` is what turns a verdict into something
    stored.

    Two entry points, one for each shape of question. `factor_for` answers about
    a single group in one narrowed read, which is how `FusionBridge` will ask it
    -- once per assumption being priced. `all_factors` answers about every group
    in one unnarrowed read, walking rows that already arrive grouped, which is
    how a report or the CLI asks. Neither is built on the other, because a
    per-group loop over every group would be one query per class, and reading
    everything to answer about one is the cost Phase 6's read was just fixed to
    stop paying.
    """

    def __init__(self, repository: Repository) -> None:
        """Bind the detective to a store.

        Args:
            repository: The store to read history from. Nothing is written.
        """
        self._repository = repository

    def factor_for(self, *, owner: str, work_class: str) -> CalibrationFactor:
        """What this person's history says about this kind of work.

        One indexed read and `O(n)` arithmetic over what comes back. This is the
        call Phase 8 makes per assumption, so it walks nothing and joins nothing
        beyond the one join `calibration_history` already does.

        Args:
            owner: The estimator.
            work_class: The kind of work, in the store's own spelling.

        Returns:
            A factor, or the reason there is none. Never `None`.
        """
        group = CalibrationGroup(owner=owner, work_class=work_class)
        if not group.classified:
            return _unclassified(group, considered=0)
        rows = self._repository.calibration_history(owner=owner, work_class=work_class)
        return summarise(group, rows)

    def all_factors(self) -> tuple[CalibrationFactor, ...]:
        """Every group the store holds, in the order the read returns them.

        One read. `calibration_history` orders by owner, then work class, then
        estimate id, so grouping is a walk and not a sort -- the property Phase 6
        wrote the query to have.

        Returns:
            One result per group, refusals included and in the same tuple.
            Filtering them out here would leave a caller unable to print the
            thing a person can act on: the classes that are one outcome short.
        """
        history = self._repository.calibration_history()
        return tuple(summarise(group, rows) for group, rows in _grouped(history))


def summarise(group: CalibrationGroup, rows: Sequence[CalibrationRow]) -> CalibrationFactor:
    """Turn one group's history into a factor or a refusal.

    Free rather than a method so the whole decision can be tested against a list
    of rows without a store, which is what lets the refusal properties be
    generated rather than fixtured.

    The order of the checks is the argument. Classification first, because an
    unclassified row is not in this group or any other and counting it would
    make every later number wrong. Then whether anything was ever resolved,
    which is a different fact from having too few. Then the threshold. The
    arithmetic runs last and only once everything above has passed, so a group
    that cannot speak has no factor computed for it at all.

    A group with no rows at all is an insufficient sample rather than a group
    nobody resolved. "Nine estimates and no outcomes" is a statement about
    follow-through; "no estimates" is a statement about nothing, and giving the
    two one verdict would put a group that does not exist in the column a person
    reads to find work worth chasing.

    Args:
        group: The key these rows share.
        rows: That group's history, resolved and unresolved together.

    Returns:
        The group's verdict, always populated.
    """
    if not group.classified:
        return _unclassified(group, considered=len(rows))

    resolved = [row for row in rows if row.resolved]
    if rows and not resolved:
        return _silent(group, rows)

    ratios = _ratios(resolved)
    excluded = len(resolved) - len(ratios)
    if len(ratios) < MINIMUM_SAMPLE:
        return CalibrationFactor(
            group=group,
            verdict=BiasVerdict.INSUFFICIENT_SAMPLE,
            considered=len(rows),
            resolved=len(resolved),
            n=len(ratios),
            excluded=excluded,
            reason=_short_by(len(ratios), excluded),
        )

    spread = spread_of(ratios)
    if spread is None:  # pragma: no cover -- unreachable: the threshold is above zero
        message = "a sample above the threshold cannot be empty"
        raise AssertionError(message)
    return CalibrationFactor(
        group=group,
        verdict=BiasVerdict.MEASURED,
        considered=len(rows),
        resolved=len(resolved),
        n=spread.n,
        excluded=excluded,
        reason=(
            f"{spread.n} resolved estimates of {group.work_class} work, "
            f"ordinarily between {spread.low}x and {spread.high}x of what was predicted"
        ),
        spread=spread,
        direction=direction_of(spread.central),
    )


def direction_of(factor: Decimal) -> BiasDirection:
    """Which way a factor points, with a neutral band around one.

    Args:
        factor: `actual / estimated`.

    Returns:
        `UNDER` above the band, `OVER` below it, `NONE` inside it.
    """
    if factor > Decimal(1) + NEUTRAL_BAND:
        return BiasDirection.UNDER
    if factor < Decimal(1) - NEUTRAL_BAND:
        return BiasDirection.OVER
    return BiasDirection.NONE


def _ratios(rows: Iterable[CalibrationRow]) -> list[Decimal]:
    """Every usable ratio in a resolved sample, active against active.

    Active and never total, which is `Outcome`'s whole reason for carrying the
    split. A phase that ran long because it waited on someone else is not an
    estimation error, and folding blocked time in would teach the calibrator that
    this estimator is unreliable about work they did correctly.

    A row with a zero on either side yields nothing -- `ratio_of` refuses to
    invent one -- and the caller counts the gap as `excluded` rather than letting
    `n` shrink silently.
    """
    ratios = []
    for row in rows:
        if row.actual_active is None:
            continue
        ratio = ratio_of(row.estimated_active, row.actual_active)
        if ratio is not None:
            ratios.append(ratio)
    return ratios


def _grouped(
    rows: Sequence[CalibrationRow],
) -> Iterable[tuple[CalibrationGroup, list[CalibrationRow]]]:
    """Walk pre-ordered rows into groups, without sorting them again.

    `calibration_history` returns rows ordered by owner, then work class, so a
    single pass is enough and re-sorting would throw away a property the query
    was written to have. A row whose key differs from the last one starts a new
    group; nothing is buffered but the group in hand.
    """
    current: list[CalibrationRow] = []
    key: CalibrationGroup | None = None
    for row in rows:
        owner, work_class = row.group
        this = CalibrationGroup(owner=owner, work_class=work_class)
        if key is not None and this != key:
            yield key, current
            current = []
        key = this
        current.append(row)
    if key is not None:
        yield key, current


def _unclassified(group: CalibrationGroup, *, considered: int) -> CalibrationFactor:
    """The row a person can act on, rather than a silence."""
    return CalibrationFactor(
        group=group,
        verdict=BiasVerdict.UNCLASSIFIED,
        considered=considered,
        resolved=0,
        n=0,
        excluded=0,
        reason=(
            f"{considered} estimates carry no work class, so they belong to no group. "
            f"Calibration is per estimator per class of work; pooling unrelated work "
            f"under one factor would describe nobody's bias."
        ),
    )


def _silent(group: CalibrationGroup, rows: Sequence[CalibrationRow]) -> CalibrationFactor:
    """Estimates were made and nothing ever answered them."""
    return CalibrationFactor(
        group=group,
        verdict=BiasVerdict.NO_RESOLVED_OUTCOMES,
        considered=len(rows),
        resolved=0,
        n=0,
        excluded=0,
        reason=(
            f"{len(rows)} estimates of {group.work_class} work and not one resolved outcome. "
            f"Nothing here is about estimation yet."
        ),
    )


def _short_by(n: int, excluded: int) -> str:
    """Why a group of this size cannot speak, said in full.

    Names how many more are needed rather than only how many there are, because
    "one short" is the sentence that makes a person go and close an outcome.
    """
    if not n:
        return (
            f"no estimates with a usable ratio, {MINIMUM_SAMPLE} short of the "
            f"{MINIMUM_SAMPLE} this refuses below."
            + (f" {excluded} resolved but had a zero on one side." if excluded else "")
        )
    missing = MINIMUM_SAMPLE - n
    sentence = (
        f"{n} usable {'ratio' if n == 1 else 'ratios'}, {missing} short of the "
        f"{MINIMUM_SAMPLE} this refuses below. No factor is computed for a sample "
        f"this size, rather than computed and withheld."
    )
    if excluded:
        sentence += (
            f" {excluded} resolved {'estimate' if excluded == 1 else 'estimates'} "
            f"had a zero on one side and yield no ratio."
        )
    return sentence


def _inverted(factor: Decimal) -> Decimal:
    """A factor below one, read as how many times over the estimate was."""
    return (Decimal(1) / factor).quantize(factor)
