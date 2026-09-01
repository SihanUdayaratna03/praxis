"""Reading the two corpora the demo is built from, with real byte offsets.

Offsets matter more here than anywhere else in this package. Invariant 6 says
every claim carries a `span_id` that really contains it, so nothing is seeded
from a string that was assembled in Python -- each record points at the bytes
of the file it came from.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

TEMPLATE_NAME: Final = "template.md"
"""A form, not an ADR. Excluded, the same way `praxis.eval.adrs` excludes it."""

_ASSUMPTION_ROW: Final = re.compile(
    r"^\|\s*\d+\s*\|(?P<claim>[^|]*)\|(?P<predicate>[^|]*)\|(?P<expiry>[^|]*)\|\s*$",
    re.MULTILINE,
)
"""A numbered assumption row. Anchored on the integer so the header, the
separator and the rejected-options table cannot be read as assumptions."""

_TITLE: Final = re.compile(r"^#\s*\d+\s*[-—]\s*(?P<title>.+)$", re.MULTILINE)
_FIELD: Final = re.compile(r"^(?P<key>\w+):\s*(?P<value>.+)$", re.MULTILINE)
_REJECTED_ROW: Final = re.compile(
    r"^\|\s*(?P<option>[^|]+?)\s*\|\s*(?P<reason>[^|]+?)\s*\|\s*$", re.MULTILINE
)
_CODE: Final = re.compile(r"`([^`]*)`")


def _unquote(cell: str) -> str:
    """A table cell, with its backticks and padding removed."""
    stripped = cell.strip()
    found = _CODE.search(stripped)
    return (found.group(1) if found else stripped).strip()


@dataclass(frozen=True, slots=True)
class Quoted:
    """One piece of text and where in its file it actually is.

    Attributes:
        text: The bytes, decoded.
        start: Byte offset of the first byte.
        end: Byte offset one past the last.
    """

    text: str
    start: int
    end: int


def locate(content: str, needle: str) -> Quoted | None:
    """Find `needle` in `content` and return it with byte offsets.

    Byte offsets rather than character ones, because `Span` is defined over
    bytes and an ADR with an em dash in it would otherwise be off by one.
    """
    index = content.find(needle)
    if index < 0:
        return None
    start = len(content[:index].encode("utf-8"))
    return Quoted(text=needle, start=start, end=start + len(needle.encode("utf-8")))


@dataclass(frozen=True, slots=True)
class AdrAssumption:
    """One row of an ADR's assumption table."""

    claim: str
    predicate: str
    expiry: str
    quoted: Quoted


@dataclass(frozen=True, slots=True)
class Adr:
    """One architecture decision record, as the seeder reads it."""

    path: Path
    number: str
    title: str
    status: str
    date: str
    decision_maker: str
    impact: str
    content: str
    chosen: Quoted
    rejected: tuple[tuple[str, str], ...]
    assumptions: tuple[AdrAssumption, ...]


def read_adrs(directory: Path) -> tuple[Adr, ...]:
    """Every ADR in a directory, in number order."""
    return tuple(
        adr
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]-*.md"))
        if path.name != TEMPLATE_NAME and (adr := _read_adr(path)) is not None
    )


def _read_adr(path: Path) -> Adr | None:
    """One ADR, or `None` if it has no title or no chosen paragraph."""
    content = path.read_text(encoding="utf-8")
    title = _TITLE.search(content)
    chosen = _chosen(content)
    if title is None or chosen is None:
        return None
    fields = {m.group("key"): m.group("value").strip() for m in _FIELD.finditer(content)}
    return Adr(
        path=path,
        number=path.name[:4],
        title=title.group("title").strip(),
        status=fields.get("status", "accepted"),
        date=fields.get("date", ""),
        decision_maker=fields.get("decision_maker", "Sihan Udayaratna"),
        impact=fields.get("impact", "medium"),
        content=content,
        chosen=chosen,
        rejected=_rejected(content),
        assumptions=_assumptions(content),
    )


def _chosen(content: str) -> Quoted | None:
    """The first paragraph under `## Chosen`, quoted from the file."""
    marker = "## Chosen"
    start = content.find(marker)
    if start < 0:
        return None
    body = content[start + len(marker) :].lstrip("\n")
    paragraph = body.split("\n\n", 1)[0].strip()
    return locate(content, paragraph) if paragraph else None


def _rejected(content: str) -> tuple[tuple[str, str], ...]:
    """The rejected-options table, between `## Rejected` and the next heading."""
    start = content.find("## Rejected")
    if start < 0:
        return ()
    section = content[start:]
    end = section.find("\n## ", 3)
    rows = [
        (_unquote(m.group("option")), m.group("reason").strip())
        for m in _REJECTED_ROW.finditer(section if end < 0 else section[:end])
    ]
    # The header and its separator match the same shape; neither is an option.
    return tuple(
        (option, reason)
        for option, reason in rows
        if option and reason and option != "Option" and not set(option) <= {"-", " "}
    )


def _assumptions(content: str) -> tuple[AdrAssumption, ...]:
    """Every numbered assumption row, with the row's own offsets."""
    found = []
    for match in _ASSUMPTION_ROW.finditer(content):
        predicate = _unquote(match.group("predicate"))
        claim = match.group("claim").strip()
        if not predicate or not claim:
            continue
        quoted = locate(content, match.group(0).strip())
        if quoted is None:  # pragma: no cover - the row came from this content
            continue
        found.append(
            AdrAssumption(
                claim=claim,
                predicate=predicate,
                expiry=_unquote(match.group("expiry")),
                quoted=quoted,
            )
        )
    return tuple(found)


@dataclass(frozen=True, slots=True)
class DogfoodRow:
    """One line of `estimates.jsonl` or `outcomes.jsonl`, with its offsets."""

    data: dict[str, Any]
    quoted: Quoted

    @property
    def id(self) -> str:
        """The record's own id."""
        return str(self.data["id"])


def read_jsonl(path: Path) -> tuple[str, tuple[DogfoodRow, ...]]:
    """Read a JSONL file and return its text beside one row per line.

    Each row is located in the file it came from, so the span a seeded record
    cites is the line a person can open and read.
    """
    content = path.read_text(encoding="utf-8")
    rows = []
    for line in content.splitlines():
        if not line.strip():
            continue
        quoted = locate(content, line)
        if quoted is None:  # pragma: no cover - the line came from this content
            continue
        rows.append(DogfoodRow(data=json.loads(line), quoted=quoted))
    return content, tuple(rows)


def quantity(value: object) -> Decimal | None:
    """A dogfood quantity as an exact `Decimal`, or `None`.

    Through `str` because these arrive from JSON as floats, and invariant 4
    does not want `Decimal(3.1)` and its eighteen trailing digits.
    """
    return None if value is None else Decimal(str(value))
