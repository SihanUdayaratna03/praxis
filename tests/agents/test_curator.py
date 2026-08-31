"""CuratorAgent: what stops standing, and the far longer list of what does not.

Three claims run through this file.

The first is that **nothing is deleted and nothing new was invented**. A merge
is a `supersedes` edge Phase 1 defined and a retirement is `Repository.retract`,
so the tests assert against those two mechanisms rather than against a curator's
own vocabulary. A test that passed because the curator had invented a third way
to make a record go away would be the failure this file exists to prevent.

The second is that **the refusals are the component**. `TestWhatIsRefused` is
the longest class here on purpose, and the refusal that matters most has its own
class: an idle assumption a live decision rests on is a finding for a person,
not dead weight for a curator, and retiring it would remove the very thing that
makes the decision worth re-reading.

The third is that **every refusal carries a control**. Phase 7's lesson: a test
asserting the curator declines passes trivially against a curator that declines
everything, so each refusal is paired with the same store one condition away,
where the curator must act.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from praxis.agents.curator import (
    CURATOR_NAME,
    IDLE_DAYS,
    MONITOR_ACTOR,
    CuratorAgent,
    MergeReason,
    Refusal,
    idle_before,
    supersedes_link,
)
from praxis.config.models import NON_LLM_AGENTS, UnknownAgentError, role_for_agent, routed_agents
from praxis.domain.enums import AssumptionStatus, DecisionScope, Impact
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Document, Link, RejectedOption, Span
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.monitor.run import MONITOR_ACTOR as RUN_MONITOR_ACTOR
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=IDLE_DAYS + 30)
RECENTLY = NOW - timedelta(days=IDLE_DAYS - 30)
ACTOR = "test"

BODY = """\
# ADR: the product search index

We are going with OpenSearch on managed nodes.

The index stays under 50 GB for the next year.
"""

OTHER_BODY = """\
# Revision: the product search index

That is no longer true, and it has not been true for a while.

The index will pass 50 GB this year.
"""


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


def make_span(store: Repository, doc_id: str, body: str, uri: str) -> Span:
    """A span through the real adapter, so a cited coordinate is a real one."""
    source = MARKDOWN_ADAPTER.normalise(body.encode("utf-8"), source_uri=uri)
    document = document_from(source, doc_id=doc_id, ingested_at=LONG_AGO)
    store.add(document, actor=ACTOR, reason="fixture")
    span = Span.covering(
        document,
        0,
        len(document.content.encode("utf-8")),
        created_by=ACTOR,
        created_at=LONG_AGO,
    )
    return store.add(span, actor=ACTOR, reason="fixture")


@pytest.fixture
def home(store: Repository) -> Span:
    return make_span(store, "DOC-0001", BODY, "adr.md")


@pytest.fixture
def elsewhere(store: Repository) -> Span:
    return make_span(store, "DOC-0002", OTHER_BODY, "revision.md")


def write_assumption(  # noqa: PLR0913 -- every field a test varies, named
    store: Repository,
    span: Span,
    *,
    number: int = 1,
    predicate: str = "index_size_gb <= 50",
    statement: str = "the index stays under 50 GB",
    status: AssumptionStatus = AssumptionStatus.UNVERIFIED,
    at: datetime = LONG_AGO,
) -> Assumption:
    """One assumption in the store, at the age a test needs it to be."""
    evaluated = None if status is AssumptionStatus.UNVERIFIED else at
    return store.add(
        Assumption(
            id=f"A-{number:04d}",
            statement=statement,
            predicate=predicate,
            expiry_condition='on_event("the index is re-sharded")',
            status=status,
            last_evaluated_at=evaluated,
            span_id=span.id,
            confidence=0.8,
            created_by="AssumptionFormalizer",
            created_at=at,
        ),
        actor=ACTOR,
        reason="fixture",
        at=at,
    )


def write_decision(store: Repository, span: Span, assumption: Assumption) -> Decision:
    """A decision resting on an assumption, through the real `assumes` edge."""
    decision = store.add(
        Decision(
            id="D-0001",
            title="the product search index",
            chosen="OpenSearch on managed nodes",
            rejected=(RejectedOption(option="Postgres", reason="ranking quality"),),
            decision_maker="Nadeesha",
            decided_at=LONG_AGO,
            scope=DecisionScope.TEAM,
            impact=Impact.MEDIUM,
            span_id=span.id,
            confidence=0.9,
            created_by=ACTOR,
            created_at=LONG_AGO,
        ),
        actor=ACTOR,
        reason="fixture",
    )
    store.add(
        Link.between(
            LinkType.ASSUMES,
            decision.id,
            assumption.id,
            rationale="the decision rests on it",
            confidence=1.0,
            created_by=ACTOR,
            created_at=LONG_AGO,
        ),
        actor=ACTOR,
        reason="fixture",
    )
    return decision


def write_contradiction(store: Repository, left: Assumption, right: Assumption) -> Link:
    """The `contradicts` edge Phase 5's detector would have written."""
    return store.add(
        Link.between(
            LinkType.CONTRADICTS,
            left.id,
            right.id,
            rationale="the two predicates permit no common value",
            confidence=1.0,
            created_by="ContradictionDetector",
            created_at=LONG_AGO,
        ),
        actor=ACTOR,
        reason="fixture",
    )


