"""Tokens to a tree, or a stated reason the text is not a predicate.

Recursive descent, no generator, no table. The grammar is small enough to read in
one screen and the error messages are half the value of the module, which is the
argument against a parser generator here rather than a general one.

```
formula     := disjunction
disjunction := conjunction ("or" conjunction)*
conjunction := negation ("and" negation)*
negation    := "not" negation | comparison
comparison  := "(" disjunction ")"                     -- when it is a formula
             | sum COMPARE sum
             | sum "between" sum "and" sum
sum         := product (("+" | "-") product)*
product     := unary (("*" | "/") unary)*
unary       := ("-" | "+") unary | atom
atom        := NUMBER | STRING | BOOLEAN | IDENTIFIER | "(" sum ")"
```

Two things in that grammar are decisions.

**A bracket is ambiguous and is resolved by looking ahead, not by a rule.**
`(a + 1) <= 5` opens an arithmetic group and `(a <= 1) and b == 2` opens a
formula, and nothing about the `(` says which. The parser tries the formula
reading first and takes it only if what follows the closing bracket could not
continue an expression; otherwise it rewinds. Predicates are one line long, so a
bounded rewind costs nothing, and the alternative -- forbidding brackets around a
formula -- would reject `not (a == 1 or b == 2)`.

**`between` is sugar and does not survive rendering.** `commits_per_phase between
8 and 20` is written that way in this project's own ADRs, and it desugars to the
conjunction it means. Keeping it as a node would mean a second shape for the
interval arithmetic in `praxis.predicates.intervals` to understand, for a form
that adds nothing once parsed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from praxis.predicates.ast import (
    Arithmetic,
    ArithOp,
    CompareOp,
    Comparison,
    Conjunction,
    Constant,
    Disjunction,
    Formula,
    Identifier,
    Negation,
    Term,
)
from praxis.predicates.errors import PredicateSyntaxError
from praxis.predicates.lexer import Token, TokenKind, tokenize

_SUM_OPS: Final = {"+", "-"}
_PRODUCT_OPS: Final = {"*", "/"}

_LITERAL_KINDS: Final = frozenset({TokenKind.NUMBER, TokenKind.STRING, TokenKind.BOOLEAN})
"""The token kinds that carry a value on `Token.literal`."""

_CONTINUES_AN_EXPRESSION: Final = frozenset({TokenKind.COMPARE, TokenKind.ARITH, TokenKind.BETWEEN})
"""What may follow a closing bracket that opened an arithmetic group.

