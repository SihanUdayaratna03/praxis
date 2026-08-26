"""Predicate text to tokens. The only place the grammar's spelling lives.

Kept apart from the parser so that "what characters are legal" and "what order
they may come in" fail separately and report separately. A model asked for a
predicate gets one of those wrong far more often than the other, and telling an
author `=` should be `==` is worth more than telling them the predicate is invalid.

Three details are decisions rather than defaults.

**Digit separators are legal.** `edge_count < 1_000_000` is written that way in two
of this project's own ADRs, and a grammar that cannot read the predicates already
in the repository would fail ADR 0001's first assumption by being too strict rather
than by being wrong.

**Keywords are matched case-insensitively, identifiers are not.** `AND` from a model
is the same operator as `and`; `Index_Size_Gb` and `index_size_gb` are two different
quantities and collapsing them would silently merge two assumptions. Reading is
permissive, writing is not -- `AssumptionFormalizer` renders in one style.

**A bare `=` is refused by name.** It is the single most likely thing to come back
from a model asked for an expression, and reporting it as "unexpected character"
would waste the one line an author reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from praxis.predicates.errors import PredicateSyntaxError


class TokenKind(StrEnum):
    """What one token is.

    The member value is how the token is *named in an error message*, not how it
    is spelled, because that is the only thing this enum's value is read for.
    """

    IDENTIFIER = "a name"
    NUMBER = "a number"
    STRING = "a quoted string"
    BOOLEAN = "true or false"
    COMPARE = "a comparison"
    ARITH = "an arithmetic operator"
    AND = "'and'"
    OR = "'or'"
    NOT = "'not'"
    BETWEEN = "'between'"
    OPEN = "'('"
    CLOSE = "')'"
    END = "the end of the predicate"


@dataclass(frozen=True, slots=True)
class Token:
    """One lexeme, where it was, and the value it denotes if it denotes one.

    Attributes:
        kind: What it is.
        text: Exactly the characters consumed, so an error can quote them.
        position: Index of the first character in the source.
        literal: The value, for the three kinds that carry one. `None`
            everywhere else, which is why the parser reads it only after
            matching on `kind`.
    """

    kind: TokenKind
    text: str
    position: int
    literal: Decimal | bool | str | None = None


_KEYWORDS: Final[dict[str, TokenKind]] = {
    "and": TokenKind.AND,
    "or": TokenKind.OR,
    "not": TokenKind.NOT,
    "between": TokenKind.BETWEEN,
}

_BOOLEANS: Final[dict[str, bool]] = {"true": True, "false": False}

_ESCAPES: Final[dict[str, str]] = {"\\": "\\", '"': '"', "n": "\n", "t": "\t"}
"""Escapes a quoted string understands. Anything else after a backslash is the
character itself, because an expiry condition quoting a Windows path is a more
likely thing to meet than an author who meant `\\q` to mean something."""

_SCANNER: Final = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<number>\d(?:[\d_]*\d)?(?:\.\d(?:[\d_]*\d)?)?)
    | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<string>"(?:[^"\\]|\\.)*")
    | (?P<compare><=|>=|==|!=|<|>)
    | (?P<arith>[+\-*/])
    | (?P<open>\()
    | (?P<close>\))
    """,
    re.VERBOSE,
)
"""One pass, longest-alternative-first where it matters.

`compare` precedes `arith` so that `<=` is one token rather than `<` followed by
something that is not an operator, and `number` precedes `word` so a digit can
never start a name.
"""


def tokenize(source: str) -> tuple[Token, ...]:
    """Read a predicate into tokens, ending with `END`.

    Args:
        source: The predicate as written.

    Returns:
        Every token in order, with an `END` token last so the parser never has to
        check for the end of a list separately from checking what comes next.

    Raises:
        PredicateSyntaxError: on a character no rule claims, an unterminated
            string, or a number this build cannot represent.
    """
    tokens: list[Token] = []
    position = 0
    while position < len(source):
        found = _SCANNER.match(source, position)
        if found is None:
            raise _unexpected(source, position)
        name = found.lastgroup
        text = found.group()
        if name != "space":
            tokens.append(_token(str(name), text, position, source))
        position = found.end()
    tokens.append(Token(kind=TokenKind.END, text="", position=len(source)))
    return tuple(tokens)


def _token(name: str, text: str, position: int, source: str) -> Token:
    """Build one token from the group that matched."""
    if name == "number":
        return Token(TokenKind.NUMBER, text, position, _number(text, position, source))
    if name == "word":
        return _word(text, position)
    if name == "string":
        return Token(TokenKind.STRING, text, position, _unescape(text))
    if name == "compare":
        return Token(TokenKind.COMPARE, text, position)
    if name == "arith":
        return Token(TokenKind.ARITH, text, position)
    return Token(TokenKind.OPEN if name == "open" else TokenKind.CLOSE, text, position)


def _word(text: str, position: int) -> Token:
    """A run of name characters: a keyword, a boolean, or an identifier."""
    folded = text.casefold()
    if folded in _KEYWORDS:
        return Token(_KEYWORDS[folded], text, position)
    if folded in _BOOLEANS:
        return Token(TokenKind.BOOLEAN, text, position, _BOOLEANS[folded])
    return Token(TokenKind.IDENTIFIER, text, position, text)


def _number(text: str, position: int, source: str) -> Decimal:
    """A numeric literal as `Decimal`, never as float.

    Invariant 4 reaches the grammar. A predicate over money or a rate is compared
    against a stored `Decimal`, and parsing `0.1` through binary floating point
    would make `cost_per_document_usd <= 0.1` answer differently depending on how
    the same number arrived.
    """
    try:
        return Decimal(text.replace("_", ""))
    except InvalidOperation as exc:  # pragma: no cover -- the scanner's shape rules it out
        message = f"{text!r} is not a number this build can represent"
        raise PredicateSyntaxError(source, position, message) from exc


def _unescape(text: str) -> str:
    """The content of a quoted string, with its escapes resolved."""
    body = text[1:-1]
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            following = body[index + 1]
            out.append(_ESCAPES.get(following, following))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _unexpected(source: str, position: int) -> PredicateSyntaxError:
    """Name the character nothing claimed, as specifically as it can be named."""
    char = source[position]
    if char == '"':
        return PredicateSyntaxError(source, position, "a quoted string is never closed")
    if char == "=":
        return PredicateSyntaxError(source, position, "equality is written '==', not '='")
    return PredicateSyntaxError(source, position, f"{char!r} has no meaning in a predicate")
