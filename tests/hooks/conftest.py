"""Load the hook scripts as modules.

`.claude/hooks/` is not a package -- the scripts are invoked by path, by both
Claude Code and pre-commit. They are still enforcement code, so they are still
tested; importing them by location is the price of keeping them runnable as
standalone scripts.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "hooks"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"praxis_hooks.{name}", HOOKS_DIR / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        pytest.fail(f"cannot load hook {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def guard_no_secrets() -> ModuleType:
    return _load("guard_no_secrets")


@pytest.fixture(scope="session")
def guard_git_workflow() -> ModuleType:
    return _load("guard_git_workflow")


@pytest.fixture(scope="session")
def guard_commit_message() -> ModuleType:
    return _load("guard_commit_message")
