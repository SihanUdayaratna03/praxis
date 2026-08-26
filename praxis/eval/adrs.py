"""Grading the predicate language against the predicates written before it existed.

ADR 0001 recorded an assumption in 2026-08-09, when there was no parser and no
grammar:

> The predicate DSL built in Phase 5 can parse predicates written by hand before
> it existed --- `adr_predicates_parsed / adr_predicates_total >= 0.9`, expiring
> `on_event("Phase 5 predicate DSL is implemented")`.

That event has now happened, so the assumption is due and this is what answers
it. The number is a count over `praxis.predicates.parser.parses` across every
assumption table in `docs/adr/`, and it is computed rather than asserted --
which is the whole point of the exercise. An author who wanted the assumption to
hold could widen the grammar until it did; a report that recomputes it from the
files each time makes that visible as a grammar change rather than invisible as
a passing test.

**`docs/adr/template.md` is excluded and nothing else is.** It is a form with
`<expression>` where a predicate goes, so counting it would be counting the
shape of an ADR rather than an ADR. Every real row is counted including the ones
that fail, because the assumption is about hand-written predicates and the
interesting ones are exactly those the author got wrong.

This is the one metric in `praxis.eval` that grades the *project* rather than a
run over a corpus. It lives here because it is a graded number that goes in a
report, and there is no second reporting layer to put it in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from praxis.eval.metrics import RATE_PLACES
from praxis.predicates.expiry import parses_expiry
from praxis.predicates.parser import parses

TEMPLATE_NAME: Final = "template.md"
"""The form an ADR is written into. A shape, not an ADR."""

_ASSUMPTION_ROW: Final = re.compile(
    r"^\|\s*\d+\s*\|(?P<claim>[^|]*)\|(?P<predicate>[^|]*)\|(?P<expiry>[^|]*)\|\s*$"
)
"""A numbered row of an ADR's assumption table.

Anchored on a leading integer so the table's own header and separator rows are
not read as assumptions, and so a table elsewhere in an ADR -- the rejected
options table, which every one of these files has -- cannot contribute a row.
"""

_CODE: Final = re.compile(r"`([^`]*)`")


@dataclass(frozen=True, slots=True)
class AdrPredicate:
    """One hand-written predicate, and whether this build can read it.

    Attributes:
        adr: The file it came from, by name.
        claim: The assumption in words, so a failure is legible without opening
            the file.
        predicate: The expression as written, backticks stripped.
        expiry: The expiry condition as written.
        parsed: Whether the predicate parses.
        expiry_parsed: Whether the expiry condition does.
    """

    adr: str
    claim: str
    predicate: str
    expiry: str
    parsed: bool
    expiry_parsed: bool


@dataclass(frozen=True, slots=True)
class AdrPredicateReport:
    """What the ADR corpus says about the grammar built after it.

    Attributes:
        predicates: Every row found, in file then table order.
    """

    predicates: tuple[AdrPredicate, ...] = ()

    @property
    def total(self) -> int:
        """`adr_predicates_total`, as ADR 0001 names it."""
        return len(self.predicates)

    @property
    def parsed(self) -> int:
        """`adr_predicates_parsed`."""
        return sum(1 for found in self.predicates if found.parsed)

    @property
    def rate(self) -> Decimal:
        """The ratio ADR 0001's first assumption is a claim about.

        One when there are no predicates: the assumption is that the grammar
        can read what was written, and nothing written is nothing unread. It
        cannot flatter anything, because the files decide the denominator.
        """
        if not self.predicates:
            return Decimal(1).quantize(RATE_PLACES)
        return (Decimal(self.parsed) / Decimal(self.total)).quantize(RATE_PLACES)

    @property
    def expiries_parsed(self) -> int:
        """How many expiry conditions parse.

        Reported beside the predicate rate although ADR 0001 does not ask for
        it, because an assumption whose predicate parses and whose expiry does
        not is one `AssumptionMonitor` still cannot reach a verdict about.
        """
        return sum(1 for found in self.predicates if found.expiry_parsed)

    @property
    def unreadable(self) -> tuple[AdrPredicate, ...]:
        """The rows this grammar cannot read, which are the interesting ones."""
        return tuple(found for found in self.predicates if not found.parsed)


def read_adr_predicates(directory: Path) -> AdrPredicateReport:
    """Read every assumption table in a directory of ADRs.

    Args:
        directory: `docs/adr/`, or any directory of ADRs written in that shape.

    Returns:
        Every predicate found and whether it parses. Empty when the directory
        holds no ADRs, which is an honest answer rather than an error -- a
        packaged install has no `docs/` at all.
    """
    return AdrPredicateReport(
        predicates=tuple(
            found
            for path in sorted(directory.glob("*.md"))
            if path.name != TEMPLATE_NAME
            for found in _predicates_in(path)
        )
    )


def _predicates_in(path: Path) -> list[AdrPredicate]:
    """Every assumption row in one ADR."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:  # pragma: no cover -- glob returned it a moment ago
        return []
    found: list[AdrPredicate] = []
    for line in lines:
        row = _ASSUMPTION_ROW.match(line)
        if row is None:
            continue
        predicate = _uncoded(row.group("predicate"))
        expiry = _uncoded(row.group("expiry"))
        if not predicate:
            continue
        found.append(
            AdrPredicate(
                adr=path.name,
                claim=row.group("claim").strip(),
                predicate=predicate,
                expiry=expiry,
                parsed=parses(predicate),
                expiry_parsed=parses_expiry(expiry),
            )
        )
    return found


def _uncoded(cell: str) -> str:
    """A table cell as the expression it was trying to be.

    Backticks are markdown, not grammar, so they come off -- and a cell holding
    *two* code spans with prose between them, which one ADR really does, comes
    back as the prose it is rather than as its first span. Reading only the
    first would quietly turn an unparseable row into a parseable one and lose
    the finding.
    """
    stripped = cell.strip()
    return _CODE.sub(r"\1", stripped).strip()
