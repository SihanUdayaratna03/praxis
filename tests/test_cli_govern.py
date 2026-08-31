"""What `praxis govern` prints, and the two things it must never let a reader conclude.

This is where a refusal becomes readable, so the renderings that carry a claim
each get a class.

**An abstention is printed as an answer, not as an absence.** A `NEEDS_HUMAN`
is the product working, and the section leads. A command that printed only its
conclusions would show an empty screen against any honest store and look broken.

**A concession is never printed as an abstention.** They look alike and are
opposite: one is a finding an argument defeated, the other is a finding nobody
was willing to decide. A reader who confuses them takes a working challenger for
an uncertain one.

**A concede rate against the mock is not evidence about the challenger**, and
the command has to say so on the line where the number appears. This is the most
misleading line the CLI could emit, so it is the one with the strongest test.

**The empty store is the case a judge is most likely to hit**, because the mock
provider's citations are refused and few assumptions survive to be breached. It
gets a sentence naming the real cause, and it is distinguished from the very
different case of a store where nothing *changed*.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from praxis.agents.challenger import Challenge, ChallengeResult
from praxis.agents.curator import IDLE_DAYS
from praxis.cli import app
from praxis.cli_govern import MAX_LISTED
from praxis.config.settings import Settings, get_settings
from praxis.domain.enums import (
    DecisionScope,
    FindingKind,
    Impact,
    RecordKind,
    Severity,
    Verdict,
)
from praxis.domain.links import LinkType
from praxis.domain.records import (
    Assumption,
    Decision,
    Finding,
    Link,
    RejectedOption,
    Span,
)
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.store.errors import StoreError
from praxis.store.repository import Repository, open_repository
from typer.testing import CliRunner

runner = CliRunner(env={"COLUMNS": "200"})
"""A wide terminal, so an assertion is about the sentence rather than about
where `rich` happened to wrap it. Eighty columns splits every explanation in
the abstention table across two rows."""

NOW = datetime.now(UTC)
OLD = NOW - timedelta(days=IDLE_DAYS + 60)
MID = NOW - timedelta(days=IDLE_DAYS - 40)
ACTOR = "test"


@pytest.fixture
def settings(tmp_path) -> Settings:
    """A store of this test's own, so nothing reads the developer's real one."""
    get_settings.cache_clear()
    return Settings(data_dir=tmp_path)


def _span(store: Repository, doc_id: str, body: str, uri: str, at: datetime) -> Span:
    source = MARKDOWN_ADAPTER.normalise(body.encode("utf-8"), source_uri=uri)
    document = document_from(source, doc_id=doc_id, ingested_at=at)
    store.add(document, actor=ACTOR, reason="fixture", at=at)
    return store.add(
        Span.covering(
            document, 0, len(document.content.encode("utf-8")), created_by=ACTOR, created_at=at
        ),
        actor=ACTOR,
        reason="fixture",
        at=at,
    )


def _seed(store: Repository) -> None:
    """A store holding every case the command has a rendering for.

    Four assumptions -- one revised by a later document, one idle and free, one
    idle with a live decision on it -- and four findings covering an ordinary
    breach, an unconfident one, an uncited contradiction and a computed
    projection. Enough that every section of the output has something in it.
    """
    home = _span(store, "DOC-0001", "# ADR\n\nThe index stays under 50 GB.\n", "adr.md", OLD)
    revision = _span(store, "DOC-0002", "# Revision\n\nIt will pass 50 GB.\n", "revision.md", MID)
    for number, predicate, statement, span, at in (
        (1, "index_size_gb <= 50", "the index stays under 50 GB", home, OLD),
        (2, "index_size_gb > 50", "the index will pass 50 GB", revision, MID),
        (3, "query_latency_ms <= 200", "latency stays under 200 ms", home, OLD),
        (4, "team_size >= 4", "the team stays at four people", home, OLD),
    ):
        store.add(
            Assumption(
                id=f"A-{number:04d}",
                statement=statement,
                predicate=predicate,
                expiry_condition='on_event("the index is re-sharded")',
                span_id=span.id,
                confidence=0.8,
                created_by="AssumptionFormalizer",
                created_at=at,
            ),
            actor=ACTOR,
            reason="fixture",
            at=at,
        )
    decision = store.add(
        Decision(
            id="D-0001",
            title="the product search index",
            chosen="OpenSearch on managed nodes",
            rejected=(RejectedOption(option="Postgres", reason="ranking quality"),),
            decision_maker="Nadeesha",
            decided_at=OLD,
            scope=DecisionScope.TEAM,
            impact=Impact.HIGH,
            span_id=home.id,
            confidence=0.9,
            created_by=ACTOR,
            created_at=OLD,
        ),
        actor=ACTOR,
        reason="fixture",
        at=OLD,
    )
    store.add(
        Link.between(
            LinkType.ASSUMES,
            decision.id,
            "A-0004",
            rationale="the decision rests on it",
            confidence=1.0,
            created_by=ACTOR,
            created_at=OLD,
        ),
        actor=ACTOR,
        reason="fixture",
        at=OLD,
    )
    store.add(
        Link.between(
            LinkType.CONTRADICTS,
            "A-0001",
            "A-0002",
            rationale="the two predicates permit no common value",
            confidence=1.0,
            created_by="ContradictionDetector",
            created_at=MID,
        ),
        actor=ACTOR,
        reason="fixture",
        at=MID,
    )
    for number, kind, subject, confidence, spans in (
        (1, FindingKind.ASSUMPTION_BREACH, "A-0001", 0.9, (home.id,)),
        (2, FindingKind.ASSUMPTION_BREACH, "A-0003", 0.3, (home.id,)),
        (3, FindingKind.CONTRADICTION, "A-0002", 0.8, ()),
        (4, FindingKind.STALE_DECISION, "A-0004", 0.7, ()),
    ):
        store.add(
            Finding(
                id=f"F-{number:04d}",
                kind=kind,
                subject_kind=RecordKind.ASSUMPTION,
                subject_id=subject,
                prosecution=f"{subject}: the case against it, stated so it can be argued with.",
                severity=Severity.MEDIUM,
                confidence=confidence,
                evidence_span_ids=spans,
                detected_at=MID,
                created_by="AssumptionMonitor",
                created_at=MID,
            ),
            actor=ACTOR,
            reason="fixture",
            at=MID,
        )


@pytest.fixture
def seeded(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[Repository]:
    monkeypatch.setenv("PRAXIS_DATA_DIR", str(settings.data_dir))
    get_settings.cache_clear()
    repository = open_repository(get_settings())
    _seed(repository)
    repository.close()
    yield repository
    get_settings.cache_clear()


@pytest.fixture
def empty(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PRAXIS_DATA_DIR", str(settings.data_dir))
    get_settings.cache_clear()
    open_repository(get_settings()).close()
    yield
    get_settings.cache_clear()


def standing(kind: FindingKind | None = None) -> list[Finding]:
    """Every standing finding, read back from the store rather than the output."""
    repository = open_repository(get_settings())
    try:
        return [f for f in repository.list_all(Finding) if kind is None or f.kind is kind]
    finally:
        repository.close()


class TestTheAbstentionsComeFirst:
    """The refusals are the output, not an error path."""

    def test_the_headline_counts_them_and_says_they_are_the_point(self, seeded: Repository) -> None:
        result = runner.invoke(app, ["govern", "--dry-run"])

        assert result.exit_code == 0
        assert "were not concluded" in result.stdout
        assert "the gate working" in result.stdout

    def test_each_rule_is_explained_in_words_a_reader_can_act_on(self, seeded: Repository) -> None:
        """ "low_confidence / 1" tells nobody what to do. Naming the cause does."""
        result = runner.invoke(app, ["govern", "--dry-run"])

        assert "never_challenged" in result.stdout
        assert "under half confident" in result.stdout

    def test_a_finding_failing_two_rules_names_both(self, seeded: Repository) -> None:
        """A person told one defect fixes it and is sent straight back."""
        result = runner.invoke(app, ["govern", "--dry-run"])

        assert "cites no span" in result.stdout
        assert "no verdict was reached" in result.stdout

    def test_quiet_keeps_the_table_and_drops_the_names(self, seeded: Repository) -> None:
        result = runner.invoke(app, ["govern", "--dry-run", "--quiet"])

        assert "were not concluded" in result.stdout
        assert "Decide it yourself" not in result.stdout


class TestTheConcedeRateIsLabelled:
    """The most misleading line this CLI could print, and the sentence that saves it."""

    def test_the_mock_is_named_as_the_source_of_the_number(self, seeded: Repository) -> None:
        """`_TRUE_BIAS` is 0.7, so an offline concede rate measures schema synthesis.

        Printing the number without this sentence would let a reader take a
        property of `MockProvider` for a property of the challenger.
        """
        result = runner.invoke(app, ["govern"])

        assert "not evidence about the challenger" in result.stdout

    def test_no_verdict_reported_is_not_the_same_as_a_rate_of_zero(
        self, seeded: Repository
    ) -> None:
        """Zero out of zero is no measurement, and reads as one otherwise."""
        result = runner.invoke(app, ["govern", "--dry-run"])

        assert "there is no concede rate" in result.stdout
        assert "not the same as a concede rate of zero" in result.stdout


class TestCuration:
    """What the memory stops carrying, and the refusal that is really a finding."""

    def test_a_merge_names_both_sides_and_which_one_stands(self, seeded: Repository) -> None:
        result = runner.invoke(app, ["govern", "--dry-run"])

        assert "A-0002 supersedes A-0001" in result.stdout

    def test_the_retirement_rate_is_printed_with_its_denominator(self, seeded: Repository) -> None:
        result = runner.invoke(app, ["govern", "--dry-run"])

        assert "assumption(s):" in result.stdout
        assert "to retire" in result.stdout

    def test_an_unverifiable_belief_under_a_live_decision_gets_its_own_section(
        self, seeded: Repository
    ) -> None:
        """The one curation refusal that is a finding rather than housekeeping.

        Grouping it with "written too recently" would bury the only line here
        worth interrupting somebody about.
        """
        result = runner.invoke(app, ["govern", "--dry-run"])

        assert "nobody can check these" in result.stdout
        assert "D-0001 still rests on it" in result.stdout


class TestWhatReachesTheStore:
    """Asserted against the store rather than against the output."""

    def test_a_real_run_writes_verdicts_edges_and_retractions(self, seeded: Repository) -> None:
        result = runner.invoke(app, ["govern"])

        assert result.exit_code == 0
        repository = open_repository(get_settings())
        try:
            assert repository.links_from("A-0002", types=(LinkType.SUPERSEDES,))
            assert repository.require(Assumption, "A-0003").retracted
        finally:
            repository.close()

    def test_a_dry_run_writes_nothing(self, seeded: Repository) -> None:
        result = runner.invoke(app, ["govern", "--dry-run"])

        assert "nothing was written" in result.stdout
        repository = open_repository(get_settings())
        try:
            assert not repository.links_from("A-0002", types=(LinkType.SUPERSEDES,))
            assert not repository.require(Assumption, "A-0003").retracted
            assert all(f.verdict is Verdict.UNDECIDED for f in repository.list_all(Finding))
        finally:
            repository.close()

    def test_a_dry_run_does_not_claim_a_record_is_already_retracted(
        self, seeded: Repository
    ) -> None:
        """Found by running the command against a seeded store.

        Feeding the gate the retirements a dry run only *proposed* made it print
        "A-0003 has been retracted" about a record still standing. A dry run may
        say what would happen; it may not assert something untrue about the
        store as it is.
        """
        result = runner.invoke(app, ["govern", "--dry-run"])

        assert "has been retracted" not in result.stdout
        assert "to retire" in result.stdout

    def test_a_widened_idle_window_retires_nothing(self, seeded: Repository) -> None:
        """Exposed so a person can ask what a different window does."""
        result = runner.invoke(app, ["govern", "--dry-run", "--idle-days", "100000"])

        assert "0 to retire" in result.stdout


class TestTheEmptyStore:
    """The case a judge is most likely to hit, and the one it must not be confused with."""

    def test_it_names_the_citation_gate_rather_than_a_command_to_re_run(self, empty: None) -> None:
        """Extraction has usually *been* run; the gate refused the claims.

        Telling somebody to run `praxis extract` again would send them round a
        loop that cannot change the answer.
        """
        result = runner.invoke(app, ["govern"])

        assert result.exit_code == 0
        assert "nothing to govern" in result.stdout
        assert "citation gate" in result.stdout

    def test_nothing_to_do_is_not_printed_as_nothing_changed(self, empty: None) -> None:
        """Found by running the command against the real offline pipeline.

        "Nothing changed" over an empty store tells a person their governance
        pass is settled when it has never had anything to settle.
        """
        result = runner.invoke(app, ["govern"])

        assert "nothing here to govern" in result.stdout
        assert "already stood and said the same thing" not in result.stdout

    def test_a_settled_store_does_print_nothing_changed(self, seeded: Repository) -> None:
        """The control, and it takes more than one run to reach.

        The mock answers about a couple of findings per call, so governance
        converges over several passes rather than one -- which is itself worth
        pinning: a pass that never settled would keep writing versions forever
        and the store would grow with nothing changing.
        """
        for _ in range(8):
            result = runner.invoke(app, ["govern"])
            if "Nothing written" in result.stdout:
                break

        assert "already stood and said the same thing" in result.stdout
        assert "no model was called" in result.stdout
        assert "nothing here to govern" not in result.stdout


def test_a_missing_store_is_a_sentence_rather_than_a_traceback(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every command in this CLI reports a missing store the same way."""
    monkeypatch.setenv("PRAXIS_DATA_DIR", str(settings.data_dir / "nowhere"))
    get_settings.cache_clear()

    result = runner.invoke(app, ["govern"])

    assert result.exit_code == 1
    assert "praxis" in result.stdout
    get_settings.cache_clear()


