"""The deterministic grid a segmenter is allowed to group, and nothing finer.

Segmentation is two decisions, and this module owns the one that must never be
a model's: **where a document can be cut.** `SegmenterAgent` owns the other --
which of these blocks belong together -- and it answers in block indices. That
split is the whole of ADR 0011, in `docs/adr/`: a byte offset is never a
model's to invent, so a fabricated one is not a thing this pipeline can emit.

Two properties everything above depends on, both property-tested:

- **The grid covers the document.** Every non-whitespace character lies inside
  exactly one block. Nothing can be dropped by a segmenter that never saw it,
  and the union of the blocks is a lossless view of the source.
- **A block's text is a slice of its own document.** `text` is not rebuilt from
  the lines it was scanned out of; it is the bytes between the offsets. A
  reconstruction that differed by one space would put that space into every
  span cut from it.

One rule set serves all three source kinds. The markdown rules degrade
correctly on the other two: a plain-text status update written with bullets
gets those bullets as blocks, and the JSON adapter's `path: value` rendering is
already blank-line separated, so each leaf lands as one block. A second rule
set per kind would be three code paths to keep in agreement for a difference
nobody could point at in the output.

Setext headings (`Title` over `=====`) are not recognised. The underline joins
the paragraph, which costs a heading being grouped as prose and costs nothing
in offsets -- and the corpus this is written for is ATX throughout.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from praxis.domain.records import Document

_ATX_HEADING: Final = re.compile(r"^ {0,3}#{1,6}\s+\S")
_FENCE: Final = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
_LIST_ITEM: Final = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])\s+\S")


class BlockKind(StrEnum):
    """What kind of thing a block is, as the grid recognised it.

    Carried into the segmenter's prompt: "heading" and "table" tell a model
    something about how a run of blocks relates that the text alone does not,
    and it is free information -- the scanner already knew.
    """

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    """Consecutive pipe-delimited rows, held together as one block.

    Deliberately not one block per row. The grid is also the degradation floor,
    and a floor that emits a table row without its header emits a span that
    means nothing on its own."""

    QUOTE = "quote"
    CODE = "code"
    """A fenced block, never split. Its contents are not prose and cutting it
    in half produces two spans that are each syntactically nothing."""


@dataclass(frozen=True, slots=True)
class Block:
    """One addressable unit of a document, with its exact byte range.

    Attributes:
        index: Position in the grid, from 0. What a segmenter refers to.
        kind: What the scanner recognised it as.
        start_byte: First byte, inclusive.
        end_byte: Last byte, exclusive.
        text: Exactly `document.content_bytes[start_byte:end_byte]`, decoded.
    """

    index: int
    kind: BlockKind
    start_byte: int
    end_byte: int
    text: str


def blocks_in(document: Document) -> tuple[Block, ...]:
    """Cut a document into the grid a segmenter may group."""
    return blocks_of(document.content)


def blocks_of(text: str) -> tuple[Block, ...]:
    """Cut normalised text into blocks, with byte offsets into that text.

    Args:
        text: Normalised content, as `praxis.ingest.adapters` produced it.
            Line endings are assumed to be LF; anything else is scanned as
            ordinary characters rather than as a break.

    Returns:
        The blocks, in document order, non-overlapping, together covering every
        non-whitespace character exactly once.
    """
    return _Scanner(text).run()


@dataclass(frozen=True, slots=True)
class _Line:
    """One line of the source, with where it starts in characters and bytes."""

    char_start: int
    char_end: int
    byte_start: int
    text: str

    @property
    def blank(self) -> bool:
        """Whether this line separates blocks rather than belonging to one."""
        return not self.text.strip()


class _Scanner:
    """One document's worth of scanning: lines in, blocks out."""

    def __init__(self, text: str) -> None:
        """Index the text by line, recording byte offsets as it goes."""
        self._text = text
        self._lines = _lines_of(text)

    def run(self) -> tuple[Block, ...]:
        """Walk every line, assigning each non-blank one to exactly one block."""
        blocks: list[Block] = []
        index = 0
        while index < len(self._lines):
            if self._lines[index].blank:
                index += 1
                continue
            kind, last = self._extent(index)
            block = self._build(len(blocks), kind, index, last)
            if block is not None:
                blocks.append(block)
            index = last + 1
        return tuple(blocks)

    def _extent(self, first: int) -> tuple[BlockKind, int]:
        """Classify the block starting at `first` and find its last line."""
        line = self._lines[first].text
        if _FENCE.match(line):
            return BlockKind.CODE, self._fence_end(first)
        if _ATX_HEADING.match(line):
            return BlockKind.HEADING, first
        if _is_table_row(line):
            return BlockKind.TABLE, self._run_of(first, _is_table_row)
        if _is_quote(line):
            return BlockKind.QUOTE, self._run_of(first, _is_quote)
        if _LIST_ITEM.match(line):
            return BlockKind.LIST_ITEM, self._until_new_block(first)
        return BlockKind.PARAGRAPH, self._until_new_block(first)

    def _fence_end(self, first: int) -> int:
        """Find the closing fence, or the end of the document without one.

        An unclosed fence swallowing the rest of the document is the correct
        reading: that is what a markdown renderer does with it, and guessing
        otherwise would put the same text in two blocks on two different runs
        depending on where the guess landed.
        """
        match = _FENCE.match(self._lines[first].text)
        assert match is not None  # noqa: S101 -- the caller matched it already
        marker = match.group("fence")
        for index in range(first + 1, len(self._lines)):
            closing = _FENCE.match(self._lines[index].text)
            if closing is not None and closing.group("fence")[0] == marker[0]:
                return index
        return len(self._lines) - 1

    def _run_of(self, first: int, matches: Callable[[str], bool]) -> int:
        """Extend while consecutive lines keep matching the same predicate."""
        last = first
        while last + 1 < len(self._lines) and matches(self._lines[last + 1].text):
            last += 1
        return last

    def _until_new_block(self, first: int) -> int:
        """Extend over continuation lines: anything that starts nothing new.

        This is what keeps a wrapped bullet or a two-line paragraph together.
        A line that begins a heading, a fence, a table, a quote or a fresh list
        item ends the current block instead of joining it.
        """
        last = first
        while last + 1 < len(self._lines):
            following = self._lines[last + 1]
            if following.blank or _starts_a_block(following.text):
                break
            last += 1
        return last

    def _build(self, index: int, kind: BlockKind, first: int, last: int) -> Block | None:
        """Cut the block's exact byte range, trimmed of surrounding whitespace.

        Trimmed on both sides so that a span built from a run of blocks quotes
        text rather than the newline before it. The offsets move with the trim;
        the text is then sliced from the document, never rebuilt from the lines
        -- a reconstruction differing by one space would put that space in
        every span cut from this block.
        """
        raw = self._text[self._lines[first].char_start : self._lines[last].char_end]
        body = raw.strip()
        if not body:
            return None
        lead = len(raw) - len(raw.lstrip())
        start_byte = self._lines[first].byte_start + len(raw[:lead].encode("utf-8"))
        return Block(
            index=index,
            kind=kind,
            start_byte=start_byte,
            end_byte=start_byte + len(body.encode("utf-8")),
            text=body,
        )


def _lines_of(text: str) -> tuple[_Line, ...]:
    """Split on LF, recording each line's character and byte start.

    Byte offsets are accumulated rather than recomputed per line, which keeps
    this linear -- `len(text[:i].encode())` per line is quadratic, and the
    corpus is thousands of documents.
    """
    lines: list[_Line] = []
    char_start = 0
    byte_start = 0
    for piece in text.split("\n"):
        lines.append(
            _Line(
                char_start=char_start,
                char_end=char_start + len(piece),
                byte_start=byte_start,
                text=piece,
            )
        )
        char_start += len(piece) + 1
        byte_start += len(piece.encode("utf-8")) + 1
    return tuple(lines)


def _starts_a_block(line: str) -> bool:
    """Whether a line begins something new rather than continuing a block."""
    return bool(
        _ATX_HEADING.match(line)
        or _FENCE.match(line)
        or _LIST_ITEM.match(line)
        or _is_table_row(line)
        or _is_quote(line)
    )


def _is_table_row(line: str) -> bool:
    """Whether a line is a row of a pipe table."""
    return line.strip().startswith("|")


def _is_quote(line: str) -> bool:
    """Whether a line is part of a block quote."""
    return line.strip().startswith(">")
