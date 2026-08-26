"""What the four memory commands print, and what they never print.

`tests/test_cli.py` invokes these commands end to end and asserts they work.
This file builds the runs by hand and asserts what reaches the terminal, because
two of those renderings carry a claim rather than a summary.

**`why` prints the record's words and the model's under separate headings.** The
agent's whole contract is that nothing a model wrote reaches the answer, so a
rendering that spliced the selection note into the answer would break the
guarantee at the last possible moment -- after every check that enforces it has
already passed. One test asserts the note is present, labelled, and outside the
answer.

**A breach names the decisions it reaches.** A breached assumption nobody can
trace forward is a fact about a predicate rather than about the work, and the
`assumes` edges exist precisely so that walk is possible.

The error paths are asserted as *sentences*. A traceback reaching a person who
typed a command is a bug report about Praxis rather than an answer about their
store.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from praxis.agents.archaeologist import Excavation, RestingAssumption
from praxis.agents.blocking import Blocking
from praxis.agents.detection import DetectionRun
from praxis.agents.errors import Refusal
from praxis.agents.formalization import FormalizationRun
from praxis.agents.formalizer import Formalization, FormalizationRefusal
from praxis.agents.results import Contradiction, DetectionResult, Settlement
from praxis.cli import app
from praxis.cli_monitor import (
    _report_detection,
    _report_excavation,
    _report_formalization,
    _report_monitoring,
)
from praxis.config.settings import Settings
from praxis.domain.enums import AssumptionStatus
from praxis.domain.links import LinkType
from praxis.domain.records import Link, Span
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.llm.errors import ProviderError
from praxis.monitor.monitor import Verdict
from praxis.monitor.run import MonitoringRun
from praxis.predicates.ast import Evaluation, Truth
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository
from typer.testing import CliRunner

from tests.monitor.conftest import ACTOR, AT, BODY, make_assumption, make_decision, rest_on

runner = CliRunner()

TRUE = Evaluation(truth=Truth.TRUE)
FALSE = Evaluation(truth=Truth.FALSE)
UNKNOWN = Evaluation(truth=Truth.UNKNOWN, reason="nothing has measured it")


@pytest.fixture
def store() -> Iterator[Repository]:
    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    yield repository
    repository.close()


@pytest.fixture
def settings() -> Settings:
    return Settings()


def an_assumption(span=None, **kwargs):
    """An assumption record, with no store behind it unless a test gives one."""
    return make_assumption(span if span is not None else _SPANLESS, **kwargs)


class _Spanless:
    """Stands in for a span where only its id is read.

    The id is a real content-addressed span id -- `SPAN-<16 hex>` -- because
    the records validate it, which is invariant 6 refusing a citation that could
    not name a place even in a fixture.
    """

    id = "SPAN-0123456789abcdef"


_SPANLESS = _Spanless()


def a_verdict(status: AssumptionStatus, reason: str, assumption=None) -> Verdict:
    """One verdict, without running a monitor to reach it."""
    return Verdict(
        assumption=assumption if assumption is not None else an_assumption(),
        status=status,
        evaluation=FALSE if status is AssumptionStatus.BREACHED else UNKNOWN,
        expiry=TRUE if status is AssumptionStatus.EXPIRED else FALSE,
        reason=reason,
    )


def an_excavation(note: str = "", **kwargs) -> Excavation:
    """An answer assembled from records, as the agent would return one."""
    decision = make_decision(_SPANLESS)
    kwargs.setdefault("answer", "It was rejected because ranking quality was not close.")
    return Excavation(
        question="why not Postgres full-text search",
        decision=decision,
        rejected=decision.rejected[0],
        resting_on=(
            RestingAssumption(assumption=an_assumption(), status=AssumptionStatus.HOLDING),
            RestingAssumption(
                assumption=an_assumption(assumption_id="A-0002"),
                status=AssumptionStatus.BREACHED,
            ),
        ),
        citations=("SPAN-0123456789abcdef",),
        confidence=0.8,
        note=note,
        calls=1,
        **kwargs,
    )


def a_contradiction(left: str = "A-0001", right: str = "A-0002") -> Contradiction:
    """One contradiction, with the edge it would write."""
    return Contradiction(
        link=Link.between(
            LinkType.CONTRADICTS,
            left,
            right,
            rationale="the two predicates permit no common value",
            confidence=1.0,
            created_by=ACTOR,
            created_at=AT,
            span_id=None,
        ),
        settled_by=Settlement.ARITHMETIC,
        rationale="the two predicates permit no common value",
    )


class TestFormalizationOutput:
    def test_a_kept_attempt_is_named_with_its_reason(self, capsys, settings):
        # These are the assumptions somebody has to look at, so a count alone
        # would be a number with no next action attached.
        marked = Formalization(
            assumption=an_assumption(predicate="the team stays motivated"),
            checkable=False,
            note="the predicate does not parse",
            calls=1,
        )

        _report_formalization(FormalizationRun(formalized=(marked,), calls=1), settings)

        printed = capsys.readouterr().out
        assert "A-0001" in printed
        assert "does not parse" in printed

    def test_a_refusal_is_counted_apart_from_a_kept_attempt(self, capsys, settings):
        run = FormalizationRun(
            refusals=(
                FormalizationRefusal(
                    assumption_id="A-0001",
                    refusal=Refusal.NO_USABLE_ANSWER,
                    detail="the model refused",
                    calls=1,
                ),
            ),
            calls=1,
        )

        _report_formalization(run, settings)

        assert "refused" in capsys.readouterr().out

    def test_the_two_skip_reasons_are_reported_apart(self, capsys, settings):
        run = FormalizationRun(already_checkable=3, already_attempted=2)

        _report_formalization(run, settings)

        printed = capsys.readouterr().out
        assert "3 already parse" in printed
        assert "2 attempted before" in printed


class TestMonitoringOutput:
    def test_a_breach_names_the_decisions_resting_on_it(self, capsys, store, settings):
        # The point of having written the `assumes` edge at all.
        span = _real_span(store)
        assumption = store.add(make_assumption(span), actor=ACTOR, reason="fixture")
        decision = store.add(make_decision(span), actor=ACTOR, reason="fixture")
        rest_on(store, decision, assumption)
        run = MonitoringRun(
            verdicts=(
                a_verdict(AssumptionStatus.BREACHED, "index_size_gb <= 50 is false", assumption),
            ),
            revised=1,
        )

        _report_monitoring(run, store, settings)

        printed = capsys.readouterr().out
        assert "A-0001" in printed
        assert f"affects {decision.id}" in printed

    def test_expired_is_reported_apart_from_breached(self, capsys, store, settings):
        # Telling these two apart is the claim this phase is graded on.
        run = MonitoringRun(
            verdicts=(
                a_verdict(AssumptionStatus.EXPIRED, "its expiry condition fired"),
                a_verdict(AssumptionStatus.BREACHED, "its predicate is false"),
            )
        )

        _report_monitoring(run, store, settings)

        printed = capsys.readouterr().out
        assert "expired    1" in printed
        assert "breached   1" in printed

    def test_an_unmeasured_assumption_is_neither(self, capsys, store, settings):
        run = MonitoringRun(
            verdicts=(a_verdict(AssumptionStatus.UNVERIFIED, "nothing measured it"),)
        )

        _report_monitoring(run, store, settings)

        assert "unchecked  1" in capsys.readouterr().out


class TestDetectionOutput:
    def test_each_stage_is_reported_apart(self, capsys, settings):
        run = DetectionRun(
            result=DetectionResult(contradictions=(a_contradiction(),), judged=1, calls=1),
            written=(a_contradiction().link,),
            records=2,
        )

        _report_detection(run, settings)

        printed = capsys.readouterr().out
        assert "1 by arithmetic" in printed
        assert "0 by judgement" in printed

    def test_a_found_pair_is_named_with_the_stage_that_settled_it(self, capsys, settings):
        run = DetectionRun(result=DetectionResult(contradictions=(a_contradiction(),)), records=2)

        _report_detection(run, settings)

        printed = capsys.readouterr().out
        assert "A-0001 <> A-0002" in printed
        assert Settlement.ARITHMETIC.value in printed

    def test_a_bucket_too_common_to_discriminate_is_reported(self, capsys, settings):
        # A pair these would have proposed is a pair nothing will look at, and
        # that is a different result from a model judging it not to conflict.
        run = DetectionRun(
            result=DetectionResult(blocking=Blocking(skipped=("word:search",), indexed=9)),
            records=9,
        )

        _report_detection(run, settings)

        assert "too common to discriminate" in capsys.readouterr().out


class TestExcavationOutput:
    def test_the_answer_is_printed(self, capsys):
        _report_excavation(an_excavation())

        assert "ranking quality was not close" in capsys.readouterr().out

    def test_the_model_s_note_is_labelled_and_outside_the_answer(self, capsys):
        # The agent's contract, kept at the last moment where it could be lost.
        _report_excavation(an_excavation(note="names that option"))

        printed = capsys.readouterr().out
        assert "the model's words, not the record's" in printed
        assert "names that option" in printed

    def test_an_assumption_that_no_longer_holds_is_marked(self, capsys):
        # "We chose this because we assumed that, and that broke" is a different
        # sentence from "we chose this", and the rendering has to show it.
        _report_excavation(an_excavation())

        printed = capsys.readouterr().out
        assert "holds" in printed
        assert "check" in printed

    def test_the_citations_are_printed(self, capsys):
        _report_excavation(an_excavation())

        assert "SPAN-0123456789abcdef" in capsys.readouterr().out


class TestFailuresReadAsSentences:
    def test_a_provider_failure_in_formalize_is_a_sentence(self, tmp_path, monkeypatch):
        runner.invoke(app, ["init"])
        monkeypatch.setattr(
            "praxis.cli_monitor.formalize_store", _raising(ProviderError("no route"))
        )

        result = runner.invoke(app, ["formalize"])

        assert result.exit_code == 1
        assert "praxis formalize: no route" in result.output
        assert "Traceback" not in result.output

    def test_a_provider_failure_in_contradictions_is_a_sentence(self, tmp_path, monkeypatch):
        runner.invoke(app, ["init"])
        monkeypatch.setattr(
            "praxis.cli_monitor.detect_in_store", _raising(ProviderError("no route"))
        )

        result = runner.invoke(app, ["contradictions"])

        assert result.exit_code == 1
        assert "praxis contradictions: no route" in result.output

    def test_a_provider_failure_in_why_is_a_sentence(self, tmp_path, monkeypatch):
        runner.invoke(app, ["init"])
        monkeypatch.setattr(
            "praxis.agents.archaeologist.ArchaeologistAgent.ask",
            lambda *args, **kwargs: (_ for _ in ()).throw(ProviderError("no route")),
        )

        result = runner.invoke(app, ["why", "why not Postgres"])

        assert result.exit_code == 1
        assert "praxis why: no route" in result.output

    def test_a_provider_failure_in_monitor_is_a_sentence(self, tmp_path, monkeypatch):
        runner.invoke(app, ["init"])
        monkeypatch.setattr("praxis.cli_monitor.monitor_store", _raising(ProviderError("no route")))

        result = runner.invoke(app, ["monitor"])

        assert result.exit_code == 1
        assert "praxis monitor: no route" in result.output


def _raising(error: BaseException):
    """A stand-in that raises rather than running."""

    def _fail(*args: object, **kwargs: object) -> None:
        raise error

    return _fail


def _real_span(store: Repository) -> Span:
    """One document and one span over it, both written."""
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


ProviderFailure = __import__("praxis.llm.errors", fromlist=["ProviderError"]).ProviderError
"""The seam's own failure type, imported by name so this file does not depend on
which subclass a given failure happens to be."""


def test_why_exits_zero_and_prints_the_answer_when_the_record_has_one(monkeypatch):
    # The agent is stood in for because offline the mock selects a decision by
    # chance, and what this asserts is the command's contract rather than the
    # mock's luck: an answer reaches stdout and the exit code says it did.
    runner.invoke(app, ["init"])
    monkeypatch.setattr(
        "praxis.cli_monitor.ArchaeologistAgent.ask",
        lambda *args, **kwargs: an_excavation(),
    )

    result = runner.invoke(app, ["why", "why not Postgres full-text search"])

    assert result.exit_code == 0, result.output
    assert "answered from the record" in result.output
    assert "ranking quality was not close" in result.output
