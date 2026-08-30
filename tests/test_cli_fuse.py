"""What `praxis fuse` prints, and what it refuses to let a reader conclude.

This is the demo surface -- the one place the sentence `ARCHITECTURE.md`
promises is actually shown to a person -- so the renderings that carry a claim
each have a class.

**A projection is never printed as an observation.** A calibrated flip says
"nothing has been measured yet" in its own heading. ADR 0028 made the two
different `FindingKind`s so a queue cannot rank one above the other; this checks
that a reader cannot misread one as the other either.

**A refusal is printed as an answer, not as an absence.** Offline, and on any
small corpus, nearly every priced edge lands on `no_factor`. A command that
printed only the flips would show an empty screen and look broken.

**The empty store is the case a judge is most likely to hit**, because the mock
provider's citations are refused and few edges survive extraction. It gets a
sentence that names the real cause rather than one that sends somebody to re-run
a command they already ran.

**`--dry-run` writes nothing**, asserted against the store rather than against
the output.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from praxis.cli import app
from praxis.cli_fuse import MAX_LISTED
from praxis.config.settings import Settings, get_settings
from praxis.domain.enums import FindingKind
from praxis.domain.records import Finding
from praxis.store.errors import StoreError
from praxis.store.repository import Repository, open_repository
from typer.testing import CliRunner

from tests.agents.test_fusion import RUNS_LONG, a_disguised_estimate, write_history
from tests.store.conftest import build_world

runner = CliRunner()


@pytest.fixture
def settings(tmp_path) -> Settings:
    """A store of this test's own, so nothing reads the developer's real one."""
    get_settings.cache_clear()
    return Settings(data_dir=tmp_path)


@pytest.fixture
def seeded(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[Repository]:
    """A store holding the fusion graph, a history that runs long, and a miss.

    Built with the same helpers the agent tests use, so the CLI is shown the
    output of the tested code rather than a second hand-made fixture.
    """
    monkeypatch.setenv("PRAXIS_DATA_DIR", str(settings.data_dir))
    get_settings.cache_clear()
    repository = open_repository(get_settings())
    world = build_world(repository)
    write_history(repository, world, RUNS_LONG)
    a_disguised_estimate(repository, world, predicate="migration_weeks <= 6", quantity="4")
    repository.close()
    yield repository
    get_settings.cache_clear()


def findings(settings: Settings, kind: FindingKind) -> list[Finding]:
    """Every standing finding of one kind, read back from the store."""
    repository = open_repository(get_settings())
    try:
        return [f for f in repository.list_all(Finding) if f.kind is kind]
    finally:
        repository.close()


class TestTheDemoSentence:
    """The output `ARCHITECTURE.md` promises, printed."""

    def test_a_flip_is_reported_with_its_sample_and_confidence(self, seeded: Repository) -> None:
        """The strength of the evidence arrives with the allegation, not behind it."""
        result = runner.invoke(app, ["fuse"])

        assert result.exit_code == 0
        assert "may rest on a mis-estimate" in result.stdout
        assert "n=5" in result.stdout

    def test_a_projection_says_nothing_has_been_measured(self, seeded: Repository) -> None:
        """A reader who takes it for an observed breach will over-react to it."""
        result = runner.invoke(app, ["fuse"])

        assert "nothing has been measured yet" in result.stdout

    def test_a_measurement_says_it_already_happened(self, seeded: Repository) -> None:
        """The control for the test above: the two must not read the same.

        Without this, a command that printed "nothing has been measured yet"
        over every finding would pass the projection test and be wrong.
        """
        result = runner.invoke(app, ["fuse"])

        assert "this already happened" in result.stdout
        assert "missed and" in result.stdout


class TestTheRefusalsComeFirst:
    """Most priced edges say nothing, and that is the output rather than an error."""

    def test_the_verdict_table_names_every_verdict_it_counted(self, seeded: Repository) -> None:
        result = runner.invoke(app, ["fuse"])

        assert "estimated_as edge(s) priced" in result.stdout
        assert "no_factor" in result.stdout

    def test_a_refusal_is_explained_in_words_a_reader_can_act_on(self, seeded: Repository) -> None:
        """ "no_factor / 1" tells nobody what to do. "Close an outcome" does."""
        result = runner.invoke(app, ["fuse"])

        assert "fewer than five resolved estimates" in result.stdout

    def test_an_empty_store_names_the_real_cause(self, settings: Settings, monkeypatch) -> None:
        """The case a judge is most likely to hit, offline.

        Extraction has usually *been* run; the citation gate refused the claims.
        Telling somebody to run `praxis extract` again would send them round a
        loop that cannot change the answer.
        """
        monkeypatch.setenv("PRAXIS_DATA_DIR", str(settings.data_dir))
        get_settings.cache_clear()
        open_repository(get_settings()).close()

        result = runner.invoke(app, ["fuse"])

        assert result.exit_code == 0
        assert "No estimated_as edges" in result.stdout
        assert "citation gate" in result.stdout


class TestWriting:
    """What reaches the store, and what a second run does not."""

    def test_a_run_writes_both_kinds_of_finding(self, seeded: Repository) -> None:
        result = runner.invoke(app, ["fuse"])

        assert result.exit_code == 0
        assert findings(get_settings(), FindingKind.STALE_DECISION)
        assert findings(get_settings(), FindingKind.COLLATERAL_IMPACT)

    def test_a_second_run_writes_nothing_and_says_so(self, seeded: Repository) -> None:
        """Reported as the correct outcome, not as an empty result."""
        runner.invoke(app, ["fuse"])

        second = runner.invoke(app, ["fuse"])

        assert second.exit_code == 0
        assert "Nothing written" in second.stdout
        assert "already stood and said the same thing" in second.stdout

    def test_dry_run_writes_nothing_at_all(self, seeded: Repository) -> None:
        """Asserted against the store, not against the output.

        A question that quietly mutates a store is one nobody can ask twice.
        """
        result = runner.invoke(app, ["fuse", "--dry-run"])

        assert result.exit_code == 0
        assert "nothing was written" in result.stdout
        assert findings(get_settings(), FindingKind.STALE_DECISION) == []
        assert findings(get_settings(), FindingKind.COLLATERAL_IMPACT) == []

    def test_dry_run_still_reports_what_it_found(self, seeded: Repository) -> None:
        """Writing nothing is not the same as finding nothing.

        The dry run uses the same two agents the pass does, so what it prints is
        what a real run would write rather than a second, simpler answer.
        """
        result = runner.invoke(app, ["fuse", "--dry-run"])

        assert "may rest on a mis-estimate" in result.stdout
        assert "missed and" in result.stdout


class TestQuiet:
    """The totals without the roll-call."""

    def test_quiet_keeps_the_verdict_table_and_drops_the_examples(self, seeded: Repository) -> None:
        result = runner.invoke(app, ["fuse", "--quiet"])

        assert result.exit_code == 0
        assert "estimated_as edge(s) priced" in result.stdout
        assert "may rest on a mis-estimate" not in result.stdout


class TestLongLists:
    """More findings than the command will name."""

    def test_it_says_how_many_it_did_not_name(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Trailing off silently would understate the size of the queue."""
        monkeypatch.setenv("PRAXIS_DATA_DIR", str(settings.data_dir))
        get_settings.cache_clear()
        repository = open_repository(get_settings())
        world = build_world(repository)
        write_history(repository, world, RUNS_LONG)
        for _ in range(MAX_LISTED + 2):
            a_disguised_estimate(repository, world, predicate="migration_weeks <= 6", quantity="4")
        repository.close()

        result = runner.invoke(app, ["fuse"])

        assert result.exit_code == 0
        assert "and 2 more" in result.stdout


class TestTheErrorPath:
    """A traceback reaching a person who typed a command is a bug report."""

    def test_a_store_failure_is_reported_as_a_sentence(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRAXIS_DATA_DIR", str(settings.data_dir))
        get_settings.cache_clear()
        runner.invoke(app, ["init"])
        monkeypatch.setattr("praxis.cli_fuse.fuse_store", _raising(StoreError("store is locked")))

        result = runner.invoke(app, ["fuse"])

        assert result.exit_code == 1
        assert "praxis fuse: store is locked" in result.output
        assert "Traceback" not in result.output


def _raising(error: BaseException):
    """A stand-in that raises rather than running."""

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise error

    return _fail
