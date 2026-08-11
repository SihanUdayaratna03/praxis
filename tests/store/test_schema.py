"""What the schema refuses, and whether it still agrees with the enums.

Two kinds of test here. The first reads the `CHECK` constraints out of
`sqlite_master` and compares them to the Python enums, because those two lists
are written in different languages and nothing else would notice them drifting
apart -- a member added to `LinkType` without a migration would simply start
failing at write time, in whichever agent happened to use it first.

The second kind writes deliberately wrong SQL. Every one of these is a rule the
repository also enforces; asserting them here is what makes the rule true of a
person with the `sqlite3` shell and a good reason, which is the whole argument
of ADR 0008 for putting append-only in triggers.
"""

from __future__ import annotations

import re
import sqlite3

import pytest
from praxis.domain.enums import (
    GRAPH_KINDS,
    AssumptionStatus,
    AuditAction,
    DecisionScope,
    DecisionStatus,
    FindingKind,
    Impact,
    MatchQuality,
    Severity,
    SourceKind,
    Unit,
    Verdict,
)
from praxis.domain.links import LinkType
from praxis.store.connection import transaction
from praxis.store.errors import (
    AppendOnlyViolationError,
    DanglingEdgeError,
    DuplicateRecordError,
    IntegrityViolationError,
)
from praxis.store.repository import Repository

from tests.store.conftest import WRITTEN_AT, World

TIMESTAMP = WRITTEN_AT.isoformat()

APPEND_ONLY_TABLES = (
    "node",
    "record_version",
    "document",
    "span",
    "decision",
    "assumption",
    "estimate",
    "outcome",
    "link",
    "finding",
    "finding_evidence",
    "audit_event",
    "schema_version",
)


def check_literals(store: Repository, table: str, constraint: str) -> set[str]:
    """Pull the quoted values out of one named CHECK constraint.

    Reads the schema SQL back from the database rather than the file, so this
    also proves the constraint survived the migration.
    """
    row = store.connection.execute(
        "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
    ).fetchone()
    schema = row["sql"]
    start = schema.index(f"CONSTRAINT {constraint} CHECK (") + len(
        f"CONSTRAINT {constraint} CHECK "
    )
    depth = 0
    for offset, character in enumerate(schema[start:], start=start):
        depth += (character == "(") - (character == ")")
        if depth == 0:
            return set(re.findall(r"'([^']*)'", schema[start : offset + 1]))
    message = f"unbalanced parentheses in {table}.{constraint}"
    raise AssertionError(message)


@pytest.mark.parametrize(
    ("table", "constraint", "members"),
    [
        ("node", "node_kind_known", {kind.value for kind in GRAPH_KINDS}),
        ("link", "link_type_known", {t.value for t in LinkType}),
        ("document", "document_source_kind_known", {k.value for k in SourceKind}),
        ("decision", "decision_scope_known", {s.value for s in DecisionScope}),
        ("decision", "decision_impact_known", {i.value for i in Impact}),
        ("decision", "decision_status_known", {s.value for s in DecisionStatus}),
        ("assumption", "assumption_status_known", {s.value for s in AssumptionStatus}),
        ("estimate", "estimate_unit_known", {u.value for u in Unit}),
        ("outcome", "outcome_unit_known", {u.value for u in Unit}),
        ("outcome", "outcome_match_quality_known", {q.value for q in MatchQuality}),
        ("finding", "finding_kind_known", {k.value for k in FindingKind}),
        ("finding", "finding_severity_known", {s.value for s in Severity}),
        ("audit_event", "audit_action_known", {a.value for a in AuditAction}),
    ],
)
def test_the_schema_and_the_enums_say_the_same_thing(store, table, constraint, members):
    assert check_literals(store, table, constraint) == members


def test_the_finding_verdict_constraint_covers_every_verdict(store):
    # Spelled out rather than parametrised: this constraint also mentions
    # 'undecided' inside the challenge rule, so the literal set is not simply
    # the enum. Both halves still have to know all three verdicts.
    literals = check_literals(store, "finding", "finding_verdict_known")

    assert literals == {v.value for v in Verdict}


