"""Typed configuration, loaded from the environment and an optional .env file.

Every setting has a working default, so a fresh clone runs the whole pipeline
with no configuration at all. Adding a real API key later is an edit to .env
and nothing else -- that is the one property this module exists to guarantee.
"""

from __future__ import annotations

import functools
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderName(StrEnum):
    """Which `LLMProvider` implementation backs every model call."""

    MOCK = "mock"
    """Deterministic and offline. The default, and what CI runs."""

    ANTHROPIC = "anthropic"
    """Live models. Requires a key."""

    REPLAY = "replay"
    """Replays recorded responses from fixture files."""


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class Settings(BaseSettings):
    """Runtime configuration for a Praxis process."""

    model_config = SettingsConfigDict(
        env_prefix="PRAXIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    llm_provider: ProviderName = ProviderName.MOCK

    anthropic_api_key: SecretStr | None = Field(
        default=None,
        description="Only read when llm_provider is 'anthropic'. Never logged.",
    )

    data_dir: Path = Field(
        default=Path(".praxis"),
        description="Root for everything generated at runtime. Gitignored.",
    )

    replay_dir: Path = Field(
        default=Path("tests/fixtures/replay"),
        description="Recorded provider responses, used when llm_provider is 'replay'.",
    )

    seed: int = Field(
        default=20260809,
        ge=0,
        description=(
            "Seeds every source of randomness. Two runs of the eval harness on "
            "the same corpus and the same seed must produce identical numbers, "
            "or the ablation table means nothing."
        ),
    )

    cost_ceiling_usd: float = Field(
        default=5.0,
        gt=0,
        description=(
            "A run aborts rather than exceed this. The owner of this project "
            "is paying for it personally; a runaway agent loop should cost an "
            "error message, not a bill."
        ),
    )

    log_level: str = Field(default="INFO")
    log_format: LogFormat = LogFormat.JSON

    @property
    def db_path(self) -> Path:
        """The embedded SQLite store. One file, no server."""
        return self.data_dir / "praxis.db"

    @property
    def trace_dir(self) -> Path:
        """Where the full input/output of every LLM call is recorded."""
        return self.data_dir / "traces"

    @property
    def is_offline(self) -> bool:
        """True when no network access is possible for model calls."""
        return self.llm_provider is not ProviderName.ANTHROPIC

    @model_validator(mode="after")
    def _key_required_for_live_calls(self) -> Self:
        if self.llm_provider is ProviderName.ANTHROPIC and self.anthropic_api_key is None:
            message = (
                "PRAXIS_LLM_PROVIDER=anthropic requires PRAXIS_ANTHROPIC_API_KEY. "
                "Set it in .env, or use the default 'mock' provider, which needs "
                "no credentials and exercises the entire pipeline."
            )
            raise ValueError(message)
        return self

    def ensure_directories(self) -> None:
        """Create the runtime directories. Safe to call repeatedly."""
        self.trace_dir.mkdir(parents=True, exist_ok=True)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read from the environment once.

    Cached so that configuration cannot change underneath a run. Tests that
    need different settings construct `Settings(...)` directly rather than
    mutating the environment and clearing this cache.
    """
    return Settings()