class TestTheRenderingsAThinStoreNeverReaches:
    """Paths a small fixture cannot exercise, each reached on purpose."""

    def test_more_abstentions_than_it_names_says_how_many_it_did_not(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Trailing off silently would hide the size of the queue."""
        monkeypatch.setenv("PRAXIS_DATA_DIR", str(settings.data_dir))
        get_settings.cache_clear()
        repository = open_repository(get_settings())
        home = _span(repository, "DOC-0001", "# ADR\n\nUnder 50 GB.\n", "adr.md", MID)
        repository.add(
            Assumption(
                id="A-0001",
                statement="the index stays under 50 GB",
                predicate="index_size_gb <= 50",
                expiry_condition='on_event("the index is re-sharded")',
                span_id=home.id,
                confidence=0.8,
                created_by="AssumptionFormalizer",
                created_at=MID,
            ),
            actor=ACTOR,
            reason="fixture",
            at=MID,
        )
        for number in range(1, MAX_LISTED + 3):
            repository.add(
                Finding(
                    id=f"F-{number:04d}",
                    kind=FindingKind.ASSUMPTION_BREACH,
                    subject_kind=RecordKind.ASSUMPTION,
                    subject_id="A-0001",
                    prosecution=f"finding {number}: the case against it.",
                    severity=Severity.MEDIUM,
                    confidence=0.2,
                    evidence_span_ids=(home.id,),
                    detected_at=MID,
                    created_by="AssumptionMonitor",
                    created_at=MID,
                ),
                actor=ACTOR,
                reason="fixture",
                at=MID,
            )
        repository.close()

        result = runner.invoke(app, ["govern", "--dry-run"])

        assert "and 2 more" in result.stdout
        get_settings.cache_clear()

    def test_a_live_provider_prints_the_rate_without_the_mock_caveat(
        self, seeded: Repository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The caveat is about the mock, so it must not follow a real number around.

        The control for the caveat test above: without this, a command that
        printed the sentence unconditionally would pass that test and be lying
        about every live run.
        """
        monkeypatch.setattr(
            "praxis.cli_govern.ProviderName", type("P", (), {"MOCK": "not-the-mock"})
        )

        result = runner.invoke(app, ["govern"])

        assert "Concede rate" in result.stdout
        assert "not evidence about the challenger" not in result.stdout

    def test_a_store_error_during_the_pass_is_a_sentence(
        self, seeded: Repository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The store can fail after it opened, and a traceback is not a report."""

        def failing(*_args: object, **_kwargs: object) -> None:
            raise StoreError("the database is locked")

        monkeypatch.setattr("praxis.cli_govern.govern_store", failing)

        result = runner.invoke(app, ["govern"])

        assert result.exit_code == 1
        assert "praxis govern: the database is locked" in result.stdout

    def test_an_unchanged_undecided_finding_reports_as_unchanged(
        self, seeded: Repository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The branch a settled store skips past, and it is a real state.

        A finding the challenger argues to the same undecided place every time
        is never *skipped* -- an undecided finding is the one thing it re-argues
        -- so it lands here rather than on the settled-store sentence.
        """
        # Run once first, so the merge and the retirement are already made and
        # this pass has nothing left to write except a verdict it will not write.
        runner.invoke(app, ["govern", "--idle-days", "100000"])
        repository = open_repository(get_settings())
        try:
            finding = repository.require(Finding, "F-0001")
            repository.revise(
                finding.model_copy(
                    update={"challenge": "argued and left open", "verdict": Verdict.UNDECIDED}
                ),
                actor="test",
                reason="fixture",
            )
        finally:
            repository.close()

        def same(self: object, findings: list[Finding]) -> ChallengeResult:
            return ChallengeResult(
                challenges=(Challenge("F-0001", "argued and left open", Verdict.UNDECIDED, 0.1),),
                calls=1,
            )

        monkeypatch.setattr("praxis.agents.challenger.ChallengerAgent.challenge", same)

        result = runner.invoke(app, ["govern", "--idle-days", "100000"])

        assert "1 finding(s) already stood and said the same thing" in result.stdout
        get_settings.cache_clear()
