"""A formalization pass over a real store: what it writes, and what it refuses to pay for twice.

Read back out of SQLite rather than off the objects the run returned, for the
reason `tests/agents/test_detection.py` gives -- inspecting what was handed in
only proves the pipeline agrees with itself.

The claims under `TestWhatIsNotPaidForTwice` are the ones this module exists for.
An assumption whose predicate already parses has nothing to compile, and one this
agent has already failed at is read off the *audit trail* -- two different
questions with two different answers, and without the second a re-run would spend
the reason tier on the same uncompilable assumption every time, forever.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from praxis.agents.formalization import (
    FORMALIZATION_ACTOR,
    FormalizationRun,
    formalize_store,
)
from praxis.agents.formalizer import FormalizationRefusal, is_checkable
from praxis.domain.records import Assumption, Span
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

from tests.agents.conftest import Answering, Refusing
from tests.monitor.conftest import ACTOR, AT, BODY, make_assumption

PROSE_PREDICATE = "the index stays under 50 GB"
PROSE_EXPIRY = "sometime next year"


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def span(store: Repository) -> Span:
    """One real document and one span over it, both in the store.

    Built through the real adapter so the span's offsets are offsets ingestion
    would really produce, and written to the store because the pass re-reads the
    cited span rather than being handed one.
    """
    source = MARKDOWN_ADAPTER.normalise(BODY.encode("utf-8"), source_uri="adr.md")
    document = store.add(
        document_from(source, doc_id="DOC-0001", ingested_at=AT), actor=ACTOR, reason="fixture"
    )
    return store.add(
        Span.covering(
            document, 0, len(document.content.encode("utf-8")), created_by=ACTOR, created_at=AT
        ),
        actor=ACTOR,
        reason="fixture",
    )


def answer(
    predicate: str | None = "index_size_gb <= 50",
    expiry: str | None = "when(indexed_documents >= 10000000)",
    confidence: float = 0.9,
    note: str | None = None,
) -> str:
    """One formalization answer as the structured layer would receive it."""
    return json.dumps(
        {
            "predicate": predicate,
            "expiry_condition": expiry,
            "confidence": confidence,
            "subject": "index_size_gb",
            "note": note,
        }
    )


def uncompiled(
    store: Repository,
    span: Span,
    *,
    assumption_id: str = "A-0001",
    predicate: str = PROSE_PREDICATE,
    expiry: str = PROSE_EXPIRY,
) -> Assumption:
    """An assumption in the store as Phase 4's extractor leaves it: prose in both fields."""
    record = store.add(
        make_assumption(span, assumption_id=assumption_id, predicate=predicate, expiry=expiry),
        actor=ACTOR,
        reason="fixture",
    )
    assert not is_checkable(record)
    return record


def compiled(store: Repository, span: Span, *, assumption_id: str = "A-0001") -> Assumption:
    """An assumption already carrying a predicate and an expiry that both parse."""
    record = store.add(make_assumption(span, assumption_id=assumption_id), actor=ACTOR, reason="f")
    assert is_checkable(record)
    return record


def stored(store: Repository, assumption_id: str = "A-0001") -> Assumption:
    """The current version, read back out of SQLite."""
    record = store.get(Assumption, assumption_id)
    assert record is not None
    return record


