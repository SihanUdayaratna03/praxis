"""What `praxis calibrate` prints, and what it refuses to let a reader conclude.

Three renderings here carry a claim rather than a summary, and each has a class.

**A refusal is printed as an answer, not as an absence.** Against this project's
own history every group refuses, so a command that printed only measured groups
would show an empty table and look broken. The refusals are named, and named
with the sample they are waiting on.

**A backtest score is never printed without its denominator.** "0.0" means "no
correction helped" and "nothing was scored" and only one of them is a grade, so
the ungraded case gets a different sentence rather than a zero.

**Asking about one estimate writes nothing.** A question that quietly mutates a
store is one nobody can ask twice, and that is asserted against the store rather
than against the output.

The error path is asserted as a *sentence*: a traceback reaching a person who
typed a command is a bug report about Praxis rather than an answer about their
store.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from decimal import Decimal

import pytest
from praxis.agents.bias import BiasVerdict, CalibrationGroup, summarise
from praxis.agents.calibration import CalibrationRun
from praxis.agents.scoring import Backtest, walk
from praxis.cli import app
from praxis.cli_calibrate import _report
from praxis.config.settings import Settings, get_settings
from praxis.domain.enums import FindingKind
from praxis.domain.records import Finding
from praxis.store.errors import StoreError
from praxis.store.repository import open_repository
from typer.testing import CliRunner

from tests.agents.test_bias import GROUP, OWNER, WORK_CLASS, a_row, write_history
from tests.store.conftest import build_world

runner = CliRunner()


def a_run(**kwargs: object) -> CalibrationRun:
    """A pass result with one measured group and one that is short."""
    measured = summarise(GROUP, [a_row("6", "10.8", index=i) for i in range(6)])
    short = summarise(
        CalibrationGroup(OWNER, "refactor"),
        [a_row("6", "9", index=i, work_class="refactor") for i in range(2)],
    )
    defaults: dict[str, object] = {
        "factors": (measured, short),
        "backtests": (walk(GROUP, [a_row("6", "10.8", index=i) for i in range(9)]),),
        "raised": (),
        "revised": (),
        "unchanged": 0,
    }
    defaults.update(kwargs)
    return CalibrationRun(**defaults)  # type: ignore[arg-type]


def printed(run: CalibrationRun, *, quiet: bool = False) -> str:
    """Render a pass and return what reached the terminal, with runs of
    whitespace collapsed.

    Through `redirect_stdout` rather than by assigning `console.file`, which is
    the mistake `tests/test_cli_estimate.py` records finding the hard way: rich
    resolves an unset `file` lazily, so assigning it pins the module console to
    a stale stdout and silently eats every later test's output.

    Collapsed because rich wraps at the terminal width, so a sentence this file
    asserts on can be split by a newline that depends on how wide the window
    was. Every assertion below is about *what was said*, and pinning where the
    wrap fell would make these tests fail on a narrower terminal without
    anything being wrong.
    """
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        _report(run, quiet=quiet)
    return " ".join(buffer.getvalue().split())


class TestTheSummary:
    """What every pass reports, whatever it found."""

    def test_it_says_how_many_groups_it_read_and_how_many_could_speak(self) -> None:
        out = printed(a_run())

        assert "2 read" in out
        assert "1 with enough history" in out

    def test_it_reports_what_it_wrote_and_what_it_did_not(self) -> None:
        """A second run writes nothing, and that is correct output rather than broken."""
        out = printed(a_run(unchanged=3))

        assert "0 raised" in out
        assert "3 already standing" in out

    def test_it_states_the_zero_model_calls_rather_than_omitting_the_row(self) -> None:
        """A missing cost row reads as unmeasured, not as free."""
        assert "0 calls" in printed(a_run())

    def test_quiet_prints_the_totals_and_names_nobody(self) -> None:
        out = printed(a_run(), quiet=True)

        assert "groups" in out
        assert OWNER not in out


class TestARefusalIsAnAnswer:
    """The rendering that stops an empty table reading as breakage."""

    def test_a_measured_group_is_named_with_its_factor(self) -> None:
        out = printed(a_run())

        assert "1.8000x under" in out
        assert "n=6" in out

    def test_a_group_short_of_the_threshold_is_named_with_its_sample(self) -> None:
        """ "One short" is what makes somebody close an outcome."""
        out = printed(a_run())

        assert "refactor" in out
        assert "n=2" in out
        assert "2 estimates" in out

    def test_unclassified_estimates_get_their_own_line_and_their_own_fix(self) -> None:
        """A different job from "this group needs one more outcome"."""
        unclassified = summarise(
            CalibrationGroup(OWNER, "unclassified"),
            [a_row("6", "9", index=i, work_class="unclassified") for i in range(7)],
        )

        out = printed(a_run(factors=(unclassified,)))

        assert "carry no work class" in out
        assert "in no group" in out

    def test_the_closest_group_to_speaking_is_named_first(self) -> None:
        """Sorted by sample size, so the actionable one is the one a reader sees."""
        far = summarise(
            CalibrationGroup(OWNER, "far"),
            [a_row("6", "9", index=i, work_class="far") for i in range(1)],
        )
        near = summarise(
            CalibrationGroup(OWNER, "near"),
            [a_row("6", "9", index=i, work_class="near") for i in range(4)],
        )

        out = printed(a_run(factors=(far, near)))

        assert out.index("near") < out.index("far")


class TestAScoreNeedsItsDenominator:
    """0.0 means two things and only one of them is a grade."""

    def test_an_ungraded_backtest_says_nothing_was_scored(self) -> None:
        short = walk(GROUP, [a_row("6", "10.8", index=i) for i in range(3)])

        out = printed(a_run(backtests=(short,)))

        assert "nothing to score" in out
        assert "none in a group that reached the threshold" in out

    def test_a_graded_backtest_prints_the_rate_with_its_count(self) -> None:
        out = printed(a_run())

        assert "of 4 corrections landed closer" in out
        assert "mean log error" in out

    def test_an_empty_pass_still_renders(self) -> None:
        """A store with nothing in it is a sentence, not a division by zero."""
        out = printed(CalibrationRun())

        assert "0 read" in out
        assert "nothing to score" in out


class TestOverARealStore:
    """The command end to end, against a store on disk."""

    @staticmethod
    def seeded(tmp_path, monkeypatch) -> None:
        """An initialised store holding one clearly biased group."""
        monkeypatch.setenv("PRAXIS_DATA_DIR", str(tmp_path / "praxis"))
        get_settings.cache_clear()
        runner.invoke(app, ["init"])
        settings = Settings(data_dir=tmp_path / "praxis")
        repository = open_repository(settings, create=False)
        try:
            world = build_world(repository)
            write_history(repository, world, [("6", "10.8")] * 6)
        finally:
            repository.close()

    def test_it_runs_and_writes_a_finding(self, tmp_path, monkeypatch) -> None:
        self.seeded(tmp_path, monkeypatch)

        result = runner.invoke(app, ["calibrate"])

        assert result.exit_code == 0
        assert "praxis calibrate: OK" in result.output
        assert "1 raised" in result.output

    def test_a_second_run_writes_nothing(self, tmp_path, monkeypatch) -> None:
        self.seeded(tmp_path, monkeypatch)
        runner.invoke(app, ["calibrate"])

        result = runner.invoke(app, ["calibrate"])

        assert "0 raised" in result.output
        assert "1 already standing" in result.output

    def test_asking_about_one_estimate_answers_and_writes_nothing(
        self, tmp_path, monkeypatch
    ) -> None:
        """A question that mutates a store is one nobody can ask twice."""
        self.seeded(tmp_path, monkeypatch)
        settings = Settings(data_dir=tmp_path / "praxis")

        result = runner.invoke(
            app,
            [
                "calibrate",
                "--owner",
                OWNER,
                "--work-class",
                WORK_CLASS,
                "--quantity",
                "6",
                "--unit",
                "weeks",
            ],
        )

        assert result.exit_code == 0
        assert "calibrated 10.8000 weeks" in result.output
        assert not _calibration_findings(settings)

    def test_a_pass_through_reads_as_an_answer_rather_than_an_error(
        self, tmp_path, monkeypatch
    ) -> None:
        """The path six of this project's own seven estimates would take."""
        self.seeded(tmp_path, monkeypatch)

        result = runner.invoke(
            app,
            ["calibrate", "--owner", "nobody", "--work-class", "migration", "--quantity", "4"],
        )

        assert result.exit_code == 0
        assert "calibrated 4 hours" in result.output
        assert "correct answer" in result.output


