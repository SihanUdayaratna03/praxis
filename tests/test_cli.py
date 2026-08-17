"""The CLI surface, exercised the way a user or CI would invoke it."""

from __future__ import annotations

from pathlib import Path

import pytest
from praxis import __version__
from praxis.cli import app
from praxis.config.settings import get_settings
from praxis.corpus.groundtruth import verify_corpus
from praxis.store.migrations import latest_version
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _uncached_settings() -> None:
    """Each test resolves settings from its own patched environment."""
    get_settings.cache_clear()


def test_version_prints_the_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_bare_invocation_shows_help_rather_than_failing_silently() -> None:
    result = runner.invoke(app, [])

    assert "doctor" in result.output
    assert "config" in result.output


def test_doctor_passes_on_a_default_offline_install() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_doctor_fails_when_configured_to_need_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRAXIS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("PRAXIS_ANTHROPIC_API_KEY", "placeholder")
    get_settings.cache_clear()

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_doctor_answers_a_real_call_rather_than_reading_the_setting() -> None:
    # The offline claim is the phase's central one, and a routing table, a
    # schema walk and a trace write all have to work before it is true. None
    # of that is visible in a configuration value, so doctor makes the call.
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "one structured call answered" in result.output


def test_doctor_reports_what_the_offline_call_cost_in_tokens() -> None:
    # Zero tokens would mean the answer was empty, which is the failure this
    # probe exists to catch and the one a passing exit code would hide.
    result = runner.invoke(app, ["doctor"])

    assert " 0 tokens" not in result.output


def test_doctor_creates_the_runtime_directories() -> None:
    runner.invoke(app, ["doctor"])

    assert get_settings().trace_dir.is_dir()


def test_config_reports_the_provider_and_the_routing_table() -> None:
    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    assert "llm_provider" in result.output
    assert "mock" in result.output
    assert "Model routing" in result.output


def test_config_never_prints_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAXIS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("PRAXIS_ANTHROPIC_API_KEY", "placeholder")
    get_settings.cache_clear()

    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    assert "placeholder" not in result.output
    assert "set" in result.output


