"""The demo dataset, seeded from this repository's own committed history.

The claim being defended is that none of it is invented. Every span really
contains the text its record cites (invariant 6), every quantity is the exact
decimal the JSONL line holds (invariant 4), and the counts are the counts of
the real files rather than numbers written into a fixture.

The other half is that the demo is worth showing: `agent-implementation` has
enough history for `BiasDetective` to speak, three other classes do not and are
refused by name, and exactly one predicate is measurably false.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import praxis
import pytest
from praxis.agents.bias import MINIMUM_SAMPLE, BiasDetective
from praxis.demo.seed import ACTOR, seed
from praxis.domain.enums import AssumptionStatus, MatchQuality, RecordKind
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Document, Estimate, Outcome, Span
from praxis.eval.adrs import read_adr_predicates
from praxis.monitor.facts import load_facts
from praxis.monitor.run import monitor_store
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

REPO = Path(praxis.__file__).resolve().parent.parent
ADRS = REPO / "docs" / "adr"
DOGFOOD = REPO / "docs" / "dogfood"


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def seeded(store: Repository) -> Repository:
    seed(store, adr_dir=ADRS, dogfood_dir=DOGFOOD)
    return store


class TestWhatItWrites:
    def test_every_adr_becomes_a_decision(self, seeded: Repository):
        adrs = list(ADRS.glob("[0-9][0-9][0-9][0-9]-*.md"))
        assert len(seeded.list_all(Decision)) == len(adrs)

    def test_every_assumption_row_becomes_an_assumption(self, seeded: Repository):
        # The same rows praxis.eval already counts, so the two cannot disagree.
        assert len(seeded.list_all(Assumption)) == read_adr_predicates(ADRS).total

    def test_the_dogfood_corpus_becomes_estimates_and_outcomes(self, seeded: Repository):
        estimates = len(
            [
                line
                for line in (DOGFOOD / "estimates.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        )
        outcomes = len(
            [
                line
                for line in (DOGFOOD / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        )
        assert len(seeded.list_all(Estimate)) == estimates
        # One outcome per recorded result, plus an `unresolved` row for every
        # estimate nothing has answered. ADR 0022.
        assert len(seeded.list_all(Outcome)) == estimates
        assert (
            sum(
                1
                for o in seeded.list_all(Outcome)
                if o.match_quality is not MatchQuality.UNRESOLVED
            )
            == outcomes
        )

    def test_every_decision_rests_on_at_least_one_assumption(self, seeded: Repository):
        for decision in seeded.list_all(Decision):
            assert seeded.links_from(decision.id, [LinkType.ASSUMES])

    def test_the_audit_trail_names_the_seeder(self, seeded: Repository):
        decision = seeded.list_all(Decision)[0]
        assert {event.actor for event in seeded.audit_for(decision.id)} == {ACTOR}

    def test_an_empty_directory_seeds_nothing_rather_than_failing(
        self, store: Repository, tmp_path: Path
    ):
        (tmp_path / "dogfood").mkdir()
        for name in ("estimates.jsonl", "outcomes.jsonl"):
            (tmp_path / "dogfood" / name).write_text("", encoding="utf-8")
        report = seed(store, adr_dir=tmp_path, dogfood_dir=tmp_path / "dogfood")
        assert report.decisions == 0
        assert report.estimates == 0


class TestNothingIsInvented:
    def test_every_span_really_contains_what_its_record_cites(self, seeded: Repository):
        """Invariant 6, over the whole seeded corpus rather than a sample."""
        documents = {d.id: d for d in seeded.list_all(Document)}
        for span in seeded.list_all(Span):
            document = documents[span.doc_id]
            raw = document.content_bytes[span.start_byte : span.end_byte]
            assert raw.decode("utf-8") == span.text

    def test_a_decision_quotes_its_adr(self, seeded: Repository):
        spans = {s.id: s for s in seeded.list_all(Span)}
        for decision in seeded.list_all(Decision):
            assert decision.chosen == spans[decision.span_id].text

    def test_an_assumption_predicate_appears_in_the_row_it_cites(self, seeded: Repository):
        spans = {s.id: s for s in seeded.list_all(Span)}
        for assumption in seeded.list_all(Assumption):
            assert assumption.predicate in spans[assumption.span_id].text

    def test_quantities_survive_as_exact_decimals(self, seeded: Repository):
        """Invariant 4. A float would have turned 3.1 into 3.100000000000000088."""
        by_subject = {e.subject[:12]: e for e in seeded.list_all(Estimate)}
        phase_10 = next(e for k, e in by_subject.items() if k.startswith("Phase 10"))
        assert phase_10.active_quantity == Decimal("3.1")
        assert str(phase_10.active_quantity) == "3.1"

    def test_every_timestamp_is_timezone_aware(self, seeded: Repository):
        """Invariant 5."""
        for estimate in seeded.list_all(Estimate):
            assert estimate.estimated_at.tzinfo is not None
        for decision in seeded.list_all(Decision):
            assert decision.decided_at.tzinfo is not None


class TestTheDemoIsWorthShowing:
    def test_one_class_has_enough_history_to_speak(self, seeded: Repository):
        speaking = [f for f in BiasDetective(seeded).all_factors() if f.speaks]
        assert [f.group.work_class for f in speaking] == ["agent-implementation"]
        assert speaking[0].n >= MINIMUM_SAMPLE
        assert speaking[0].direction is not None

    def test_the_thin_classes_are_refused_by_name(self, seeded: Repository):
        """A refusal is the output most of the time, and it says why."""
        refused = [f for f in BiasDetective(seeded).all_factors() if not f.speaks]
        assert refused
        for factor in refused:
            assert factor.factor is None
            assert factor.reason

    def test_the_newest_class_refuses_at_one_sample(self, seeded: Repository):
        """`frontend` is EST-0012, closed by OUT-0012 at the end of Phase 11.

        Repinned when that outcome landed: the group moved from "not one
        resolved outcome" to "four short of five", which is the corpus growing
        rather than the test breaking.
        """
        by_class = {f.group.work_class: f for f in BiasDetective(seeded).all_factors()}
        assert by_class["frontend"].n == 1
        assert by_class["frontend"].speaks is False
        assert str(MINIMUM_SAMPLE) in by_class["frontend"].reason

    def test_the_monitor_finds_the_segmenter_breach_and_only_that(self, seeded: Repository):
        """The real payoff: a real decision invalidated by a real measurement."""
        run = monitor_store(seeded, supplied=load_facts(DOGFOOD / "facts.json"))
        assert len(run.verdicts) == len(seeded.list_all(Assumption))
        breached = [v.assumption.predicate for v in run.breached]
        assert breached == ["segmenter_f1 - paragraph_floor_f1 >= 0.05"]

    def test_the_breach_names_the_decision_resting_on_it(self, seeded: Repository):
        run = monitor_store(seeded, supplied=load_facts(DOGFOOD / "facts.json"))
        assert len(run.findings) == 1
        assert "D-" in run.findings[0].prosecution

    def test_some_assumptions_hold_and_some_have_expired(self, seeded: Repository):
        monitor_store(seeded, supplied=load_facts(DOGFOOD / "facts.json"))
        statuses = {a.status for a in seeded.list_all(Assumption)}
        assert AssumptionStatus.HOLDING in statuses
        assert AssumptionStatus.EXPIRED in statuses
        assert AssumptionStatus.BREACHED in statuses

    def test_every_estimate_carries_an_outcome(self, store: Repository):
        """ADR 0022, over the real corpus. No estimate is left as a silence."""
        report = seed(store, adr_dir=ADRS, dogfood_dir=DOGFOOD)
        assert report.outcomes == report.estimates

    def test_an_unanswered_estimate_becomes_an_unresolved_row(
        self, store: Repository, tmp_path: Path
    ):
        """ADR 0022, over a corpus written for it.

        Against a synthetic pair rather than `docs/dogfood/`, because the real
        corpus is fully resolved whenever a phase has just closed and would
        stop exercising this path.
        """
        dogfood = tmp_path / "dogfood"
        dogfood.mkdir()
        (dogfood / "estimates.jsonl").write_text(
            json.dumps(
                {
                    "id": "EST-9001",
                    "logged_at": "2026-09-02T00:00:00Z",
                    "owner": "somebody",
                    "subject": "a phase still in flight",
                    "work_class": "frontend",
                    "active_quantity": 3.0,
                    "blocked_quantity": 0.5,
                    "unit": "hours",
                    "confidence": 0.5,
                    "conditions": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (dogfood / "outcomes.jsonl").write_text("", encoding="utf-8")

        report = seed(store, adr_dir=tmp_path, dogfood_dir=dogfood)
        assert report.unresolved == 1
        assert report.outcomes == 1
        unresolved = store.list_all(Outcome)
        assert unresolved[0].match_quality is MatchQuality.UNRESOLVED
        # The schema refuses an unresolved row that also carries numbers.
        assert unresolved[0].active_quantity is None
        assert unresolved[0].resolved_at is None

    def test_nothing_calls_a_model(self, seeded: Repository):
        """The whole seed is deterministic; a demo that needed a key would be the bug."""
        run = monitor_store(seeded, supplied=load_facts(DOGFOOD / "facts.json"))
        assert run.calls == 0
        assert seeded.stats().records.get(RecordKind.SPAN, 0) > 0