def test_audit_events_cannot_be_graph_nodes(store):
    # The one member of RecordKind deliberately absent from the node registry.
    # A trail that participates in the graph it audits is not evidence.
    assert "audit_event" not in check_literals(store, "node", "node_kind_known")


# --- append-only ---------------------------------------------------------


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_every_table_refuses_an_update(store, world: World, table):
    with pytest.raises(AppendOnlyViolationError), transaction(store.connection):
        store.connection.execute(f"UPDATE {table} SET rowid = rowid")  # noqa: S608


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_every_table_refuses_a_delete(store, world: World, table):
    with pytest.raises(AppendOnlyViolationError), transaction(store.connection):
        store.connection.execute(f"DELETE FROM {table}")  # noqa: S608


def test_the_refusal_says_what_the_rule_is(store, world: World):
    with (
        pytest.raises(AppendOnlyViolationError, match="append-only") as caught,
        transaction(store.connection),
    ):
        store.connection.execute("DELETE FROM decision")

    assert "new version" in str(caught.value) or "retracted" in str(caught.value)


def test_versions_cannot_skip(store, world: World):
    # Without this a caller could insert version 7 of a record that has only
    # ever had version 1, and every "current version" read would silently skip
    # the five in between.
    with pytest.raises(AppendOnlyViolationError), transaction(store.connection):
        store.connection.execute(
            "INSERT INTO record_version (id, version, retracted, created_at, created_by) "
            "VALUES (?, 7, 0, ?, 'forger')",
            (world.decision.id, TIMESTAMP),
        )


def test_a_first_version_cannot_be_numbered_anything_but_one(store, world: World):
    store.connection.execute(
        "INSERT INTO node (id, kind, ordinal) VALUES ('D-0099', 'decision', 99)"
    )

    with pytest.raises(AppendOnlyViolationError), transaction(store.connection):
        store.connection.execute(
            "INSERT INTO record_version (id, version, retracted, created_at, created_by) "
            "VALUES ('D-0099', 2, 0, ?, 'forger')",
            (TIMESTAMP,),
        )


# --- referential integrity -----------------------------------------------


def test_an_edge_cannot_point_at_a_node_that_is_not_there(store, world: World):
    with pytest.raises(DanglingEdgeError), transaction(store.connection):
        store.connection.execute(
            "INSERT INTO link VALUES "
            "('L-ffff000000000001', 1, 'assumes', ?, 'decision', 'A-9999', 'assumption', "
            "0.5, 'invented', NULL)",
            (world.decision.id,),
        )


def test_an_edge_cannot_lie_about_the_kind_at_its_end(store, world: World):
    # The composite (id, kind) foreign key. Without it an edge could claim its
    # target is an assumption while pointing at an estimate, and the grammar in
    # praxis.domain.links would be checking a fact that is not true.
    with pytest.raises(DanglingEdgeError), transaction(store.connection):
        store.connection.execute(
            "INSERT INTO link VALUES "
            "('L-ffff000000000002', 1, 'assumes', ?, 'decision', ?, 'assumption', "
            "0.5, 'mislabelled', NULL)",
            (world.decision.id, world.estimate.id),
        )


def test_a_span_cannot_cite_a_document_that_is_not_there(store, world: World):
    with pytest.raises(DanglingEdgeError), transaction(store.connection):
        store.connection.execute(
            "INSERT INTO node (id, kind, ordinal) VALUES ('SPAN-ffffffffffffffff', 'span', NULL)"
        )
        store.connection.execute(
            "INSERT INTO record_version (id, version, retracted, created_at, created_by) "
            "VALUES ('SPAN-ffffffffffffffff', 1, 0, ?, 'forger')",
            (TIMESTAMP,),
        )
        store.connection.execute(
            "INSERT INTO span VALUES ('SPAN-ffffffffffffffff', 1, 'DOC-9999', 0, 3, 'abc')"
        )