Seeing any of these after `( ... )` means the brackets were arithmetic, however
much the contents looked like a formula.
"""


def parse(source: str) -> Formula:
    """Read a predicate into a tree.

    Args:
        source: The predicate as written.

    Returns:
        The formula it denotes.

    Raises:
        PredicateSyntaxError: if it is not a predicate this grammar expresses.
            Never returns a partial tree -- a predicate that half-parses is one
            whose meaning nobody knows.
    """
    tokens = tokenize(source)
    if tokens[0].kind is TokenKind.END:
        raise PredicateSyntaxError(source, 0, "there is no predicate here")
    parser = _Parser(source, tokens)
    formula = parser.formula()
    parser.expect(TokenKind.END)
    return formula


def parses(source: str) -> bool:
    """Whether a predicate parses, without caring why it does not.

    The predicate behind ADR 0001's first assumption --
    `adr_predicates_parsed / adr_predicates_total >= 0.9` -- is a count over this
    function, so it exists as one rather than as a `try` block in three callers.

    Args:
        source: The predicate as written.

    Returns:
        True if it is a predicate this grammar expresses.
    """
    try:
        parse(source)
    except PredicateSyntaxError:
        return False
    return True


class _Parser:
    """One pass over one token list, with the index it has reached."""

    def __init__(self, source: str, tokens: tuple[Token, ...]) -> None:
        """Hold the text for error messages and the tokens for reading.

        Args:
            source: The predicate as written.
            tokens: Its tokens, ending with `END`.
        """
        self._source = source
        self._tokens = tokens
        self._index = 0

    @property
    def current(self) -> Token:
        """The token about to be read. Always exists -- the list ends with `END`."""
        return self._tokens[self._index]

    def advance(self) -> Token:
        """Consume the current token and return it."""
        token = self.current
        if token.kind is not TokenKind.END:
            self._index += 1
        return token

    def expect(self, kind: TokenKind, *, instead: str | None = None) -> Token:
        """Consume the current token, or say what was wanted in its place.

        Args:
            kind: What must be here.
            instead: A better complaint than the generic one, where the caller
                knows why this particular token matters.

        Returns:
            The consumed token.

        Raises:
            PredicateSyntaxError: if the current token is not of that kind.
        """
        if self.current.kind is not kind:
            problem = instead or f"expected {kind.value} and found {self.current.kind.value}"
            raise self.error(problem)
        return self.advance()

    def error(self, problem: str) -> PredicateSyntaxError:
        """A syntax error pointing at the token the parser is looking at."""
        return PredicateSyntaxError(self._source, self.current.position, problem)

    def formula(self) -> Formula:
        """The whole predicate: the loosest-binding rule."""
        return self._disjunction()

    def _disjunction(self) -> Formula:
        """One or more conjunctions joined by `or`, left-associated."""
        formula = self._conjunction()
        while self.current.kind is TokenKind.OR:
            self.advance()
            formula = Disjunction(left=formula, right=self._conjunction())
        return formula

    def _conjunction(self) -> Formula:
        """One or more negations joined by `and`, left-associated."""
        formula = self._negation()
        while self.current.kind is TokenKind.AND:
            self.advance()
            formula = Conjunction(left=formula, right=self._negation())
        return formula

    def _negation(self) -> Formula:
        """`not` binds tighter than `and`, so it applies to one comparison."""
        if self.current.kind is TokenKind.NOT:
            self.advance()
            return Negation(operand=self._negation())
        return self._comparison()

    def _comparison(self) -> Formula:
        """The atom of a predicate: two quantities and a relation between them."""
        if self.current.kind is TokenKind.OPEN:
            grouped = self._grouped_formula()
            if grouped is not None:
                return grouped
        left = self._sum()
        if self.current.kind is TokenKind.BETWEEN:
            return self._between(left)
        operator = self.expect(
            TokenKind.COMPARE,
            instead=("a predicate has to compare something, and this one stops after a quantity"),
        )
        return Comparison(op=CompareOp(operator.text), left=left, right=self._sum())

    def _grouped_formula(self) -> Formula | None:
        """Read `( formula )`, or rewind and report that these were arithmetic.

        The lookahead after the closing bracket is what decides. `(a) <= 5` and
        `(a <= 5) and b` both start identically, and only the token after the `)`
        tells them apart.
        """
        mark = self._index
        self.advance()
        try:
            inner = self._disjunction()
            self.expect(TokenKind.CLOSE)
        except PredicateSyntaxError:
            self._index = mark
            return None
        if self.current.kind in _CONTINUES_AN_EXPRESSION:
            self._index = mark
            return None
        return inner

    def _between(self, subject: Term) -> Formula:
        """`x between a and b`, desugared to the conjunction it means."""
        self.advance()
        lower = self._sum()
        self.expect(
            TokenKind.AND,
            instead="'between' names two bounds, so it needs 'and' between them",
        )
        upper = self._sum()
        return Conjunction(
            left=Comparison(op=CompareOp.GE, left=subject, right=lower),
            right=Comparison(op=CompareOp.LE, left=subject, right=upper),
        )

    def _sum(self) -> Term:
        """Addition and subtraction, left-associated."""
        term = self._product()
        while self.current.kind is TokenKind.ARITH and self.current.text in _SUM_OPS:
            operator = self.advance()
            term = Arithmetic(op=ArithOp(operator.text), left=term, right=self._product())
        return term

    def _product(self) -> Term:
        """Multiplication and division, binding tighter than addition."""
        term = self._unary()
        while self.current.kind is TokenKind.ARITH and self.current.text in _PRODUCT_OPS:
            operator = self.advance()
            term = Arithmetic(op=ArithOp(operator.text), left=term, right=self._unary())
        return term

    def _unary(self) -> Term:
        """A leading sign.

        A sign on a numeric literal is folded into the literal, so `-5` renders as
        `-5` rather than as `0 - 5`. On anything else it becomes a subtraction
        from zero, because `-x` where `x` is a string is a type error the
        evaluator should report rather than a shape the parser should refuse.
        """
        if not (self.current.kind is TokenKind.ARITH and self.current.text in _SUM_OPS):
            return self._atom()
        sign = self.advance()
        operand = self._unary()
        if sign.text == "+":
            return operand
        if isinstance(operand, Constant) and isinstance(operand.value, Decimal):
            return Constant(value=-operand.value)
        return Arithmetic(op=ArithOp.SUBTRACT, left=Constant(value=Decimal(0)), right=operand)

    def _atom(self) -> Term:
        """A literal, a name, or a bracketed expression."""
        token = self.current
        if token.kind is TokenKind.OPEN:
            self.advance()
            inner = self._sum()
            self.expect(TokenKind.CLOSE)
            return inner
        if token.kind is TokenKind.IDENTIFIER:
            self.advance()
            return Identifier(name=token.text)
        # `literal` is set for exactly these three kinds. Testing it alongside the
        # kind narrows the type without an assertion that `-O` would strip, and
        # the impossible case falls through to the same error as any other token.
        if token.kind in _LITERAL_KINDS and token.literal is not None:
            self.advance()
            return Constant(value=token.literal)
        raise self.error(f"expected a quantity and found {token.kind.value}")
