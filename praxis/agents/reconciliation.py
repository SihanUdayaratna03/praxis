"""The arithmetic half of outcome matching: selection, units, and the band.

Split out of `praxis.agents.matcher` on the line this whole project draws --
what is computed from what is judged. Nothing here takes a provider, nothing
here can be wrong in a way a better prompt would fix, and all of it is
property-tested. `OutcomeMatcher` is the part that asks a model; this is
everything it does before and after that call.

The split is not cosmetic. `match_quality` is a field a model would happily
fill in, and invariant 3 says it must not: "was this estimate close" is
arithmetic on two numbers, and an opinion sitting in that column would be
irreproducible, unfalsifiable, and indistinguishable in the eval table from a
number. Keeping the function in a module with no `LLMProvider` import makes
that structural rather than a rule somebody has to remember. See ADR 0021.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Final

from praxis.agents.blocking import word_keys
from praxis.domain.enums import MatchQuality, Unit
from praxis.domain.records import Estimate, Span

DEFAULT_MAX_CANDIDATES: Final = 12
"""How many passages are put in front of the model at once.

ADR 0015's second assumption states `spans_per_offering <= 12` and this is
exactly it. Not a coincidence and not a tuned number: widening it means
breaching a recorded assumption rather than editing a constant.
"""

DEFAULT_MIN_SHARED_WORDS: Final = 1
"""Shared discriminating words a passage needs to be worth reading.

Looser than `praxis.agents.blocking`'s two, and deliberately. Blocking is
choosing which of `n(n-1)/2` pairs to pay the reason tier for, where precision
is what bounds cost. This is choosing which of one document's passages to put in
a single call that is already being made, where the only cost of a loose
candidate is a line in a listing -- and the cost of a tight one is an outcome
nobody ever finds. Different question, different threshold.
"""

WORKING_DAY_HOURS: Final = Decimal(8)
"""Hours in a working day. A stated convention, not a fact about the world."""

WORKING_WEEK_DAYS: Final = Decimal(5)
"""Working days in a week. Also a convention, and recorded in `notes` whenever
it is applied, so a converted quantity can always be read back to what the
document actually said."""

_IN_HOURS: Final[dict[Unit, Decimal]] = {
    Unit.HOURS: Decimal(1),
    Unit.DAYS: WORKING_DAY_HOURS,
    Unit.WEEKS: WORKING_DAY_HOURS * WORKING_WEEK_DAYS,
}
"""The one family of units that converts. Everything else has no honest rate:
points are a team's own scale, `count` counts different things in two documents,
and turning dollars into weeks would require a labour rate nobody stated.
"""

EXACT_BELOW: Final = Decimal("1.10")
CLOSE_BELOW: Final = Decimal("1.50")
PARTIAL_BELOW: Final = Decimal("2.50")
"""The bands, on the ratio of the larger quantity to the smaller.

Checked against this project's own six hand-scored outcomes, where they
reproduce five. The sixth -- `OUT-0002` at 2.10x, scored `miss` rather than
`partial` -- was scored on failed *conditions* rather than on the ratio, and the
difference is reported as a finding rather than tuned away. This function
compares two numbers; it cannot see whether an estimate's conditions held and it
must not pretend to. See ADR 0021.
"""


def candidates_for(
    estimate: Estimate,
    spans: Sequence[Span],
    *,
    limit: int = DEFAULT_MAX_CANDIDATES,
    min_shared_words: int = DEFAULT_MIN_SHARED_WORDS,
) -> tuple[Span, ...]:
    """The passages worth reading for this estimate's actual. Deterministic.

    Selection before a model, so that a pair lost here is lost measurably rather
    than blamed on the model afterwards. The span the estimate itself was read
    from is always offered: a status update states the estimate and the actual in
    one passage often enough that dropping it would lose the commonest case.

    Args:
        estimate: The prediction whose actual is being looked for.
        spans: The document's spans, in document order.
        limit: How many passages may be offered. ADR 0015's ceiling.
        min_shared_words: Discriminating words a passage needs to qualify.

    Returns:
        The passages, in document order, capped at `limit`. Empty when nothing
        in the document shares any vocabulary with the estimate.
    """
    wanted = word_keys(estimate.subject, estimate.owner)
    scored = [(len(wanted & word_keys(span.text)), index, span) for index, span in enumerate(spans)]
    chosen = {
        span.id
        for shared, _, span in scored
        if shared >= min_shared_words or span.id == estimate.span_id
    }
    return tuple(span for span in spans if span.id in chosen)[:limit]


def converted(quantity: Decimal, source: Unit, target: Unit) -> Decimal | None:
    """One quantity in another unit, or `None` when there is no honest rate.

    Deterministic arithmetic over a stated convention -- eight hours to a working
    day, five days to a week -- and nothing else converts. `points` are a team's
    own scale, a `count` counts different things in two documents, and turning
    `usd` into `weeks` needs a labour rate nobody wrote down.

    Args:
        quantity: The amount as the document stated it.
        source: The unit it was stated in.
        target: The unit it is wanted in.

    Returns:
        The converted amount, or `None` if the two units are not commensurable.
    """
    if source is target:
        return quantity
    if source not in _IN_HOURS or target not in _IN_HOURS:
        return None
    return quantity * _IN_HOURS[source] / _IN_HOURS[target]


def quality_for(estimated: Decimal, actual: Decimal) -> tuple[MatchQuality, Decimal | None]:
    """How well an actual answered an estimate, and the ratio it was read off.

    Arithmetic and only arithmetic. It compares two numbers, which means it
    cannot see whether the estimate's stated conditions held -- and it must not
    pretend to. See ADR 0021.

    A zero on one side has no ratio: an estimate of no hands-on effort answered
    by real hands-on effort is not "infinitely wrong", it is a miss, and
    reporting a ratio there would be inventing one. Both zero agree exactly.

    Args:
        estimated: Predicted hands-on effort.
        actual: Hands-on effort really spent, already in the estimate's unit.

    Returns:
        The band, and the ratio of the larger to the smaller where one exists.
    """
    if estimated == actual:
        return MatchQuality.EXACT, Decimal(1) if estimated else None
    if not estimated or not actual:
        return MatchQuality.MISS, None
    ratio = max(estimated, actual) / min(estimated, actual)
    if ratio < EXACT_BELOW:
        return MatchQuality.EXACT, ratio
    if ratio < CLOSE_BELOW:
        return MatchQuality.CLOSE, ratio
    if ratio < PARTIAL_BELOW:
        return MatchQuality.PARTIAL, ratio
    return MatchQuality.MISS, ratio
