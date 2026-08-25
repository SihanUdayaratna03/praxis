"""Numbers that make a predicate true, and numbers that make it false.

The corpus has to say what a monitoring run should conclude about each of its
assumptions, which means supplying measurements. Those measurements could have
been written into `praxis.corpus.topics` beside each predicate, and that is the
obvious thing to do and the wrong one: two hand-written literals -- the
predicate and a value said to violate it -- can disagree, and nothing would
notice. `index_size_gb <= 50` beside a "breaching" measurement of 40 would make
the corpus expect a breach that never happens, and the eval table would report a
monitor bug that was really a typo in the answer key.

So the values are *derived from the predicate*, using the same interval
arithmetic `ContradictionDetector` settles pairs with. A witness inside the
satisfying range makes the predicate hold; one outside it makes the predicate
false. They cannot disagree with the predicate because they are computed from
it.

Only the shapes `praxis.predicates.intervals` can read are handled, which is
every predicate this corpus generates: one name, one comparison, one number. A
predicate outside that comes back with no measurement at all, and the assumption
is then expected to stay unverified -- which is the honest expectation for a
claim nothing can measure.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from praxis.predicates.errors import PredicateSyntaxError
from praxis.predicates.intervals import NumericRange, constraints_of
from praxis.predicates.parser import parse

STEP: Final = Decimal("0.001")
"""How far past a bound a witness is placed.

Small enough not to overshoot a neighbouring threshold in this corpus, and
finite because an open bound needs a value strictly beyond it. `<= 50` and
`< 50` want different witnesses and this is the difference between them.
"""

_FAR: Final = Decimal(1)
"""How far inside an unbounded direction to place a witness.

An unbounded range has no far end to work back from, so a witness is placed a
step in from the bound that does exist.
"""


def satisfying(predicate: str) -> tuple[str, Decimal] | None:
    """A name and a value that makes the predicate true, if one can be computed.

    Args:
        predicate: The predicate as written.

    Returns:
        The quantity and a measurement holding it, or `None` when the predicate
        is not one name against one number.
    """
    return _witness(predicate, inside=True)


def violating(predicate: str) -> tuple[str, Decimal] | None:
    """A name and a value that makes the predicate false, if one can be computed.

    Args:
        predicate: The predicate as written.

    Returns:
        The quantity and a measurement breaching it, or `None` when the
        predicate is not one name against one number.
    """
    return _witness(predicate, inside=False)


def _witness(predicate: str, *, inside: bool) -> tuple[str, Decimal] | None:
    """One measurement, inside or outside a predicate's satisfying range."""
    single = _single_range(predicate)
    if single is None:
        return None
    return single.name, (_inside(single) if inside else _outside(single))


def _single_range(predicate: str) -> NumericRange | None:
    """The one numeric constraint a predicate places, if it places exactly one.

    More than one and there is no single measurement that settles it; fewer and
    there is nothing to measure.
    """
    try:
        constraints = constraints_of(parse(predicate))
    except PredicateSyntaxError:
        return None
    ranges = [found for found in constraints if isinstance(found, NumericRange)]
    return ranges[0] if len(ranges) == 1 and len(constraints) == 1 else None


def _inside(found: NumericRange) -> Decimal:
    """A value the range permits.

    A closed bound is itself permitted, so it can be the witness; an open one
    needs a step inward.
    """
    if found.low is not None and found.high is not None:
        return found.low if found.low_closed else found.low + STEP
    if found.high is not None:
        return found.high if found.high_closed else found.high - STEP
    if found.low is not None:
        return found.low if found.low_closed else found.low + STEP
    return Decimal(0)  # pragma: no cover -- an unbounded range has no constraint


def _outside(found: NumericRange) -> Decimal:
    """A value the range forbids.

    Past whichever bound exists. A closed bound is stepped past; an open one is
    the witness itself, since the bound is exactly what it excludes.
    """
    if found.high is not None:
        return found.high + STEP if found.high_closed else found.high
    if found.low is not None:
        return found.low - STEP if found.low_closed else found.low
    return _FAR  # pragma: no cover -- an unbounded range forbids nothing