def test_a_broken_env_file_surfaces_as_an_error_not_a_wrong_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting the live provider with no key must not silently fall back."""
    monkeypatch.setenv("PRAXIS_LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()

    result = runner.invoke(app, ["config"])

    assert result.exit_code != 0


# --- the store commands ---------------------------------------------------


def test_init_creates_a_store_at_the_configured_path() -> None:
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert get_settings().db_path.exists()
    assert "created" in result.output


def test_init_reports_the_schema_it_brought_the_store_to() -> None:
    result = runner.invoke(app, ["init"])

    # Against the shipped migration count rather than a literal: the number
    # this asserted was 2 until a migration was added, and a test that has to
    # be edited by every schema change is a test people learn to edit without
    # reading.
    assert "schema" in result.output
    assert f"version {latest_version()}" in result.output


def test_init_can_be_run_twice() -> None:
    # Migrations are forward-only and skip what is applied, so this is the
    # command an owner runs after pulling rather than a one-time step.
    first = runner.invoke(app, ["init"])
    second = runner.invoke(app, ["init"])

    assert first.exit_code == 0
    assert second.exit_code == 0, second.output
    assert "existing" in second.output


def test_store_stats_reports_an_empty_store() -> None:
    runner.invoke(app, ["init"])

    result = runner.invoke(app, ["store", "stats"])

    assert result.exit_code == 0, result.output
    assert "Records" in result.output
    assert "assumes" in result.output
    assert "0 versions written" in result.output


def test_store_stats_lists_a_kind_nobody_has_written_yet() -> None:
    # Zeros are printed rather than omitted: "no estimates" and "estimates are
    # not a thing this store knows about" are different answers.
    runner.invoke(app, ["init"])

    result = runner.invoke(app, ["store", "stats"])

    assert "estimate" in result.output


def test_store_stats_reports_a_missing_store_instead_of_creating_one() -> None:
    result = runner.invoke(app, ["store", "stats"])

    assert result.exit_code == 1
    assert "praxis init" in result.output
    assert not get_settings().db_path.exists()


def test_bare_store_shows_its_subcommands() -> None:
    result = runner.invoke(app, ["store"])

    assert "stats" in result.output


# --- ADR 0010: the sync-root warning --------------------------------------


def test_doctor_warns_when_the_store_sits_under_a_sync_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRAXIS_DATA_DIR", str(tmp_path / "OneDrive" / "praxis"))
    get_settings.cache_clear()

    result = runner.invoke(app, ["doctor"])

    # Warns and still passes. A deliberate override is legitimate, and a tool
    # that refuses to run is a tool that gets worked around.
    assert result.exit_code == 0, result.output
    assert "warning" in result.output
    assert "OneDrive" in result.output


def test_doctor_says_nothing_about_a_safe_location() -> None:
    result = runner.invoke(app, ["doctor"])

    assert "warning" not in result.output


def test_init_warns_before_it_writes_to_a_synced_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRAXIS_DATA_DIR", str(tmp_path / "Dropbox" / "praxis"))
    get_settings.cache_clear()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert "warning" in result.output
    assert "PRAXIS_JOURNAL_MODE=delete" in result.output


# --- Phase 3: ingestion and the corpus ------------------------------------


def test_corpus_generate_writes_a_corpus_that_verifies(tmp_path: Path) -> None:
    root = tmp_path / "corpus"

    result = runner.invoke(app, ["corpus", "generate", str(root), "--documents", "4"])

    assert result.exit_code == 0, result.output
    assert "OK" in result.output
    assert verify_corpus(root) == ()
    assert "4 distractors" in result.output


def test_corpus_generate_takes_its_seed_from_the_configuration(tmp_path: Path) -> None:
    """So that a corpus regenerated on another machine is the same corpus,
    without anyone having to remember a number."""
    result = runner.invoke(app, ["corpus", "generate", str(tmp_path / "c"), "--documents", "2"])

    assert f"seed {get_settings().seed}" in result.output


def test_corpus_generate_reports_a_bad_request_as_a_sentence(tmp_path: Path) -> None:
    result = runner.invoke(app, ["corpus", "generate", str(tmp_path / "c"), "--documents", "0"])

    assert result.exit_code == 1
    assert "at least one document" in result.output


def test_ingest_reads_a_corpus_into_the_store(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    runner.invoke(app, ["corpus", "generate", str(corpus), "--documents", "3"])
    runner.invoke(app, ["init"])

    result = runner.invoke(app, ["ingest", str(corpus / "documents")])

    assert result.exit_code == 0, result.output
    assert "3 written" in result.output
    assert "0 refused" in result.output
    assert "calls via mock" in result.output


def test_ingest_recognises_a_corpus_it_has_already_read(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    runner.invoke(app, ["corpus", "generate", str(corpus), "--documents", "2"])
    runner.invoke(app, ["init"])
    runner.invoke(app, ["ingest", str(corpus / "documents")])

    result = runner.invoke(app, ["ingest", str(corpus / "documents")])

    assert result.exit_code == 0, result.output
    assert "0 written, 2 already present" in result.output


def test_ingest_exits_non_zero_when_a_source_fails(tmp_path: Path) -> None:
    runner.invoke(app, ["init"])
    broken = tmp_path / "broken.md"
    broken.write_bytes(b"not utf-8: \xff\xfe")

    result = runner.invoke(app, ["ingest", str(broken)])

    assert result.exit_code == 1
    assert "failed" in result.output


def test_ingest_without_a_store_says_to_run_init(tmp_path: Path) -> None:
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nA decision was made.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", str(source)])

    assert result.exit_code == 1
    assert "praxis init" in result.output


def test_bare_corpus_shows_its_subcommands() -> None:
    result = runner.invoke(app, ["corpus"])

    assert "generate" in result.output