class TestFailuresReadAsSentences:
    """Nobody who typed a command should see a traceback."""

    def test_the_three_question_options_must_be_given_together(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("PRAXIS_DATA_DIR", str(tmp_path / "praxis"))
        get_settings.cache_clear()
        runner.invoke(app, ["init"])

        result = runner.invoke(app, ["calibrate", "--owner", OWNER])

        assert result.exit_code == 1
        assert "go together" in result.output
        assert "Traceback" not in result.output

    def test_a_quantity_that_is_not_a_number_is_refused_in_words(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv("PRAXIS_DATA_DIR", str(tmp_path / "praxis"))
        get_settings.cache_clear()
        runner.invoke(app, ["init"])

        result = runner.invoke(
            app,
            ["calibrate", "--owner", OWNER, "--work-class", "migration", "--quantity", "soon"],
        )

        assert result.exit_code == 1
        assert "is not a quantity" in result.output
        assert "Traceback" not in result.output

    def test_a_negative_quantity_is_refused_in_words(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("PRAXIS_DATA_DIR", str(tmp_path / "praxis"))
        get_settings.cache_clear()
        runner.invoke(app, ["init"])

        result = runner.invoke(
            app,
            ["calibrate", "--owner", OWNER, "--work-class", "migration", "--quantity", "-3"],
        )

        assert result.exit_code == 1
        assert "cannot be negative" in result.output
        assert "Traceback" not in result.output

    def test_a_store_failure_is_a_sentence_and_never_a_traceback(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setenv("PRAXIS_DATA_DIR", str(tmp_path / "praxis"))
        get_settings.cache_clear()
        runner.invoke(app, ["init"])
        monkeypatch.setattr(
            "praxis.cli_calibrate.calibrate_store", _raising(StoreError("store is locked"))
        )

        result = runner.invoke(app, ["calibrate"])

        assert result.exit_code == 1
        assert "praxis calibrate: store is locked" in result.output
        assert "Traceback" not in result.output


def _calibration_findings(settings: Settings) -> list[Finding]:
    """Every calibration finding a store on disk currently holds."""
    repository = open_repository(settings, create=False)
    try:
        return [
            finding
            for finding in repository.list_all(Finding)
            if finding.kind is FindingKind.CALIBRATION_BIAS
        ]
    finally:
        repository.close()


def _raising(error: BaseException):
    """A stand-in that raises rather than running."""

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise error

    return _fail


def test_the_backtest_helper_reaches_the_graded_case() -> None:
    """The control on `TestAScoreNeedsItsDenominator`: the fixture really grades."""
    graded = walk(GROUP, [a_row("6", "10.8", index=i) for i in range(9)])

    assert graded.graded
    assert graded.scored == 4
    assert isinstance(graded, Backtest)


def test_a_measured_verdict_is_what_the_fixture_holds() -> None:
    """Guards the fixture rather than the command: a run of refusals proves less."""
    assert a_run().factors[0].verdict is BiasVerdict.MEASURED
    assert a_run().factors[0].factor == Decimal("1.8000")


@pytest.mark.parametrize("flag", ["--help"])
def test_the_command_is_registered(flag: str) -> None:
    result = runner.invoke(app, ["calibrate", flag])

    assert result.exit_code == 0
