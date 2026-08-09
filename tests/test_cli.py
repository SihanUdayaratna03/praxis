"""The CLI surface, exercised the way a user or CI would invoke it."""

from __future__ import annotations

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
