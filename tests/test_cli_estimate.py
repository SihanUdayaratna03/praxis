"""What `praxis estimates` prints, and what it refuses to print in red.

`tests/test_cli.py` invokes the command end to end and asserts it works. This
file builds the runs by hand and asserts what reaches the terminal, because two
of those renderings carry a claim rather than a summary.

**The match rate is never printed without its denominator.** Two matches out of
two and two out of forty are different facts and only one of them is good news,
so the rate alone is unreadable and the command does not offer it alone.

**An estimate nothing resolved is not coloured as a problem.** Most estimates in
a real corpus are never resolved, and a command that marked every one of them
would train a reader to ignore the marking. The four causes somebody could act
on -- an unusable answer, a citation that failed the gate, units that cannot be
reconciled, a record the store refused -- are the ones marked, and the two that
are ordinary answers are not.

The error path is asserted as a *sentence*. A traceback reaching a person who
typed a command is a bug report about Praxis rather than an answer about their
store.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from praxis.agents.classifier import UNCLASSIFIED, Classification, ClassificationRefusal
from praxis.agents.errors import Refusal
from praxis.agents.estimation import DocumentEstimates, EstimationRun
from praxis.agents.matcher import MatchedOutcome, Unmatched, UnmatchedEstimate
from praxis.agents.results import Refused, Stage
from praxis.cli import app
from praxis.cli_estimate import BENIGN, _report
from praxis.config.settings import Settings
from praxis.domain.enums import MatchQuality, RecordKind, SourceKind, Unit
from praxis.domain.ids import DocumentId, EstimateId, OutcomeId
from praxis.domain.records import Document, Estimate, Outcome
from praxis.ingest.verifier import ClaimMatch
from praxis.llm.errors import ProviderError
from typer.testing import CliRunner

runner = CliRunner()

AT = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


def a_document() -> Document:
    return Document(
        id=DocumentId("DOC-0001"),
        source_uri="file://status.md",
        source_kind=SourceKind.MARKDOWN,
        title="Discovery status",
        content="Nadeesha put the search index migration at 4 weeks.",
        ingested_at=AT,
        created_at=AT,
        created_by="test",
    )


def an_estimate(record_id: str = "EST-0001", work_class: str = "migration") -> Estimate:
    return Estimate(
        id=EstimateId(record_id),
        subject="the search index migration",
        owner="Nadeesha",
        work_class=work_class,
        active_quantity=Decimal(4),
        blocked_quantity=Decimal(0),
        unit=Unit.WEEKS,
        confidence=0.7,
        estimated_at=AT,
        span_id="SPAN-0123456789abcdef",
        created_by="EstimateExtractor",
        created_at=AT,
    )


def a_matched(record_id: str = "OUT-0001", estimate: Estimate | None = None) -> MatchedOutcome:
    resolved = estimate if estimate is not None else an_estimate()
    return MatchedOutcome(
        outcome=Outcome(
            id=OutcomeId(record_id),
            estimate_id=resolved.id,
            active_quantity=Decimal(7),
            blocked_quantity=Decimal(0),
            unit=Unit.WEEKS,
            match_quality=MatchQuality.PARTIAL,
            resolved_at=AT,
            notes="the same migration, in the past tense",
            span_id="SPAN-0123456789abcdef",
            created_by="OutcomeMatcher",
            created_at=AT,
        ),
        estimate=resolved,
        quote="It actually took 7 weeks of hands-on work.",
        quote_match=ClaimMatch.EXACT,
        ratio=Decimal("1.75"),
    )


def an_unmatched(
    record_id: str = "OUT-0002",
    estimate_id: str = "EST-0002",
    reason: Unmatched = Unmatched.MODEL_FOUND_NONE,
) -> UnmatchedEstimate:
    estimate = an_estimate(estimate_id)
    return UnmatchedEstimate(
        outcome=Outcome(
            id=OutcomeId(record_id),
            estimate_id=estimate.id,
            unit=Unit.WEEKS,
            match_quality=MatchQuality.UNRESOLVED,
            notes=f"{reason.value}: nothing here resolves it",
            created_by="OutcomeMatcher",
            created_at=AT,
        ),
        estimate=estimate,
        reason=reason,
        detail="nothing here resolves it",
    )


def a_classification() -> Classification:
    return Classification(
        estimate=an_estimate(),
        work_class="migration",
        proposed=False,
        reason="moving a search index",
        confidence=0.8,
        calls=1,
    )


def a_run(**kwargs) -> EstimationRun:
    """A Half B pass, with everything the renderer reads and nothing it does not."""
    kwargs.setdefault(
        "documents",
        (DocumentEstimates(document=a_document(), calls=2),),
    )
    kwargs.setdefault("classified", (a_classification(),))
    kwargs.setdefault("matched", (a_matched(),))
    kwargs.setdefault("unmatched", (an_unmatched(),))
    kwargs.setdefault("classifier_calls", 1)
    kwargs.setdefault("matcher_calls", 2)
    return EstimationRun(**kwargs)


def printed(run: EstimationRun, *, quiet: bool = False) -> str:
    """Render a run and return what reached the terminal.

    Through `redirect_stdout` rather than by assigning `console.file`. Rich
    resolves an unset `file` lazily against `sys.stdout`, so assigning it here
    would capture whatever stdout happened to be current and pin the module's
    console to it -- which then silently swallows the output of every later test
    that runs the command through `CliRunner`. Found the hard way.
    """
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _report(run, Settings(), quiet=quiet)
    return buffer.getvalue()


class TestTheSummary:
    """What every run reports, whatever it found."""

    def test_names_what_it_extracted_and_from_how_many_documents(self) -> None:
        assert "1 documents" in printed(a_run())

    def test_the_match_rate_never_appears_without_its_denominator(self) -> None:
        """Two out of two and two out of forty are different facts."""
        out = printed(a_run())

        assert "1 of 2" in out
        assert "match rate 0.5000" in out

    def test_says_what_it_did_not_pay_for(self) -> None:
        """Four different skips, and a second run reporting them is correct output."""
        out = printed(a_run(already_classified=3, already_attempted=1, already_matched=4))

        assert "3 already classified" in out
        assert "1 attempted before" in out
        assert "4 already answered" in out

    def test_names_the_provider_the_calls_went_through(self) -> None:
        assert "mock" in printed(a_run())

    def test_a_second_run_reports_zeroes_rather_than_nothing(self) -> None:
        """All zeros is the correct output for an unchanged store, not a fault."""
        out = printed(
            EstimationRun(
                documents=(DocumentEstimates(document=a_document(), already_read=True),),
                already_classified=2,
                already_matched=2,
            )
        )

        assert "praxis estimates: OK" in out
        assert "0 calls" in out


class TestWhatIsMarked:
    """The colour has to mean something, so it is not spent on ordinary answers."""

    def test_the_two_ordinary_causes_are_not_marked(self) -> None:
        assert {Unmatched.MODEL_FOUND_NONE, Unmatched.NO_CANDIDATES} == BENIGN

    @pytest.mark.parametrize("reason", sorted(BENIGN, key=lambda value: value.value))
    def test_an_estimate_nobody_resolved_is_reported_plainly(self, reason: Unmatched) -> None:
        out = printed(a_run(unmatched=(an_unmatched(reason=reason),)))

        assert f"unresolved: {reason.value}" in out
        assert "\x1b[33m" not in out

    @pytest.mark.parametrize(
        "reason",
        [
            Unmatched.UNCITED,
            Unmatched.INCOMPARABLE_UNITS,
            Unmatched.NO_USABLE_ANSWER,
            Unmatched.INCOHERENT_RECORD,
        ],
    )
    def test_a_cause_somebody_could_act_on_is_marked(self, reason: Unmatched) -> None:
        out = printed(a_run(unmatched=(an_unmatched(reason=reason),)))

        assert f"unresolved: {reason.value}" in out

    def test_a_lost_citation_is_counted_apart_from_an_unmatched_estimate(self) -> None:
        """Different failures: one lost an estimate, the other lost a pairing."""
        run = a_run(
            documents=(
                DocumentEstimates(
                    document=a_document(),
                    refused=(
                        Refused(
                            stage=Stage.ESTIMATE,
                            refusal=Refusal.FABRICATED_QUOTE,
                            doc_id=DocumentId("DOC-0001"),
                            detail="the quotation is in none of the passages",
                            lost=RecordKind.ESTIMATE,
                        ),
                    ),
                ),
            )
        )

        out = printed(run)

        assert "refused    1 estimates lost to a citation" in out
        assert "unresolved" in out

    def test_a_blind_window_is_reported_when_there_was_one(self) -> None:
        run = a_run(documents=(DocumentEstimates(document=a_document(), blind_windows=2),))

        assert "2 windows never answered about" in printed(run)

    def test_nothing_blind_prints_no_blind_line(self) -> None:
        assert "never answered about" not in printed(a_run())


class TestNamingExamples:
    """A number with nothing behind it is a number nobody can check."""

    def test_a_matched_outcome_is_named_with_the_band_arithmetic_computed(self) -> None:
        """The record's own field, not a judgement the renderer invented."""
        out = printed(a_run())

        assert "OUT-0001 resolves EST-0001" in out
        assert "as partial" in out

    def test_quiet_prints_the_totals_and_names_nothing(self) -> None:
        out = printed(a_run(), quiet=True)

        assert "praxis estimates: OK" in out
        assert "OUT-0001" not in out

    def test_a_classification_refusal_is_counted_apart_from_an_unclassified_answer(
        self,
    ) -> None:
        """One is a row to look at; the other is a call to make again."""
        run = a_run(
            classification_refusals=(
                ClassificationRefusal(
                    estimate_id="EST-0003",
                    refusal=Refusal.NO_USABLE_ANSWER,
                    detail="the model refused",
                ),
            )
        )

        assert "1 said nothing usable" in printed(run)

    def test_an_estimate_left_unclassified_is_still_a_classification(self) -> None:
        """`unclassified` is an answer. It is counted, and it is not a refusal."""
        unclassified = Classification(
            estimate=an_estimate(work_class=UNCLASSIFIED),
            work_class=UNCLASSIFIED,
            proposed=False,
            reason="the passage does not make the kind of work clear",
            confidence=0.4,
        )

        out = printed(a_run(classified=(unclassified,)))

        assert "classified 1 onto a work class" in out
        assert "0 said nothing usable" in out


class TestFailuresReadAsSentences:
    def test_a_provider_failure_is_a_sentence_and_never_a_traceback(
        self, tmp_path, monkeypatch
    ) -> None:
        runner.invoke(app, ["init"])
        monkeypatch.setattr(
            "praxis.cli_estimate.estimate_store", _raising(ProviderError("no route"))
        )

        result = runner.invoke(app, ["estimates"])

        assert result.exit_code == 1
        assert "praxis estimates: no route" in result.output
        assert "Traceback" not in result.output


def _raising(error: BaseException):
    """A stand-in that raises rather than running."""

    def _fail(*args: object, **kwargs: object) -> None:
        raise error

    return _fail
