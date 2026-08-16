"""The grid, and the two properties everything above it stands on.

The unit tests below say what the scanner recognises. The property tests say
the things that have to be true of *any* document, and they are the ones worth
having: a segmenter can only be trusted not to drop text if the grid it groups
provably covers the document, and a span can only be trusted to quote its
source if a block's text is a slice of that source rather than a rebuild of it.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.ingest.adapters import JSON_ADAPTER
from praxis.ingest.blocks import BlockKind, blocks_of

# Documents assembled from the lines a real corpus is made of. Raw random text
# exercises the offset arithmetic; this exercises the classifier, and the two
# find different bugs.
LINES = st.sampled_from(
    [
        "# A heading",
        "### Deeper",
        "",
        "   ",
        "A sentence of ordinary prose.",
        "A second line of the same paragraph.",
        "- a bullet",
        "* another bullet",
        "1. a numbered item",
        "  wrapped continuation of the item",
        "| a | table | row |",
        "| - | ----- | --- |",
        "> quoted from somewhere else",
        "```python",
        "code = 1",
        "```",
        "\ttab indented",
        "unicode: é中\U0001f600",
        "---",
    ]
)
DOCUMENTS = st.lists(LINES, min_size=1, max_size=40).map("\n".join)

RAW_TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=300)


# -- the properties ----------------------------------------------------------


@given(st.one_of(DOCUMENTS, RAW_TEXT))
def test_a_blocks_text_is_a_slice_of_its_own_document(text):
    """Rebuilt text differing from sliced text by one space would put that
    space inside every span cut from the block."""
    raw = text.encode("utf-8")

    for block in blocks_of(text):
        assert raw[block.start_byte : block.end_byte].decode("utf-8") == block.text


@given(st.one_of(DOCUMENTS, RAW_TEXT))
def test_blocks_are_ordered_disjoint_and_indexed_from_zero(text):
    blocks = blocks_of(text)

    assert [block.index for block in blocks] == list(range(len(blocks)))
    previous_end = 0
    for block in blocks:
        assert block.start_byte >= previous_end
        assert block.end_byte > block.start_byte
        previous_end = block.end_byte
    assert previous_end <= len(text.encode("utf-8"))


@given(st.one_of(DOCUMENTS, RAW_TEXT))
def test_every_non_whitespace_character_lies_in_exactly_one_block(text):
    """The property that makes the grid a lossless view.

    A segmenter can only be said not to drop text if the grid it groups covers
    the document -- otherwise text could disappear before any model saw it,
    and no downstream check would ever notice.
    """
    raw = text.encode("utf-8")
    seen = bytearray(len(raw))
    for block in blocks_of(text):
        for position in range(block.start_byte, block.end_byte):
            seen[position] += 1

    cursor = 0
    for character in text:
        width = len(character.encode("utf-8"))
        if not character.isspace():
            assert set(seen[cursor : cursor + width]) == {1}, (
                f"{character!r} at byte {cursor} is in {seen[cursor]} blocks"
            )
        cursor += width


@given(st.one_of(DOCUMENTS, RAW_TEXT))
def test_no_block_begins_or_ends_in_whitespace(text):
    """Blocks are trimmed on both sides, so a span over a run of them quotes
    text rather than the newline in front of it."""
    for block in blocks_of(text):
        assert block.text == block.text.strip()


@given(DOCUMENTS)
def test_the_grid_is_a_function_of_the_text(text):
    assert blocks_of(text) == blocks_of(text)


# -- what the scanner recognises ---------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\n\n", "\t\n \n"])
def test_a_document_with_nothing_in_it_has_no_blocks(text):
    assert blocks_of(text) == ()


def test_a_heading_is_its_own_block():
    blocks = blocks_of("# Chosen\nWe picked SQLite.\n")

    assert [block.kind for block in blocks] == [BlockKind.HEADING, BlockKind.PARAGRAPH]
    assert blocks[0].text == "# Chosen"
    assert blocks[1].text == "We picked SQLite."


def test_consecutive_lines_are_one_paragraph_and_a_blank_line_ends_it():
    blocks = blocks_of("first line\nsecond line\n\nnext paragraph\n")

    assert [block.text for block in blocks] == ["first line\nsecond line", "next paragraph"]


def test_each_list_item_is_a_block_and_a_wrapped_line_joins_it():
    blocks = blocks_of("- first item\n  wrapped\n- second item\n")

    assert [block.kind for block in blocks] == [BlockKind.LIST_ITEM, BlockKind.LIST_ITEM]
    assert blocks[0].text == "- first item\n  wrapped"
    assert blocks[1].text == "- second item"


def test_a_table_is_one_block_rather_than_one_block_per_row():
    """The grid is also the degradation floor, and a floor that emits a row
    without its header emits a span that means nothing alone."""
    table = "| phase | hours |\n| ----- | ----- |\n| 2 | 5.6 |"
    blocks = blocks_of(f"intro\n\n{table}\n\nafter")

    assert [block.kind for block in blocks] == [
        BlockKind.PARAGRAPH,
        BlockKind.TABLE,
        BlockKind.PARAGRAPH,
    ]
    assert blocks[1].text == table


def test_consecutive_quoted_lines_are_one_block():
    blocks = blocks_of("> first\n> second\n\nplain")

    assert blocks[0].kind is BlockKind.QUOTE
    assert blocks[0].text == "> first\n> second"


def test_a_fenced_block_is_never_split_by_what_it_contains():
    """Its contents are not prose. Cutting it in half produces two spans that
    are each syntactically nothing."""
    fenced = "```python\n# not a heading\n\n- not a bullet\n```"
    blocks = blocks_of(f"{fenced}\n\nafter")

    assert blocks[0].kind is BlockKind.CODE
    assert blocks[0].text == fenced
    assert blocks[1].text == "after"


def test_an_unclosed_fence_swallows_the_rest_of_the_document():
    """What a markdown renderer does with it. Guessing otherwise would put the
    same text in different blocks depending on where the guess landed."""
    blocks = blocks_of("```\nstill code\n\nalso code")

    assert len(blocks) == 1
    assert blocks[0].kind is BlockKind.CODE


def test_a_tilde_fence_is_not_closed_by_a_backtick_fence():
    blocks = blocks_of("~~~\n```\nstill inside\n~~~\n\nafter")

    assert [block.kind for block in blocks] == [BlockKind.CODE, BlockKind.PARAGRAPH]
    assert blocks[1].text == "after"


def test_a_thematic_break_is_not_mistaken_for_a_list_item():
    blocks = blocks_of("- a bullet\n\n---\n\nprose")

    assert [block.kind for block in blocks] == [
        BlockKind.LIST_ITEM,
        BlockKind.PARAGRAPH,
        BlockKind.PARAGRAPH,
    ]


# -- offsets are bytes -------------------------------------------------------


def test_offsets_are_byte_offsets_and_not_character_offsets():
    """The mistake that would make every span in a document with an emoji in
    it resolve one or two bytes short."""
    text = "\U0001f600 leading emoji\n\nsecond block"

    blocks = blocks_of(text)

    assert blocks[1].start_byte == len("\U0001f600 leading emoji\n\n".encode())
    assert blocks[1].text == "second block"


def test_trailing_whitespace_is_outside_the_block_it_follows():
    blocks = blocks_of("a paragraph   \n\nnext")

    assert blocks[0].text == "a paragraph"
    assert blocks[0].end_byte == len("a paragraph")


def test_indentation_is_outside_the_block_it_precedes():
    blocks = blocks_of("      indented prose")

    assert blocks[0].start_byte == 6
    assert blocks[0].text == "indented prose"


# -- the other two source kinds ----------------------------------------------


def test_the_json_rendering_lands_one_block_per_leaf():
    """The claim that one rule set serves all three source kinds, checked
    against the adapter's real output rather than against a hand-written
    imitation of it."""
    raw = json.dumps({"chosen": "SQLite", "notes": "line one\nline two"}).encode("utf-8")
    source = JSON_ADAPTER.normalise(raw, source_uri="d.json")

    blocks = blocks_of(source.text)

    assert [block.text for block in blocks] == [
        "chosen: SQLite",
        "notes:\nline one\nline two",
    ]


def test_a_plain_text_status_update_still_gets_its_bullets():
    blocks = blocks_of("Weekly status\n\n- shipped the store\n- started ingestion\n")

    assert [block.kind for block in blocks] == [
        BlockKind.PARAGRAPH,
        BlockKind.LIST_ITEM,
        BlockKind.LIST_ITEM,
    ]
