"""The shape a predicate has once it is read, and the truth value it can reach.

The truth value is named `Truth` rather than `Verdict` because
`praxis.domain.enums.Verdict` is a different thing -- what survived
`ChallengerAgent` -- and `praxis.monitor` imports both.

**A truth value is three-valued, and that is the decision this module exists to hold.**
`TRUE`, `FALSE` and `UNKNOWN` -- never two. An assumption whose predicate mentions
a quantity nobody has measured has not been violated; it has not been checked. A
two-valued evaluator has to call that `FALSE`, and `FALSE` is what raises an
`AssumptionBreach` against every decision resting on it. The whole of the
distinction the phase is graded on -- a genuinely violated predicate against one
that is merely old -- lives in the difference between `FALSE` and `UNKNOWN`, so it
is in the type rather than in a convention each caller has to remember.

`UNKNOWN` carries a reason and the other two do not, enforced here rather than
hoped for. A verdict a person cannot act on is worth very little, and "unknown"
with no explanation is exactly that.

The nodes below are a closed set. `Term` is the arithmetic half -- what a value is
-- and `Formula` is the logical half, which is what a predicate must be at the top:
`index_size_gb` alone is a quantity and not a claim, and accepting it would let a
predicate truncated by a bad model answer parse as though it said something.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Truth(StrEnum):
    """What evaluating a predicate concluded."""

    TRUE = "true"
    """The predicate holds against the facts it was read with."""

    FALSE = "false"
    """The predicate is violated. The only verdict that raises a breach."""

    UNKNOWN = "unknown"
    """The facts do not settle it -- an identifier nothing bound, a division by
    zero, two values that cannot be compared. Not a violation, and the reason
    the monitor can tell an aged assumption from a broken one."""


class CompareOp(StrEnum):
    """The six comparisons a predicate may make.

    The member value is the operator's own spelling, so a rendered predicate is
    the text a person would have written and the lexer's table and this enum
    cannot drift.
    """

    LE = "<="
    GE = ">="
    EQ = "=="
    NE = "!="
    LT = "<"
    GT = ">"

    @property
    def is_ordering(self) -> bool:
        """Whether this comparison needs its operands to be ordered.

        `==` and `!=` are defined between any two values of one type. The other
        four are not: ordering two booleans, or two strings, is a question this
        language declines to answer rather than answers arbitrarily.
        """
        return self not in {CompareOp.EQ, CompareOp.NE}


class ArithOp(StrEnum):
    """The four arithmetic operators, over `Decimal` and nothing else."""

    ADD = "+"
    SUBTRACT = "-"
    MULTIPLY = "*"
    DIVIDE = "/"


@dataclass(frozen=True, slots=True)
class Evaluation:
    """A truth value and, when it is `UNKNOWN`, why.

    Attributes:
        truth: What the evaluator concluded.
        reason: Why it could not decide. Required for `UNKNOWN` and forbidden
            otherwise, so a verdict is never accompanied by an explanation that
            contradicts it.
    """

    truth: Truth
    reason: str = ""

    def __post_init__(self) -> None:
        """Keep a verdict and its explanation from disagreeing.

        Raises:
            ValueError: if `UNKNOWN` carries no reason, or a decided verdict does.
        """
        if self.truth is Truth.UNKNOWN and not self.reason:
            message = "an unknown verdict without a reason is not something anyone can act on"
            raise ValueError(message)
        if self.truth is not Truth.UNKNOWN and self.reason:
            message = f"a {self.truth.value} verdict carries no reason, got {self.reason!r}"
            raise ValueError(message)

    @property
    def holds(self) -> bool:
        """Whether the predicate was found true. `UNKNOWN` is not true."""
        return self.truth is Truth.TRUE

    @property
    def violated(self) -> bool:
        """Whether the predicate was found false. `UNKNOWN` is not a violation.

        The property `AssumptionMonitor` branches on to raise a breach, named so
        that reading the call site makes the three-valued rule obvious.
        """
        return self.truth is Truth.FALSE

    @property
    def decided(self) -> bool:
        """Whether the facts settled it either way."""
        return self.truth is not Truth.UNKNOWN


TRUE: Evaluation = Evaluation(Truth.TRUE)
FALSE: Evaluation = Evaluation(Truth.FALSE)


def unknown(reason: str) -> Evaluation:
    """An undecided verdict, with the reason it could not be decided.

    Args:
        reason: What was missing or unusable, as a phrase a person can act on.

    Returns:
        The evaluation.
    """
    return Evaluation(Truth.UNKNOWN, reason)


@dataclass(frozen=True, slots=True)
class Constant:
    """A literal: a number, a boolean, or a quoted string."""

    value: Decimal | bool | str


@dataclass(frozen=True, slots=True)
class Identifier:
    """A name the world state is asked to bind to a value."""

    name: str


@dataclass(frozen=True, slots=True)
class Arithmetic:
    """Two terms combined by one operator, over `Decimal`."""

    op: ArithOp
    left: Term
    right: Term


type Term = Constant | Identifier | Arithmetic
"""Anything that denotes a value. Not a predicate on its own."""


@dataclass(frozen=True, slots=True)
class Comparison:
    """Two terms and the relation asserted between them. The atom of a predicate."""

    op: CompareOp
    left: Term
    right: Term


@dataclass(frozen=True, slots=True)
class Negation:
    """`not` applied to a formula."""

    operand: Formula


@dataclass(frozen=True, slots=True)
class Conjunction:
    """Two formulas joined by `and`."""

    left: Formula
    right: Formula


@dataclass(frozen=True, slots=True)
class Disjunction:
    """Two formulas joined by `or`."""

    left: Formula
    right: Formula


type Formula = Comparison | Negation | Conjunction | Disjunction
"""What a predicate must be. A `Term` at the top is a quantity, not a claim."""


def identifiers_in(node: Formula | Term) -> frozenset[str]:
    """Every identifier a formula or term mentions.

    The blocking key `ContradictionDetector` buckets on, and the set
    `AssumptionMonitor` asks the world state for before evaluating. Both need it
    without evaluating anything, which is why it is a walk over the tree rather
    than a side effect of evaluation.

    Args:
        node: Any node in the tree.

    Returns:
        The names, deduplicated. Empty for a predicate over literals alone.
    """
    match node:
        case Identifier(name=name):
            return frozenset({name})
        case Constant():
            return frozenset()
        case Arithmetic(left=left, right=right) | Comparison(left=left, right=right):
            return identifiers_in(left) | identifiers_in(right)
        case Negation(operand=operand):
            return identifiers_in(operand)
        case Conjunction(left=formula, right=other) | Disjunction(left=formula, right=other):
            return identifiers_in(formula) | identifiers_in(other)


_BINDING: dict[type, int] = {
    Disjunction: 1,
    Conjunction: 2,
    Negation: 3,
    Comparison: 4,
    Arithmetic: 5,
    Constant: 7,
    Identifier: 7,
}
"""How tightly each node binds, for deciding where a bracket is needed.

