"""The CLI surface, exercised the way a user or CI would invoke it."""

from __future__ import annotations

from pathlib import Path

import pytest
from praxis import __version__
from praxis.cli import app
from praxis.config.settings import get_settings
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

    assert "schema" in result.output
    assert "version 2" in result.output


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
