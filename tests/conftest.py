"""Shared fixtures, and the one hypothesis setting the whole suite needs.

The environment is scrubbed of PRAXIS_* variables for every test. Without
this, a developer with a populated .env would run a different test suite than
CI does, and the offline guarantee would be tested on some machines and not
others.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings
from praxis.config.settings import Settings

HYPOTHESIS_DEADLINE = timedelta(seconds=2)
"""How long one generated example may take before it is a failure.

Hypothesis defaults to 200ms per example, and CI runs `pytest --cov`. Under
coverage instrumentation a first example that builds a Pydantic record can pass
200ms on a cold interpreter and not on the re-run, which hypothesis reports as
a flaky deadline rather than as a falsifying example -- an intermittent red
build carrying no information about the property under test.

Two seconds rather than `None`, because a strategy that really has become slow
is worth hearing about and a disabled deadline would never say so. It is a
sanity bound, not a latency assertion: none of these properties is a claim
about speed.
"""

hypothesis_settings.register_profile(
    "praxis",
    deadline=HYPOTHESIS_DEADLINE,
    suppress_health_check=[HealthCheck.too_slow],
)
hypothesis_settings.load_profile("praxis")


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