`Arithmetic` is one entry rather than two because `_binding` reads the operator
off the node -- `*` and `/` bind tighter than `+` and `-`, and putting that in the
table would mean a type keyed twice.
"""


def _binding(node: Formula | Term) -> int:
    """The precedence level of one node."""
    if isinstance(node, Arithmetic) and node.op in {ArithOp.MULTIPLY, ArithOp.DIVIDE}:
        return 6
    return _BINDING[type(node)]


def render(node: Formula | Term) -> str:
    """Write a tree back out as text, bracketed only where it has to be.

    The inverse of the parser, and it exists for two callers rather than for
    debugging. `AssumptionFormalizer` stores the *rendered* predicate rather than
    the model's spelling of it, so `x<=50` and `x <= 50` become one string and two
    assumptions saying the same thing block into the same bucket. And a round trip
    -- parse, render, parse -- is the cheapest property test there is for a
    grammar, which is how the parser's tests are written.

    Args:
        node: Any node in the tree.

    Returns:
        Text that parses back to an equal tree.
    """
    match node:
        case Constant(value=value):
            return _constant(value)
        case Identifier(name=name):
            return name
        case Negation(operand=operand):
            return f"not {_bracketed(operand, _binding(node))}"
        case Arithmetic() | Comparison() | Conjunction() | Disjunction():
            return _infix(node)


type _Binary = Arithmetic | Comparison | Conjunction | Disjunction
"""The four nodes with a left and a right, so rendering them is one function."""

_WORD: dict[type, str] = {Conjunction: "and", Disjunction: "or"}
"""The operators spelled as words rather than carried on an enum member."""


def _operator(node: _Binary) -> str:
    """How one binary node's operator is written."""
    if isinstance(node, Arithmetic | Comparison):
        return node.op.value
    return _WORD[type(node)]


def _infix(node: _Binary) -> str:
    """One binary node, bracketing each side only if its own binding is looser.

    The right-hand side is bracketed one level earlier than the left, which is
    what keeps `a - (b - c)` from rendering as `a - b - c` and meaning something
    else on the way back in.
    """
    level = _binding(node)
    return f"{_bracketed(node.left, level)} {_operator(node)} {_bracketed(node.right, level + 1)}"


def _bracketed(node: Formula | Term, needed: int) -> str:
    """Render a child, in brackets when it binds looser than its parent allows."""
    text = render(node)
    return f"({text})" if _binding(node) < needed else text


def _constant(value: Decimal | bool | str) -> str:
    """A literal in the spelling the lexer reads back.

    `bool` is tested first because it is the narrower claim: `True` is an `int`
    in Python and the ordering of these branches is the only thing stopping a
    boolean from rendering as a number.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return str(value)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
