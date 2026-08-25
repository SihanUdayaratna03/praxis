"""Why a predicate would not parse, and where.

One exception type, not a family, because a caller does exactly one thing with any
of them: keep the assumption, record that its predicate is not machine-checkable,
and say so in the confidence. `AssumptionFormalizer` never lets one of these escape
-- an assumption that cannot be formalized is a low-confidence formalization with a
stated reason rather than a dropped record, which is the instinct
`DecisionScout` set and every Half A agent has followed since.

The position matters more than the message. A predicate is short, a person reading
a failed formalization is looking at one line, and "unexpected `outside` at 42" tells
them which character to look at where "invalid predicate" does not.
"""

from __future__ import annotations


class PredicateError(Exception):
    """Base class for every failure raised out of `praxis.predicates`."""


class PredicateSyntaxError(PredicateError):
    """The text is not a predicate this grammar can express.

    Raised by the lexer and the parser alike. The distinction between "that
    character has no meaning here" and "that token cannot follow the one before
    it" is real but nothing acts on it differently, so it is carried in `problem`
    rather than in two classes.

    Attributes:
        source: The predicate as it was written, unmodified.
        position: Index into `source` of the offending character. Zero-based, and
            clamped to the length of the source so an error at the end still
            points at somewhere real.
        problem: What is wrong, as a phrase that reads after "in ...".
    """

    def __init__(self, source: str, position: int, problem: str) -> None:
        """Record the text, the place and the complaint.

        Args:
            source: The predicate that would not parse.
            position: Where in it the trouble is.
            problem: What is wrong with it.
        """
        self.source = source
        self.position = max(0, min(position, len(source)))
        self.problem = problem
        super().__init__(f"{problem}, at position {self.position} of {source!r}")


class ExpiryError(PredicateError):
    """An expiry condition is not one of the three forms this project writes.

    Separate from `PredicateSyntaxError` because an expiry is not a predicate: it
    is one of `when(...)`, `after(...)` or `on_event(...)`, and the failure a
    caller reports is "that is not an expiry condition" rather than "that
    expression is malformed". A `when(...)` whose *inner* predicate is bad raises
    `PredicateSyntaxError` from inside, and that difference is worth keeping.

    Attributes:
        source: The expiry condition as it was written.
        problem: What is wrong with it.
    """

    def __init__(self, source: str, problem: str) -> None:
        """Record the text and the complaint.

        Args:
            source: The expiry condition that would not parse.
            problem: What is wrong with it.
        """
        self.source = source
        self.problem = problem
        super().__init__(f"{problem}: {source!r}")
