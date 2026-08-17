"""Assembling a document while recording where everything landed.

This is the mechanism ADR 0012 is about. A document is built as a list of
blocks, and appending a block returns its exact byte range in the finished
text -- so the answer key is written at the moment the text is, and never by
searching the finished document for a phrase.

The shortcut matters more than it looks. A generator drawing from fixed phrase
menus repeats phrases constantly: two topics both say "of hands-on work", four
documents open the same way. Locating ground truth by search would silently
label the wrong occurrence, and nothing downstream could tell -- the offsets
would resolve, the quotation would match, and the grading would be wrong about
which sentence it was grading.

Blocks are joined with `BLOCK_SEPARATOR` and the text ends with a newline,
which is exactly what the JSON adapter's rendering does. That is not a
coincidence being relied on: the JSON template builds its blocks by calling the
adapter's own `render_leaf`, so the two agree by construction rather than by
this module imitating it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from praxis.corpus.groundtruth import ExpectedField, ExpectedLink, GroundTruthItem, ItemKind
from praxis.ingest.adapters import BLOCK_SEPARATOR

_SEPARATOR_BYTES: Final = len(BLOCK_SEPARATOR.encode("utf-8"))


class Ids:
    """Allocates item ids across a whole corpus.

    Corpus-wide rather than per document, because an edge names its target by
    id and the fusion edge this corpus exists to demonstrate has no reason to
    stay inside one file.
    """

    def __init__(self) -> None:
        """Start before the first id."""
        self._issued = 0

    def next_id(self) -> str:
        """Return the next id, `GT-0001` onwards."""
        self._issued += 1
        return f"GT-{self._issued:04d}"


@dataclass(frozen=True, slots=True)
class Placed:
    """One block of text and the byte range it occupies in its document."""

    text: str
    start_byte: int
    end_byte: int


@dataclass
class Draft:
    """A document under construction, and the answers it is known to contain."""

    ids: Ids
    blocks: list[str] = field(default_factory=list)
    items: list[GroundTruthItem] = field(default_factory=list)
    _cursor: int = 0

    def block(self, text: str) -> Placed:
        """Append a block, returning where it landed.

        The cursor advances past the separator that will follow this block.
        After the last block that over-counts by one separator, which is
        harmless: the cursor is only ever read as the *start* of the next
        block, and there is no block after the last one.
        """
        start = self._cursor
        width = len(text.encode("utf-8"))
        self.blocks.append(text)
        self._cursor = start + width + _SEPARATOR_BYTES
        return Placed(text=text, start_byte=start, end_byte=start + width)

    def text(self) -> str:
        """The finished document."""
        return BLOCK_SEPARATOR.join(self.blocks) + "\n"

    def record(  # noqa: PLR0913 -- an answer-key entry has this many parts
        self,
        kind: ItemKind,
        placed: Placed,
        *,
        fields: tuple[ExpectedField, ...] = (),
        links: tuple[ExpectedLink, ...] = (),
        resolves_item_id: str | None = None,
        note: str = "",
    ) -> str:
        """Record that a block is an instance of something, and return its id.

        The quotation comes from the block that was appended, so the key cannot
        disagree with the document about what is written there.
        """
        item_id = self.ids.next_id()
        self.items.append(
            GroundTruthItem(
                item_id=item_id,
                kind=kind,
                start_byte=placed.start_byte,
                end_byte=placed.end_byte,
                quote=placed.text,
                fields=fields,
                links=links,
                resolves_item_id=resolves_item_id,
                note=note,
            )
        )
        return item_id

    def distractor(self, kind: ItemKind, placed: Placed, note: str) -> str:
        """Record a block that looks extractable and must not be extracted.

        A separate method rather than a flag, because a distractor carrying
        expected fields is a contradiction the format refuses -- and the way
        that mistake happens is a caller passing the flag and the fields
        together without noticing.
        """
        item_id = self.ids.next_id()
        self.items.append(
            GroundTruthItem(
                item_id=item_id,
                kind=kind,
                is_distractor=True,
                start_byte=placed.start_byte,
                end_byte=placed.end_byte,
                quote=placed.text,
                note=note,
            )
        )
        return item_id