def test_a_finding_cannot_cite_evidence_that_is_not_there(store, world: World):
    # The reason evidence spans are a child table and not a JSON array: this is
    # the one place a reference could otherwise dangle unnoticed.
    with pytest.raises(DanglingEdgeError), transaction(store.connection):
        store.connection.execute(
            "INSERT INTO finding_evidence VALUES (?, 1, 0, 'SPAN-ffffffffffffffff')",
            (world.decision.id,),
        )


def test_the_same_record_cannot_be_written_twice(store, world: World):
    with pytest.raises(DuplicateRecordError), transaction(store.connection):
        store.connection.execute(
            "INSERT INTO node (id, kind, ordinal) VALUES (?, 'decision', 1)",
            (world.decision.id,),
        )


def test_the_store_holds_no_dangling_references_after_the_fixture(store, world: World):
    assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []


# --- value constraints ---------------------------------------------------


@pytest.mark.parametrize(
    ("description", "statement", "parameters"),
    [
        (
            "a naive timestamp",
            "INSERT INTO record_version (id, version, retracted, created_at, created_by) "
            "VALUES ('D-0098', 1, 0, '2026-08-12T09:00:00', 'forger')",
            (),
        ),
        (
            "an edge from a node to itself",
            "INSERT INTO link VALUES ('L-ffff000000000003', 1, 'contradicts', 'D-0001', "
            "'decision', 'D-0001', 'decision', 0.5, 'r', NULL)",
            (),
        ),
        (
            "a confidence outside [0, 1]",
            "INSERT INTO link VALUES ('L-ffff000000000004', 1, 'assumes', 'D-0001', "
            "'decision', 'A-0001', 'assumption', 1.4, 'r', NULL)",
            (),
        ),
        (
            "an unknown record kind",
            "INSERT INTO node (id, kind, ordinal) VALUES ('Q-0001', 'quantum', 1)",
            (),
        ),
        (
            "a span whose ordinal was allocated",
            "INSERT INTO node (id, kind, ordinal) VALUES ('SPAN-eeeeeeeeeeeeeeee', 'span', 1)",
            (),
        ),
        (
            "an empty reason on an audit row",
            "INSERT INTO audit_event VALUES ('AUD-9999', 9999, '2026-08-12T09:00:00+00:00', "
            "'a', 'created', 'D-0001', 'decision', 1, '   ', NULL)",
            (),
        ),
    ],
)
def test_the_schema_refuses(store, world: World, description, statement, parameters):
    with pytest.raises(IntegrityViolationError), transaction(store.connection):
        store.connection.execute(statement, parameters)


def test_a_decision_must_have_rejected_an_option(store, world: World):
    # A decision with no rejected alternatives is a note. The model says so and
    # so does the table, which is what makes it true of the corpus rather than
    # of the code path that happened to write it.
    with pytest.raises(IntegrityViolationError), transaction(store.connection):
        store.connection.execute(
            "INSERT INTO decision VALUES (?, 2, 't', 'c', '[]', 'me', ?, 'team', 'low', "
            "'proposed', ?, 0.5)",
            (world.decision.id, TIMESTAMP, world.span.id),
        )


def test_an_unresolved_outcome_cannot_carry_a_number(store, world: World):
    with pytest.raises(IntegrityViolationError), transaction(store.connection):
        store.connection.execute(
            "INSERT INTO node (id, kind, ordinal) VALUES ('OUT-0001', 'outcome', 1)"
        )
        store.connection.execute(
            "INSERT INTO record_version (id, version, retracted, created_at, created_by) "
            "VALUES ('OUT-0001', 1, 0, ?, 'forger')",
            (TIMESTAMP,),
        )
        store.connection.execute(
            "INSERT INTO outcome VALUES ('OUT-0001', 1, ?, '7', '0', 'weeks', "
            "'unresolved', NULL, '', NULL)",
            (world.estimate.id,),
        )


def test_the_store_survives_its_own_integrity_check(store, world: World):
    assert store.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_raw_sqlite_errors_do_not_escape_the_transaction_boundary(store, world: World):
    # A statement run through `transaction()` arrives as a StoreError; one run
    # outside it does not. Pinned here because the boundary is the thing ADR
    # 0003's replaceability claim actually rests on.
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute("DELETE FROM decision")
