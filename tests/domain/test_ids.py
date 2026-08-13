"""Identifiers: distinct per kind, canonical in spelling, stable under hashing."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from praxis.domain import ids
from praxis.domain.enums import GRAPH_KINDS, RecordKind
from praxis.domain.links import LinkType

SEQUENTIAL = st.sampled_from(sorted(ids.SEQUENTIAL_KINDS, key=lambda k: k.value))
ORDINALS = st.integers(min_value=1, max_value=10**6)


def test_every_kind_has_a_prefix():
    assert set(ids.PREFIXES) == set(RecordKind)


def test_prefixes_are_unique():
    # Two kinds sharing a prefix would make kind_of ambiguous and would let a
    # link point at a node of the wrong type without any check noticing.
    assert len(set(ids.PREFIXES.values())) == len(ids.PREFIXES)


def test_decision_and_document_prefixes_do_not_shadow_each_other():
    assert ids.kind_of("D-0042") is RecordKind.DECISION
    assert ids.kind_of("DOC-0042") is RecordKind.DOCUMENT


@given(kind=SEQUENTIAL, ordinal=ORDINALS)
def test_sequential_ids_round_trip_through_their_ordinal(kind, ordinal):
    record_id = ids.format_sequential_id(kind, ordinal)
    assert ids.ordinal_of(record_id) == ordinal
    assert ids.kind_of(record_id) is kind
    assert ids.is_valid(record_id, kind)


@given(kind=SEQUENTIAL, ordinal=ORDINALS)
def test_a_sequential_id_is_valid_for_no_other_kind(kind, ordinal):
    record_id = ids.format_sequential_id(kind, ordinal)
    for other in RecordKind:
        assert ids.is_valid(record_id, other) is (other is kind)


def test_non_canonical_padding_is_rejected():
    # D-00042 and D-0042 would otherwise be two identities for one record.
    assert ids.is_valid("D-0042", RecordKind.DECISION)
    assert not ids.is_valid("D-00042", RecordKind.DECISION)
    assert not ids.is_valid("D-42", RecordKind.DECISION)


@pytest.mark.parametrize("bad", ["", "D", "D-", "-0001", "d-0001", "D-abcd", "D-0000", "X-0001"])
def test_malformed_ids_are_rejected(bad):
    assert not ids.is_valid(bad, RecordKind.DECISION)


def test_ordinals_start_at_one():
    with pytest.raises(ids.InvalidIdError):
        ids.format_sequential_id(RecordKind.DECISION, 0)


@pytest.mark.parametrize("kind", sorted(ids.CONTENT_ADDRESSED_KINDS, key=lambda k: k.value))
def test_content_addressed_kinds_cannot_be_allocated(kind):
    with pytest.raises(ids.InvalidIdError):
        ids.format_sequential_id(kind, 1)


def test_kind_of_rejects_an_unknown_prefix():
    with pytest.raises(ids.InvalidIdError):
        ids.kind_of("NOPE-0001")


def test_ordinal_of_rejects_a_content_addressed_id():
    span_id = ids.span_id_for(ids.DocumentId("DOC-0001"), 0, 5)
    with pytest.raises(ids.InvalidIdError):
        ids.ordinal_of(span_id)


@given(doc=st.integers(1, 500), start=st.integers(0, 500), width=st.integers(1, 500))
def test_span_ids_are_a_function_of_their_coordinates(doc, start, width):
    doc_id = ids.DocumentId(ids.format_sequential_id(RecordKind.DOCUMENT, doc))
    once = ids.span_id_for(doc_id, start, start + width)
    again = ids.span_id_for(doc_id, start, start + width)
    assert once == again
    assert ids.is_valid(once, RecordKind.SPAN)
    # Two agents citing adjacent ranges must not collapse into one citation.
    assert ids.span_id_for(doc_id, start, start + width + 1) != once


def test_span_id_parts_cannot_be_confused_by_concatenation():
    # Without a separator that cannot occur in an id, (DOC-1, 23, 4) and
    # (DOC-1, 2, 34) would hash identically and two citations would merge.
    doc_id = ids.DocumentId("DOC-0001")
    assert ids.span_id_for(doc_id, 23, 4) != ids.span_id_for(doc_id, 2, 34)


@given(source=st.integers(1, 300), target=st.integers(1, 300))
def test_link_ids_make_the_same_edge_the_same_record(source, target):
    source_id = ids.format_sequential_id(RecordKind.DECISION, source)
    target_id = ids.format_sequential_id(RecordKind.ASSUMPTION, target)
    once = ids.link_id_for(LinkType.ASSUMES, source_id, target_id)
    assert once == ids.link_id_for(LinkType.ASSUMES, source_id, target_id)
    assert ids.is_valid(once, RecordKind.LINK)
    # Type and direction are both part of the identity.
    assert ids.link_id_for(LinkType.CONTRADICTS, source_id, target_id) != once
    assert ids.link_id_for(LinkType.ASSUMES, target_id, source_id) != once


def test_audit_events_are_not_graph_endpoints():
    assert RecordKind.AUDIT_EVENT not in GRAPH_KINDS
    assert len(GRAPH_KINDS) == len(RecordKind) - 1