class TestWhatAPassCompiles:
    def test_an_uncompiled_assumption_is_compiled(self, store, span):
        uncompiled(store, span)
        run = formalize_store(store, Answering([answer()]), at=AT)
        assert len(run.formalized) == 1
        assert run.checkable

    def test_the_compiled_predicate_reaches_sqlite(self, store, span):
        uncompiled(store, span)
        formalize_store(store, Answering([answer()]), at=AT)
        assert stored(store).predicate == "index_size_gb <= 50"

    def test_the_stored_predicate_is_the_rendered_form(self, store, span):
        # So two assumptions about one quantity land in one blocking bucket in
        # ContradictionDetector rather than in two.
        uncompiled(store, span)
        formalize_store(store, Answering([answer(predicate="index_size_gb<=50")]), at=AT)
        assert stored(store).predicate == "index_size_gb <= 50"

    def test_the_write_is_a_new_version_and_not_a_replacement(self, store, span):
        # Invariant 7. The words the extractor read stay readable beside the
        # expression they were compiled into.
        uncompiled(store, span)
        formalize_store(store, Answering([answer()]), at=AT)
        assert stored(store).version == 2

    def test_the_words_the_extractor_read_are_still_there(self, store, span):
        uncompiled(store, span)
        formalize_store(store, Answering([answer()]), at=AT)
        first = store.get(Assumption, "A-0001", version=1)
        assert first is not None
        assert first.predicate == PROSE_PREDICATE

    def test_every_assumption_in_the_store_is_visited(self, store, span):
        uncompiled(store, span, assumption_id="A-0001")
        uncompiled(store, span, assumption_id="A-0002")
        run = formalize_store(store, Answering([answer(), answer()]), at=AT)
        assert len(run.formalized) == 2

    def test_an_uncompilable_assumption_is_kept_rather_than_dropped(self, store, span):
        # Degrade quality, never correctness. A person can repair what they can
        # see, and silence cannot be repaired by anyone.
        uncompiled(store, span)
        run = formalize_store(store, Answering([answer(predicate="the team stays motivated")]))
        assert len(run.marked) == 1
        assert not run.checkable
        assert stored(store).version == 2

    def test_a_kept_assumption_is_not_checkable_afterwards(self, store, span):
        uncompiled(store, span)
        formalize_store(store, Answering([answer(predicate="morale is fine")]), at=AT)
        assert not is_checkable(stored(store))


class TestWhatIsNotPaidForTwice:
    def test_an_assumption_that_already_parses_costs_no_call(self, store, span):
        compiled(store, span)
        provider = Answering([answer()])
        run = formalize_store(store, provider, at=AT)
        assert run.already_checkable == 1
        assert provider.requests == []

    def test_an_assumption_that_already_parses_is_not_rewritten(self, store, span):
        compiled(store, span)
        formalize_store(store, Answering([answer()]), at=AT)
        assert stored(store).version == 1

    def test_a_second_pass_does_not_re_attempt_what_it_already_failed_at(self, store, span):
        # Read off the audit trail, not off a flag: the trail already records
        # that this agent wrote a version, and a flag would be a second copy of
        # that fact which could disagree with it.
        uncompiled(store, span)
        formalize_store(store, Answering([answer(predicate="still prose")]), at=AT)
        provider = Answering([answer()])
        run = formalize_store(store, provider, at=AT)
        assert run.already_attempted == 1
        assert provider.requests == []

    def test_a_second_pass_writes_nothing(self, store, span):
        uncompiled(store, span)
        formalize_store(store, Answering([answer(predicate="still prose")]), at=AT)
        formalize_store(store, Answering([answer()]), at=AT)
        assert stored(store).version == 2

    def test_force_re_attempts_what_an_older_prompt_failed_at(self, store, span):
        # Named rather than guessed at: the first check is a property of the
        # text and the second of the history, so a better prompt has to be able
        # to say so out loud.
        uncompiled(store, span)
        formalize_store(store, Answering([answer(predicate="still prose")]), at=AT)
        run = formalize_store(store, Answering([answer()]), at=AT, force=True)
        assert run.already_attempted == 0
        assert len(run.checkable) == 1
        assert stored(store).predicate == "index_size_gb <= 50"

    def test_force_does_not_re_open_one_that_already_parses(self, store, span):
        # force is about the history, not about the text. An assumption that
        # parses has nothing to compile whatever the prompt says.
        compiled(store, span)
        provider = Answering([answer()])
        run = formalize_store(store, provider, at=AT, force=True)
        assert run.already_checkable == 1
        assert provider.requests == []


