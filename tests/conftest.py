"""Shared fixtures.

The environment is scrubbed of PRAXIS_* variables for every test. Without
this, a developer with a populated .env would run a different test suite than
CI does, and the offline guarantee would be tested on some machines and not
others.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from praxis.config.settings import Settings


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for key in [k for k in os.environ if k.startswith("PRAXIS_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PRAXIS_DATA_DIR", str(tmp_path / "praxis"))
    monkeypatch.chdir(tmp_path)
    yield


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Default settings pointed at a throwaway directory."""
    return Settings(data_dir=tmp_path / "praxis")
