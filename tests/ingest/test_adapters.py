"""The normalisation rules every byte offset in the system depends on.

These are boring assertions about whitespace and they guard the least
forgiving invariant in the project. A span is a byte range into
`Document.content`; if normalisation moves by one byte between two runs, every
citation cut from that document stops resolving and `VerifierAgent` reports a
corpus full of hallucinations that never happened.

So the tests below check the transformations that happen, and -- with as much
care -- the ones that must not.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.domain.enums import SourceKind
from praxis.domain.ids import DocumentId
from praxis.domain.records import Span
from praxis.domain.spans import verify_span
from praxis.ingest.adapters import (
    ADAPTER_NAME,
    JSON_ADAPTER,
    MARKDOWN_ADAPTER,
    MAX_TITLE_CHARS,
    TEXT_ADAPTER,
    adapter_for,
    document_from,
    normalise_bytes,
    read_source,
)
from praxis.ingest.errors import (
    EmptySourceError,
    MalformedSourceError,
    SourceDecodeError,
    UnsupportedSourceError,
)

AT = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)

TEXT_BODIES = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=1,
    max_size=200,
).filter(lambda body: body.strip() != "")


# -- what normalisation does -------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"a\r\nb", "a\nb"),
        (b"a\rb", "a\nb"),
        (b"a\r\r\nb", "a\n\nb"),
        (b"\xef\xbb\xbfheading", "heading"),
        (b"a\nb", "a\nb"),
    ],
)
def test_line_endings_and_the_byte_order_mark_are_normalised(raw, expected):
    assert normalise_bytes(raw, "test") == expected


def test_the_same_character_spelled_two_ways_normalises_to_one():
    """NFC, and the reason it matters is that the two spellings differ in length.

    A model quoting text back in the decomposed spelling would produce a span
    whose text does not match the document's bytes, and the rejection would
    look exactly like a hallucination.
    """
    # Spelled with escapes rather than as literals: two source files that look
    # identical would make this test pass without testing anything.
    composed = "caf" + chr(0x00E9)  # e-acute, one code point
    decomposed = "cafe" + chr(0x0301)  # e, then a combining acute
    assert composed != decomposed, "the two spellings collapsed in the source file"
    assert len(composed.encode("utf-8")) != len(decomposed.encode("utf-8"))

    assert normalise_bytes(decomposed.encode("utf-8"), "test") == composed
    assert normalise_bytes(composed.encode("utf-8"), "test") == composed


def test_a_byte_order_mark_is_dropped_only_from_the_front():
    """One in the middle is content, however unlikely -- and moving it would
    shift every offset after it."""
    inside = "a" + chr(0xFEFF) + "b"
    assert normalise_bytes(inside.encode("utf-8"), "test") == inside


@pytest.mark.parametrize(
    "raw",
    [
        b"trailing whitespace   \nkept",
        b"\n\n\nleading blank lines kept",
        b"no final newline added",
        b"    indented lines kept",
    ],
)
def test_nothing_else_is_touched(raw):
    """Every one of these would be harmless to tidy and would move offsets."""
    assert normalise_bytes(raw, "test").encode("utf-8") == raw


@given(TEXT_BODIES)
def test_normalisation_is_idempotent(body):
    """Re-ingesting a document must produce the same offsets and the same ids.

    Not idempotent means the content hash changes on the second read, which
    would make the store's re-ingestion check useless and fork every span id.
    """
    once = normalise_bytes(body.encode("utf-8"), "test")
    twice = normalise_bytes(once.encode("utf-8"), "test")

    assert once == twice


@given(TEXT_BODIES)
def test_normalised_text_carries_no_carriage_return(body):
    assert "\r" not in normalise_bytes(body.encode("utf-8"), "test")


# -- what it refuses ---------------------------------------------------------


def test_bytes_that_are_not_utf8_are_refused_rather_than_repaired():
    with pytest.raises(SourceDecodeError) as caught:
        normalise_bytes(b"valid then \xff\xfe", "notes.md")

    assert "notes.md" in str(caught.value)
    assert "replacement character" in str(caught.value)


@pytest.mark.parametrize("raw", [b"", b"   ", b"\n\n\n", b"\t \r\n"])
def test_a_source_with_nothing_to_address_is_refused(raw):
    with pytest.raises(EmptySourceError):
        MARKDOWN_ADAPTER.normalise(raw, source_uri="empty.md")


# -- the registry ------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("notes.md", SourceKind.MARKDOWN),
        ("NOTES.MD", SourceKind.MARKDOWN),
        ("notes.markdown", SourceKind.MARKDOWN),
        ("notes.txt", SourceKind.TEXT),
        ("notes.text", SourceKind.TEXT),
        ("NOTES", SourceKind.TEXT),
        ("export.json", SourceKind.JSON),
        ("export.JSON", SourceKind.JSON),
    ],
)
def test_extensions_route_to_adapters_case_insensitively(name, expected):
    assert adapter_for(Path(name)).source_kind is expected


def test_an_unknown_extension_is_refused_and_says_what_is_supported():
    """Never a silent fall back to the text adapter: a PDF read as UTF-8 mostly
    succeeds, and every span cut from the result would be valid and useless."""
    with pytest.raises(UnsupportedSourceError) as caught:
        adapter_for(Path("report.pdf"))

    message = str(caught.value)
    assert ".pdf" in message
    assert ".md" in message and ".json" in message


def test_reading_a_file_goes_through_the_adapter_its_extension_names(tmp_path):
    source = tmp_path / "adr.md"
    source.write_bytes(b"# Chosen\r\n\r\nWe picked SQLite.\r\n")

    read = read_source(source)

    assert read.source_kind is SourceKind.MARKDOWN
    assert read.title == "Chosen"
    assert "\r" not in read.text


# -- titles ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("# Store location\n\ntext", "Store location"),
        ("### Deeper heading\n", "Deeper heading"),
        ("## Closed ATX ##\n", "Closed ATX"),
        ("   # Indented three spaces\n", "Indented three spaces"),
        ("text first\n\n# Later heading\n", "Later heading"),
        ("no heading at all\n", None),
        ("#missing space\n", None),
        ("####### seven hashes\n", None),
        (f"# {'x' * (MAX_TITLE_CHARS + 1)}\n", None),
    ],
)
def test_markdown_titles_come_from_the_first_heading(body, expected):
    assert MARKDOWN_ADAPTER.normalise(body.encode("utf-8"), source_uri="d.md").title == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Weekly status\n\nWe shipped the store.", "Weekly status"),
        ("Only one line", "Only one line"),
        ("First line\nsecond line immediately", None),
        ("\nblank first line", None),
        (f"{'x' * (MAX_TITLE_CHARS + 1)}\n\nbody", None),
    ],
)
def test_text_titles_are_only_taken_where_the_layout_declares_one(body, expected):
    assert TEXT_ADAPTER.normalise(body.encode("utf-8"), source_uri="d.txt").title == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"title": "Sprint 12"}, "Sprint 12"),
        ({"name": "Sprint 12"}, "Sprint 12"),
        ({"summary": "Sprint 12", "title": "Preferred"}, "Preferred"),
        ({"title": "   "}, None),
        ({"title": 12}, None),
        (["no", "mapping", "at the root"], None),
    ],
)
def test_json_titles_come_from_the_first_title_shaped_key(payload, expected):
    raw = json.dumps(payload).encode("utf-8")

    assert JSON_ADAPTER.normalise(raw, source_uri="d.json").title == expected


# -- the JSON rendering ------------------------------------------------------


def test_json_leaves_are_rendered_one_block_per_path():
    raw = json.dumps(
        {"decision": {"chosen": "SQLite", "confidence": 0.8}, "tags": ["store", "adr"]}
    ).encode("utf-8")

    text = JSON_ADAPTER.normalise(raw, source_uri="d.json").text

    assert text == (
        "decision.chosen: SQLite\n\ndecision.confidence: 0.8\n\ntags[0]: store\n\ntags[1]: adr\n"
    )


def test_multi_line_prose_in_json_is_rendered_verbatim_rather_than_escaped():
    """The reason the JSON adapter renders at all.

    Left as raw JSON, a paragraph would appear as one line full of `\\n`, and
    every span quoting it would quote escape sequences.
    """
    prose = "We chose SQLite.\n\nPostgres needs a server nobody will run."
    raw = json.dumps({"notes": prose}).encode("utf-8")

    text = JSON_ADAPTER.normalise(raw, source_uri="d.json").text

    assert text == f"notes:\n{prose}\n"
    assert "\\n" not in text


def test_escaped_carriage_returns_inside_json_strings_are_normalised_too():
    """The one place the newline rule could have been applied to the file and
    missed: `\\r\\n` inside a JSON string is two characters until it is parsed."""
    raw = json.dumps({"notes": "first\r\nsecond"}).encode("utf-8")

    assert "\r" not in JSON_ADAPTER.normalise(raw, source_uri="d.json").text


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"open": True}, "open: true\n"),
        ({"owner": None}, "owner: null\n"),
        ({"weeks": 6}, "weeks: 6\n"),
        ({"tags": []}, "tags: []\n"),
        ({"meta": {}}, "meta: {}\n"),
        ("a bare string document", "value: a bare string document\n"),
        (42, "value: 42\n"),
    ],
)
def test_non_string_and_empty_leaves_stay_visible(payload, expected):
    """An absent field and a field that is present and empty are different
    facts, and an agent reading a status export is asked to tell them apart."""
    raw = json.dumps(payload).encode("utf-8")

    assert JSON_ADAPTER.normalise(raw, source_uri="d.json").text == expected


def test_json_that_is_not_json_is_a_different_failure_from_bytes_that_are_not_utf8():
    with pytest.raises(MalformedSourceError):
        JSON_ADAPTER.normalise(b"{not json,}", source_uri="d.json")


@given(
    st.dictionaries(
        keys=st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1),
        values=TEXT_BODIES,
        min_size=1,
        max_size=5,
    )
)
def test_every_string_leaf_survives_the_rendering_verbatim(payload):
    """The property that makes JSON prose citable at all.

    If a value were escaped, re-wrapped or trimmed on the way through, no span
    over the rendering could quote what the source actually said.
    """
    text = JSON_ADAPTER.normalise(json.dumps(payload).encode("utf-8"), source_uri="d.json").text

    for value in payload.values():
        assert normalise_bytes(value.encode("utf-8"), "leaf") in text


# -- becoming a Document -----------------------------------------------------


def test_a_document_built_from_a_source_can_be_cited_end_to_end():
    """The point of the whole module, asserted directly: a span over the
    normalised content resolves against it."""
    source = MARKDOWN_ADAPTER.normalise(b"# ADR\r\n\r\nWe chose SQLite.\n", source_uri="adr.md")
    document = document_from(source, doc_id=DocumentId("DOC-0001"), ingested_at=AT)

    span = Span.covering(document, 0, 5, created_by=ADAPTER_NAME, created_at=AT)

    assert verify_span(span, document).ok
    assert span.text == "# ADR"


def test_the_document_records_the_adapter_as_its_author_by_default():
    """Spelled the same as the entry in NON_LLM_AGENTS, so the name in the
    audit trail is the name the routing table refuses to route."""
    source = TEXT_ADAPTER.normalise(b"status", source_uri="s.txt")

    document = document_from(source, doc_id=DocumentId("DOC-0001"), ingested_at=AT)

    assert document.created_by == ADAPTER_NAME
    assert document.ingested_at == AT == document.created_at


def test_a_blank_title_becomes_no_title_rather_than_an_empty_one():
    source = TEXT_ADAPTER.normalise(b"body", source_uri="s.txt")

    document = document_from(
        source.__class__(
            source_uri=source.source_uri,
            source_kind=source.source_kind,
            text=source.text,
            title="   ",
        ),
        doc_id=DocumentId("DOC-0001"),
        ingested_at=AT,
    )

    assert document.title is None


@given(TEXT_BODIES)
def test_content_hash_is_stable_across_re_reading(body):
    """What the store's re-ingestion check rests on."""
    raw = body.encode("utf-8")
    first = document_from(
        TEXT_ADAPTER.normalise(raw, source_uri="s.txt"),
        doc_id=DocumentId("DOC-0001"),
        ingested_at=AT,
    )
    second = document_from(
        TEXT_ADAPTER.normalise(raw, source_uri="s.txt"),
        doc_id=DocumentId("DOC-0002"),
        ingested_at=AT,
    )

    assert first.content_hash == second.content_hash
    assert first.byte_length == len(first.content.encode("utf-8"))