class TestTheAuditTrail:
    def test_the_actor_is_the_formalizer(self, store, span):
        uncompiled(store, span)
        formalize_store(store, Answering([answer()]), at=AT)
        assert store.audit_for("A-0001")[-1].actor == FORMALIZATION_ACTOR

    def test_the_reason_names_the_predicate_it_compiled(self, store, span):
        uncompiled(store, span)
        formalize_store(store, Answering([answer()]), at=AT)
        assert "index_size_gb <= 50" in store.audit_for("A-0001")[-1].reason

    def test_the_reason_says_so_when_it_is_only_a_best_attempt(self, store, span):
        uncompiled(store, span)
        formalize_store(store, Answering([answer(predicate="the team stays motivated")]), at=AT)
        assert "best attempt" in store.audit_for("A-0001")[-1].reason

    def test_the_run_id_reaches_the_audit_row(self, store, span):
        uncompiled(store, span)
        formalize_store(store, Answering([answer()]), at=AT, run_id="RUN-0001")
        assert store.audit_for("A-0001")[-1].run_id == "RUN-0001"

    def test_the_pass_stamps_the_time_it_was_given(self, store, span):
        uncompiled(store, span)
        formalize_store(store, Answering([answer()]), at=AT)
        assert stored(store).created_at == AT

    def test_the_time_defaults_to_now_and_is_timezone_aware(self, store, span):
        # Invariant 5. An expiry condition is a comparison against wall-clock
        # time, so a naive timestamp here is a wrong answer later.
        before = datetime.now(UTC)
        uncompiled(store, span)
        formalize_store(store, Answering([answer()]))
        written = stored(store).created_at
        assert written.tzinfo is not None
        assert written >= before


class TestRefusals:
    def test_a_refused_assumption_is_reported_rather_than_raised(self, store, span):
        # A pass over two hundred assumptions that dies on the third has said
        # nothing about the rest.
        uncompiled(store, span)
        run = formalize_store(store, Refusing([]), at=AT)
        assert len(run.refusals) == 1
        assert isinstance(run.refusals[0], FormalizationRefusal)
        assert run.refusals[0].assumption_id == "A-0001"

    def test_a_refusal_writes_nothing(self, store, span):
        # Distinct from an uncheckable formalization: there is no attempt to
        # keep, so there is nothing to write and nothing to mark.
        uncompiled(store, span)
        formalize_store(store, Refusing([]), at=AT)
        assert stored(store).version == 1

    def test_a_refusal_leaves_no_trace_the_next_pass_would_read_as_an_attempt(self, store, span):
        # Nothing was kept, so nothing was attempted in the sense the second
        # check means -- a later pass is free to try again.
        uncompiled(store, span)
        formalize_store(store, Refusing([]), at=AT)
        run = formalize_store(store, Answering([answer()]), at=AT)
        assert run.already_attempted == 0
        assert len(run.checkable) == 1

    def test_a_refusal_does_not_stop_the_assumptions_after_it(self, store, span):
        uncompiled(store, span, assumption_id="A-0001")
        uncompiled(store, span, assumption_id="A-0002")
        run = formalize_store(store, Refusing([]), at=AT)
        assert len(run.refusals) == 2


class TestTheRunReport:
    def test_checkable_and_marked_split_what_was_written(self, store, span):
        # The difference a person acts on: `marked` is the list somebody has to
        # look at, and `checkable` is what the monitor can reach a verdict about.
        uncompiled(store, span, assumption_id="A-0001")
        uncompiled(store, span, assumption_id="A-0002")
        run = formalize_store(store, Answering([answer(), answer(predicate="morale is fine")]))
        assert len(run.checkable) == 1
        assert len(run.marked) == 1

    def test_skipped_totals_the_two_reasons_a_call_was_not_made(self, store, span):
        compiled(store, span, assumption_id="A-0001")
        uncompiled(store, span, assumption_id="A-0002")
        formalize_store(store, Answering([answer(predicate="still prose")]), at=AT)
        run = formalize_store(store, Answering([answer()]), at=AT)
        assert run.already_checkable == 1
        assert run.already_attempted == 1
        assert run.skipped == 2

    def test_calls_are_counted(self, store, span):
        uncompiled(store, span)
        run = formalize_store(store, Answering([answer()]), at=AT)
        assert run.calls == 1

    def test_a_pass_over_an_empty_store_says_nothing_happened(self, store):
        run = formalize_store(store, Answering([]), at=AT)
        assert run == FormalizationRun()
        assert run.skipped == 0
