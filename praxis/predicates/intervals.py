"""What a comparison permits, so two predicates can be shown to conflict by arithmetic.

`ContradictionDetector` is routed to the `reason` tier, and most of the pairs it
is handed do not need it. `index_size_gb <= 50` and `index_size_gb > 50` are
irreconcilable for a reason a model has no privileged access to: the sets of
numbers they permit do not intersect. Settling those here costs nothing, is
reproducible, and leaves the expensive tier for the pairs that are genuinely a
judgement -- two assumptions phrased in prose about the same thing.

This is invariant 3 applied to a place it is easy to miss. A contradiction found
by arithmetic is a contradiction two runs agree about; one found by a model is a
contradiction with a confidence attached. Both are written as `contradicts`
edges and `Link.confidence` is what tells them apart, so the eval table can
report the two recalls separately -- which is the only way to know whether the
blocking or the judgement is what needs work.

**Only a conjunction of simple comparisons is read.** `a <= 1 and b >= 2` yields
two constraints; `a <= 1 or b >= 2` yields nothing at all. A disjunction permits
a union of regions and showing two unions disjoint is a different and much
larger problem -- one whose wrong answers would be *false contradictions*, which
is the expensive direction to be wrong in.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from praxis.predicates.ast import (
    CompareOp,
    Comparison,
    Conjunction,
    Constant,
    Formula,
    Identifier,
    Term,
    render,
)
from praxis.predicates.world import Value

_FLIPPED: Final[dict[CompareOp, CompareOp]] = {
    CompareOp.LE: CompareOp.GE,
    CompareOp.GE: CompareOp.LE,
    CompareOp.LT: CompareOp.GT,
    CompareOp.GT: CompareOp.LT,
    CompareOp.EQ: CompareOp.EQ,
    CompareOp.NE: CompareOp.NE,
}
"""The same claim read from the other side. `50 >= x` is `x <= 50`, and
normalising here means everything below only has to handle one orientation."""

_UPPER_BOUNDS: Final[dict[CompareOp, bool]] = {CompareOp.LE: True, CompareOp.LT: False}
_LOWER_BOUNDS: Final[dict[CompareOp, bool]] = {CompareOp.GE: True, CompareOp.GT: False}
"""Which comparisons bound a quantity from which side, and whether the bound
itself is permitted. `==` is in neither: it bounds from both."""


@dataclass(frozen=True, slots=True)
class NumericRange:
    """The numbers one comparison against a numeric literal permits.

    Attributes:
        name: The identifier constrained.
        low: The lower bound, or `None` when unbounded below.
        low_closed: Whether `low` itself is permitted.
        high: The upper bound, or `None` when unbounded above.
        high_closed: Whether `high` itself is permitted.
    """

    name: str
    low: Decimal | None
    low_closed: bool
    high: Decimal | None
    high_closed: bool


@dataclass(frozen=True, slots=True)
class ExactValue:
    """`name == value`, where the value is not a number.

    Kept apart from a degenerate range because a boolean and a string have no
    order -- `valid == true` and `valid == false` conflict, and neither of them
    is an interval.
    """

    name: str
    value: Value


@dataclass(frozen=True, slots=True)
class ExcludedValue:
    """`name != value`. A hole rather than a region, so it is its own shape."""

    name: str
    value: Value


type Constraint = NumericRange | ExactValue | ExcludedValue
"""What one simple comparison says about one identifier."""


def constraints_of(formula: Formula) -> tuple[Constraint, ...]:
    """Read a predicate as the constraints it places on named quantities.

    Args:
        formula: The parsed predicate.

    Returns:
        One constraint per simple comparison, in the order they appear. **Empty
        when the predicate is not a conjunction of simple comparisons** -- which
        includes anything with `or`, anything negated, and any comparison whose
        sides are arithmetic rather than a name and a literal. An empty result
        means "arithmetic has nothing to say", never "there is no constraint".
    """
    match formula:
        case Conjunction(left=left, right=right):
            parts = constraints_of(left), constraints_of(right)
            # A conjunction is readable only if both halves are. Keeping the
            # readable half would silently weaken the claim being checked, and a
            # weakened claim is what produces a contradiction nobody agrees with.
            return () if not all(parts) else parts[0] + parts[1]
        case Comparison(op=op, left=left, right=right):
            simple = _simple(op, left, right)
            return () if simple is None else (simple,)
        case _:
            return ()


def conflict(first: Constraint, second: Constraint) -> str | None:
    """Why two constraints cannot both hold, or `None` if they can.

    Args:
        first: One constraint.
        second: Another.

    Returns:
        A sentence naming the incompatibility, suitable for a `Link.rationale`,
        or `None` when the two are satisfiable together or when arithmetic
        cannot tell. **`None` is not "they agree"** -- it is "nothing here
        proves otherwise", and the caller's next move is to ask a model.
    """
    if first.name != second.name:
        return None
    if isinstance(first, NumericRange) and isinstance(second, NumericRange):
        return _ranges_conflict(first, second)
    if isinstance(first, ExactValue) and isinstance(second, ExactValue):
        if first.value == second.value:
            return None
        return (
            f"{first.name} cannot be both {_as_written(first.value)} "
            f"and {_as_written(second.value)}"
        )
    return _exclusion_conflict(first, second) or _exclusion_conflict(second, first)


def _exclusion_conflict(exact: Constraint, excluded: Constraint) -> str | None:
    """A value asserted and the same value ruled out."""
    if not isinstance(exact, ExactValue) or not isinstance(excluded, ExcludedValue):
        return None
    if exact.value != excluded.value:
        return None
    return f"{exact.name} is asserted to be {_as_written(exact.value)} and ruled out from being it"


def _ranges_conflict(first: NumericRange, second: NumericRange) -> str | None:
    """Whether two numeric ranges share no number at all."""
    low, low_closed = _tighter_low(first, second)
    high, high_closed = _tighter_high(first, second)
    if low is None or high is None:
        return None
    if low < high or (low == high and low_closed and high_closed):
        return None
    return (
        f"{first.name} is required to be {_describe(first)} "
        f"and also {_describe(second)}, which share no value"
    )


def _tighter_low(first: NumericRange, second: NumericRange) -> tuple[Decimal | None, bool]:
    """The higher of two lower bounds, taking the stricter one when they meet."""
    if first.low is None:
        return second.low, second.low_closed
    if second.low is None:
        return first.low, first.low_closed
    if first.low > second.low:
        return first.low, first.low_closed
    if second.low > first.low:
        return second.low, second.low_closed
    return first.low, first.low_closed and second.low_closed


def _tighter_high(first: NumericRange, second: NumericRange) -> tuple[Decimal | None, bool]:
    """The lower of two upper bounds, taking the stricter one when they meet."""
    if first.high is None:
        return second.high, second.high_closed
    if second.high is None:
        return first.high, first.high_closed
    if first.high < second.high:
        return first.high, first.high_closed
    if second.high < first.high:
        return second.high, second.high_closed
    return first.high, first.high_closed and second.high_closed


def _simple(op: CompareOp, left: Term, right: Term) -> Constraint | None:
    """One comparison as a constraint, if it is a name against a literal."""
    if isinstance(left, Identifier) and isinstance(right, Constant):
        return _constrain(left.name, op, right.value)
    if isinstance(left, Constant) and isinstance(right, Identifier):
        return _constrain(right.name, _FLIPPED[op], left.value)
    return None


def _constrain(name: str, op: CompareOp, value: Value) -> Constraint | None:
    """What one normalised comparison says about one name."""
    if op is CompareOp.NE:
        return ExcludedValue(name=name, value=value)
    if not isinstance(value, Decimal):
        # A boolean or a string, which has no order. Only equality says
        # anything, and `<` over one was already refused by the evaluator.
        return ExactValue(name=name, value=value) if op is CompareOp.EQ else None
    return _range(name, op, value)


def _range(name: str, op: CompareOp, value: Decimal) -> NumericRange:
    """A numeric comparison as the interval of numbers it permits.

    `==` falls through to a closed point rather than being listed, because a
    point is what the two bounds meeting *is* -- and writing it as one keeps the
    intersection arithmetic from needing a case for equality.
    """
    if op in _UPPER_BOUNDS:
        return NumericRange(
            name, low=None, low_closed=False, high=value, high_closed=_UPPER_BOUNDS[op]
        )
    if op in _LOWER_BOUNDS:
        return NumericRange(
            name, low=value, low_closed=_LOWER_BOUNDS[op], high=None, high_closed=False
        )
    return NumericRange(name, low=value, low_closed=True, high=value, high_closed=True)


def _describe(constraint: NumericRange) -> str:
    """A range as a phrase that reads inside a rationale.

    Total over the three shapes `_constrain` can produce -- bounded above,
    bounded below, or a closed point -- and no wider. A range bounded on both
    sides at two different values is not one of them: a conjunction yields two
    constraints rather than a merged interval, so there is no fourth phrase to
    write and no dead branch to leave lying here.
    """
    if constraint.high is None:
        return f"{'at least' if constraint.low_closed else 'over'} {constraint.low}"
    if constraint.low is None:
        return f"{'at most' if constraint.high_closed else 'under'} {constraint.high}"
    return f"exactly {constraint.low}"


def _as_written(value: Value) -> str:
    """A value spelled the way a predicate spells it.

    Through the renderer rather than through `repr`, so a rationale a person
    reads says `true` where the predicate said `true`, and not `True`.
    """
    return render(Constant(value=value))
