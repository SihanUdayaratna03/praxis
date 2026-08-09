"""Settings behaviour, with emphasis on the offline guarantee."""

from __future__ import annotations

from pathlib import Path

import pytest
from praxis.config.settings import LogFormat, ProviderName, Settings, get_settings
from pydantic import ValidationError

# Deliberately a value the credential guard recognises as a placeholder, so
# the test suite cannot be the thing that teaches the guard to cry wolf.
FAKE_KEY = "placeholder"


def test_defaults_are_offline_and_need_no_credentials() -> None:
    settings = Settings()

    assert settings.llm_provider is ProviderName.MOCK
    assert settings.anthropic_api_key is None
    assert settings.is_offline


@pytest.mark.parametrize("provider", [ProviderName.MOCK, ProviderName.REPLAY])
def test_non_live_providers_never_require_a_key(provider: ProviderName) -> None:
    settings = Settings(llm_provider=provider)

    assert settings.is_offline
    assert settings.anthropic_api_key is None


def test_anthropic_provider_without_a_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="PRAXIS_ANTHROPIC_API_KEY"):
        Settings(llm_provider=ProviderName.ANTHROPIC)


def test_anthropic_provider_with_a_key_is_accepted_and_not_offline() -> None:
    settings = Settings(llm_provider=ProviderName.ANTHROPIC, anthropic_api_key=FAKE_KEY)

    assert not settings.is_offline


def test_the_key_is_not_exposed_by_repr_or_str() -> None:
    settings = Settings(llm_provider=ProviderName.ANTHROPIC, anthropic_api_key=FAKE_KEY)

    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == FAKE_KEY


def test_switching_provider_is_a_single_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The README promises editing .env is the only change needed later."""
    monkeypatch.setenv("PRAXIS_LLM_PROVIDER", "replay")

    assert Settings().llm_provider is ProviderName.REPLAY


def test_derived_paths_live_under_the_data_dir(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "store")

    assert settings.db_path.parent == tmp_path / "store"
    assert settings.trace_dir.parent == tmp_path / "store"


def test_ensure_directories_is_idempotent(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "store")

    settings.ensure_directories()
    settings.ensure_directories()

    assert settings.trace_dir.is_dir()


def test_settings_are_frozen() -> None:
    settings = Settings()

    with pytest.raises(ValidationError):
        settings.seed = 1


def test_unknown_settings_are_rejected_rather_than_ignored() -> None:
    """A typo in .env should fail loudly, not silently do nothing."""
    with pytest.raises(ValidationError):
        Settings(llm_provdier="mock")


@pytest.mark.parametrize("bad_ceiling", [0.0, -1.0])
def test_cost_ceiling_must_be_positive(bad_ceiling: float) -> None:
    with pytest.raises(ValidationError):
        Settings(cost_ceiling_usd=bad_ceiling)


def test_log_format_defaults_to_json() -> None:
    assert Settings().log_format is LogFormat.JSON


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()

    assert get_settings() is get_settings()

    get_settings.cache_clear()