class TestMerging:
    """Two assumptions collapsing into one, and which mechanism does it."""

    def test_two_predicates_with_the_same_shape_are_a_duplicate(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """Same quantity, same constraint, different spelling of the sentence.

        Nobody revised anything here -- two agents read the same claim twice,
        which an append-only store has no way to notice on its own.
        """
        write_assumption(store, home, number=1)
        write_assumption(
            store,
            elsewhere,
            number=2,
            statement="we assume the index stays below fifty gigabytes",
            at=RECENTLY,
        )

        result = CuratorAgent(store).curate(at=NOW)

        assert len(result.merges) == 1
        assert result.merges[0].reason is MergeReason.DUPLICATE

    def test_the_later_assumption_is_the_survivor(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """`supersedes` points newer to older, which is Phase 1's direction."""
        older = write_assumption(store, home, number=1)
        newer = write_assumption(store, elsewhere, number=2, at=RECENTLY)

        merge = CuratorAgent(store).curate(at=NOW).merges[0]

        assert merge.survivor.id == newer.id
        assert merge.superseded.id == older.id

    def test_a_contradiction_across_documents_is_a_revision(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """The corpus's own shape: a note months later saying it is no longer true.

        The `contradicts` edge is `ContradictionDetector`'s judgement, made in
        Phase 5 and paid for once. This reads it rather than re-deciding it,
        which is why the curator makes no model call.
        """
        older = write_assumption(store, home, number=1)
        newer = write_assumption(
            store,
            elsewhere,
            number=2,
            predicate="index_size_gb > 50",
            statement="the index will pass 50 GB this year",
            at=RECENTLY,
        )
        write_contradiction(store, older, newer)

        result = CuratorAgent(store).curate(at=NOW)

        assert len(result.merges) == 1
        assert result.merges[0].reason is MergeReason.REVISION
        assert result.merges[0].survivor.id == newer.id

    def test_a_merge_becomes_a_supersedes_edge_and_nothing_else(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """The mechanism is Phase 1's, asserted against `LinkType` directly."""
        write_assumption(store, home, number=1)
        write_assumption(store, elsewhere, number=2, at=RECENTLY)
        merge = CuratorAgent(store).curate(at=NOW).merges[0]

        link = supersedes_link(merge, at=NOW)

        assert link.link_type is LinkType.SUPERSEDES
        assert link.source_id == merge.survivor.id
        assert link.target_id == merge.superseded.id
        assert link.created_by == CURATOR_NAME

    def test_a_duplicate_edge_is_certain_and_a_revision_inherits_a_confidence(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """Two runs agree that two parsed predicates match; a revision is judged.

        So the duplicate edge carries 1.0 and the revision carries the
        survivor's own confidence rather than a number this agent invented.
        """
        write_assumption(store, home, number=1)
        write_assumption(store, elsewhere, number=2, at=RECENTLY)
        duplicate = CuratorAgent(store).curate(at=NOW).merges[0]

        assert supersedes_link(duplicate, at=NOW).confidence == 1.0

    def test_a_tie_on_creation_time_is_broken_rather_than_refused(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """Two assumptions from one ingestion run share a timestamp.

        Refusing the pair would leave the commonest duplicate in the store
        forever; breaking the tie on the id makes the outcome the same on every
        pass, which is what re-runnability needs.
        """
        write_assumption(store, home, number=1)
        write_assumption(store, elsewhere, number=2)

        merge = CuratorAgent(store).curate(at=NOW).merges[0]

        assert merge.survivor.id == "A-0002"

    def test_curation_is_the_same_on_a_second_pass(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """Deterministic, because nothing here is a model."""
        write_assumption(store, home, number=1)
        write_assumption(store, elsewhere, number=2, at=RECENTLY)
        agent = CuratorAgent(store)

        first, second = agent.curate(at=NOW), agent.curate(at=NOW)

        assert [merge.survivor.id for merge in first.merges] == [
            merge.survivor.id for merge in second.merges
        ]


class TestRetiring:
    """Withdrawing an assumption nothing has ever settled."""

    def test_an_idle_assumption_is_retired(self, store: Repository, home: Span) -> None:
        write_assumption(store, home)

        result = CuratorAgent(store).curate(at=NOW)

        assert len(result.retirements) == 1
        assert result.retirements[0].assumption.id == "A-0001"

    def test_the_retirement_reports_how_long_it_went_unsettled(
        self, store: Repository, home: Span
    ) -> None:
        """The threshold travels with the decision, so a reader can disagree."""
        write_assumption(store, home)

        retirement = CuratorAgent(store).curate(at=NOW).retirements[0]

        assert retirement.idle_days == IDLE_DAYS + 30

    def test_the_retirement_rate_has_a_denominator(self, store: Repository, home: Span) -> None:
        """One retired of two live is a half, and zero of zero is no measurement."""
        write_assumption(store, home, number=1)
        write_assumption(
            store,
            home,
            number=2,
            predicate="query_latency_ms <= 200",
            status=AssumptionStatus.HOLDING,
        )

        result = CuratorAgent(store).curate(at=NOW)

        assert result.considered == 2
        assert result.retirement_rate == pytest.approx(0.5)

    def test_an_empty_store_reports_no_rate_rather_than_a_low_one(self, store: Repository) -> None:
        result = CuratorAgent(store).curate(at=NOW)

        assert result.considered == 0
        assert result.retirement_rate == 0.0

    def test_the_idle_window_is_exported_so_two_spellings_cannot_disagree(self) -> None:
        assert idle_before(NOW) == NOW - timedelta(days=IDLE_DAYS)


class TestTheRefusalThatMattersMost:
    """An idle assumption a live decision rests on, and its control."""

    def test_it_is_not_retired(self, store: Repository, home: Span) -> None:
        """Removing it would remove what makes the decision worth re-reading."""
        assumption = write_assumption(store, home)
        write_decision(store, home, assumption)

        result = CuratorAgent(store).curate(at=NOW)

        assert not result.retirements
        assert [entry.refusal for entry in result.declined] == [Refusal.RESTS_ON_A_DECISION]

    def test_the_same_assumption_with_no_decision_is_retired(
        self, store: Repository, home: Span
    ) -> None:
        """The control. Without it the test above passes against a curator that
        retires nothing, which is the opposite defect."""
        write_assumption(store, home)

        result = CuratorAgent(store).curate(at=NOW)

        assert len(result.retirements) == 1

    def test_the_refusal_names_the_decision_a_person_has_to_look_at(
        self, store: Repository, home: Span
    ) -> None:
        """A refusal nobody can act on is a refusal that gets ignored."""
        assumption = write_assumption(store, home)
        write_decision(store, home, assumption)

        declined = CuratorAgent(store).curate(at=NOW).declined[0]

        assert "D-0001" in declined.detail

    def test_a_retracted_decision_does_not_hold_an_assumption_alive(
        self, store: Repository, home: Span
    ) -> None:
        """`Repository.get` returns a retracted record rather than `None`.

        Phase 8 found this the hard way. A curator testing only for `None` would
        refuse to retire an assumption whose sole dependant was itself
        withdrawn, and the store would keep both forever.
        """
        assumption = write_assumption(store, home)
        write_decision(store, home, assumption)
        store.retract(Decision, "D-0001", actor=ACTOR, reason="withdrawn")

        result = CuratorAgent(store).curate(at=NOW)

        assert len(result.retirements) == 1


class TestWhatIsRefused:
    """Every other refusal, each with the control that gives it meaning."""

    def test_an_assumption_the_monitor_has_settled_is_left_alone(
        self, store: Repository, home: Span
    ) -> None:
        """It is memory that works. Curating it would be curating the product."""
        write_assumption(store, home, status=AssumptionStatus.HOLDING)

        result = CuratorAgent(store).curate(at=NOW)

        assert not result.retirements
        assert [entry.refusal for entry in result.declined] == [Refusal.ALREADY_EVALUATED]

    def test_an_assumption_the_audit_trail_shows_the_monitor_touched_is_left_alone(
        self, store: Repository, home: Span
    ) -> None:
        """The audit trail is the query, and it is Phase 5's own trail.

        Status alone would miss an assumption the monitor revised back to
        unverified, so idleness is checked against the trail as well as the
        field. No parallel bookkeeping anywhere.
        """
        assumption = write_assumption(store, home)
        store.revise(assumption, actor=MONITOR_ACTOR, reason="re-read", at=RECENTLY)

        result = CuratorAgent(store).curate(at=NOW)

        assert not result.retirements

    def test_an_assumption_some_other_actor_revised_is_still_idle(
        self, store: Repository, home: Span
    ) -> None:
        """The control: it is the *monitor's* silence that makes it idle.

        A formalizer rewriting a predicate is not evidence that anything has
        ever been settled, so a curator keyed on any revision at all would
        never retire an assumption the pipeline had touched twice.

        It also pins where the idle clock starts. `revise` overwrites
        `created_at`, so measuring off the current version would make this
        assumption 30 days old; measuring off the audit trail's first event
        keeps it at 90, which is what "has gone this long unsettled" means.
        """
        assumption = write_assumption(store, home)
        store.revise(assumption, actor="AssumptionFormalizer", reason="compiled", at=RECENTLY)

        result = CuratorAgent(store).curate(at=NOW)

        assert len(result.retirements) == 1

    def test_an_assumption_written_too_recently_is_not_retired(
        self, store: Repository, home: Span
    ) -> None:
        write_assumption(store, home, at=RECENTLY)

        result = CuratorAgent(store).curate(at=NOW)

        assert not result.retirements
        assert [entry.refusal for entry in result.declined] == [Refusal.NOT_YET_IDLE]

    def test_one_day_past_the_window_is_retired(self, store: Repository, home: Span) -> None:
        """The control, at the boundary rather than far from it."""
        write_assumption(store, home, at=NOW - timedelta(days=IDLE_DAYS))

        result = CuratorAgent(store).curate(at=NOW)

        assert len(result.retirements) == 1

    def test_a_pair_already_carrying_a_supersedes_edge_is_not_merged_again(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """Re-running a curation pass over a curated store proposes nothing."""
        older = write_assumption(store, home, number=1)
        newer = write_assumption(store, elsewhere, number=2, at=RECENTLY)
        store.add(
            Link.between(
                LinkType.SUPERSEDES,
                newer.id,
                older.id,
                rationale="already curated",
                confidence=1.0,
                created_by=CURATOR_NAME,
                created_at=RECENTLY,
            ),
            actor=ACTOR,
            reason="fixture",
        )

        result = CuratorAgent(store).curate(at=NOW)

        assert not result.merges
        assert Refusal.ALREADY_SUPERSEDED in [entry.refusal for entry in result.declined]

    def test_a_contradiction_inside_one_document_is_not_a_revision(
        self, store: Repository, home: Span
    ) -> None:
        """Two conflicting claims on one page are a contradiction to look at.

        Collapsing them would silently pick a winner between two things stated
        together, which nobody asked this agent to do.
        """
        older = write_assumption(store, home, number=1)
        newer = write_assumption(store, home, number=2, predicate="index_size_gb > 50", at=RECENTLY)
        write_contradiction(store, older, newer)

        result = CuratorAgent(store).curate(at=NOW)

        assert not result.merges
        assert Refusal.SAME_DOCUMENT in [entry.refusal for entry in result.declined]

    def test_the_same_pair_across_two_documents_is_a_revision(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """The control for the same-document refusal."""
        older = write_assumption(store, home, number=1)
        newer = write_assumption(
            store, elsewhere, number=2, predicate="index_size_gb > 50", at=RECENTLY
        )
        write_contradiction(store, older, newer)

        assert len(CuratorAgent(store).curate(at=NOW).merges) == 1

    def test_an_unparseable_predicate_is_never_a_duplicate_of_anything(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """ "I cannot read either of these" is not evidence that they agree.

        Two assumptions still holding prose rather than a compiled predicate --
        which is exactly the state Phase 4's extractor leaves them in -- must
        not collapse into one on the strength of both being unreadable.
        """
        write_assumption(store, home, number=1, predicate="it should be fine")
        write_assumption(store, elsewhere, number=2, predicate="it should be fine", at=RECENTLY)

        result = CuratorAgent(store).curate(at=NOW)

        assert not result.merges

    def test_two_readable_predicates_that_differ_are_not_a_duplicate(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """The control from the other side: readable, and genuinely different."""
        write_assumption(store, home, number=1, predicate="index_size_gb <= 50")
        write_assumption(
            store, elsewhere, number=2, predicate="query_latency_ms <= 200", at=RECENTLY
        )

        assert not CuratorAgent(store).curate(at=NOW).merges

    def test_a_record_about_to_be_superseded_is_not_also_a_retirement(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """One record cannot be both replaced and abandoned.

        Counting it as both would put the same assumption in two rates, and the
        retirement rate is a number the phase report prints.
        """
        write_assumption(store, home, number=1)
        write_assumption(store, elsewhere, number=2, at=RECENTLY)

        result = CuratorAgent(store).curate(at=NOW)

        assert len(result.merges) == 1
        assert "A-0001" not in [entry.assumption.id for entry in result.retirements]


class TestTheAgentsPlaceInTheSystem:
    """Where it sits in the routing table, and what it refuses to be given."""

    def test_the_curator_makes_no_model_call(self) -> None:
        """ADR 0031. Eighth consecutive component on the arithmetic side.

        Everything it decides is already a record: an edge Phase 5 wrote, a
        predicate the parser reads, `created_at` ordering, and an audit trail.
        """
        assert CURATOR_NAME in NON_LLM_AGENTS
        assert CURATOR_NAME not in routed_agents()

    def test_asking_for_its_route_raises_rather_than_returning_one(self) -> None:
        """A deterministic agent reaching for a model is the drift the set catches."""
        with pytest.raises(UnknownAgentError, match="deterministic by design"):
            role_for_agent(CURATOR_NAME)

    def test_the_monitor_actor_is_spelled_the_same_in_both_modules(self) -> None:
        """Drift is a failing test rather than a silent one.

        The curator holds the string by value to avoid tying a governance pass
        to a monitoring one, which means only a test can hold the two together.
        """
        assert MONITOR_ACTOR == RUN_MONITOR_ACTOR

    def test_a_negative_idle_window_is_refused(self, store: Repository) -> None:
        """It would retire assumptions written in the future."""
        with pytest.raises(ValueError, match="a number of days"):
            CuratorAgent(store, idle_days=-1)

    def test_a_retracted_assumption_is_never_considered(
        self, store: Repository, home: Span
    ) -> None:
        """`list_all` excludes them, and re-curating a withdrawn record would
        withdraw it twice."""
        write_assumption(store, home)
        store.retract(Assumption, "A-0001", actor=ACTOR, reason="already curated")

        result = CuratorAgent(store).curate(at=NOW)

        assert result.considered == 0
        assert not result.retirements


def test_a_document_is_a_document(store: Repository, home: Span) -> None:
    """The fixture writes real documents, so `_same_document` joins real ids."""
    assert store.get(Document, "DOC-0001") is not None


class TestThePairsThatAreNotCandidates:
    """Pairs the curator neither merges nor reports, because they never qualified."""

    def test_two_conflicting_claims_written_at_the_same_moment_are_left_standing(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """Neither came after the other, so neither revised the other.

        Two live claims in conflict are `ContradictionDetector`'s output and a
        person's problem. Picking a winner on the strength of an id would be
        this agent inventing a revision nobody wrote.
        """
        older = write_assumption(store, home, number=1)
        newer = write_assumption(store, elsewhere, number=2, predicate="index_size_gb > 50")
        write_contradiction(store, older, newer)

        result = CuratorAgent(store).curate(at=NOW)

        assert not result.merges
        assert Refusal.SAME_DOCUMENT not in [entry.refusal for entry in result.declined]

    def test_a_claim_dated_after_the_moment_asked_about_is_not_yet_a_revision(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """As far as this pass is concerned it has not been written."""
        older = write_assumption(store, home, number=1)
        newer = write_assumption(
            store,
            elsewhere,
            number=2,
            predicate="index_size_gb > 50",
            at=NOW + timedelta(days=1),
        )
        write_contradiction(store, older, newer)

        assert not CuratorAgent(store).curate(at=NOW).merges

    def test_an_assumption_citing_a_span_the_store_does_not_hold_is_not_same_document(
        self, store: Repository, home: Span
    ) -> None:
        """Unreachable through the store's foreign keys, and guarded anyway.

        `_same_document` joins through spans because a record carries a
        `span_id` and nothing else about where it was read from. A missing span
        cannot mean "same document", so the pair falls through to the ordinary
        revision rule rather than being silently refused.
        """
        from praxis.agents.curator import _same_document  # noqa: PLC0415 -- private, on purpose

        present = write_assumption(store, home, number=1)
        absent = present.model_copy(update={"id": "A-0009", "span_id": "SPAN-0000000000000000"})

        assert not _same_document(store, present, absent)

    def test_an_empty_predicate_has_no_shape(self) -> None:
        """A record cannot hold one, and the guard is the reason a caller can pass
        anything a future extractor produces without this reading it as a match."""
        from praxis.agents.curator import _shape_of  # noqa: PLC0415 -- private, on purpose

        assert _shape_of("   ") is None

    def test_a_contradiction_against_a_retracted_assumption_proposes_nothing(
        self, store: Repository, home: Span, elsewhere: Span
    ) -> None:
        """The edge outlives the record, because nothing here is deleted.

        A curated store keeps its `contradicts` edges pointing at withdrawn
        assumptions forever, so a pass that followed one would keep proposing
        the merge it already made. The candidate set is narrowed to live
        records for exactly that reason.
        """
        older = write_assumption(store, home, number=1)
        newer = write_assumption(
            store, elsewhere, number=2, predicate="index_size_gb > 50", at=RECENTLY
        )
        write_contradiction(store, older, newer)
        store.retract(Assumption, older.id, actor=ACTOR, reason="already curated")

        assert not CuratorAgent(store).curate(at=NOW).merges
