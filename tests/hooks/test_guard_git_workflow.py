"""The git workflow guard blocks the two mistakes that cannot be undone."""

from __future__ import annotations

from types import ModuleType

import pytest


@pytest.mark.parametrize(
    "command",
    [
        "git merge --squash feat/x",
        "gh pr merge --squash --delete-branch",
        "git merge --no-ff --squash feat/x",
    ],
)
def test_squashing_is_always_blocked(guard_git_workflow: ModuleType, command: str) -> None:
    """The granular history is a deliverable, and a squash cannot be undone."""
    problems = guard_git_workflow.violations(command)

    assert any("Squashing" in problem for problem in problems)


@pytest.mark.parametrize(
    "command",
    [
        "git merge --no-ff feat/x",
        "gh pr merge --merge --delete-branch",
    ],
)
def test_merge_commits_are_allowed(guard_git_workflow: ModuleType, command: str) -> None:
    assert guard_git_workflow.violations(command) == []


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin main",
        "git push -f origin feat/x",
    ],
)
def test_force_pushes_are_flagged(guard_git_workflow: ModuleType, command: str) -> None:
    problems = guard_git_workflow.violations(command)

    assert any("Force push" in problem for problem in problems)


def test_force_with_lease_is_the_allowed_escape_hatch(guard_git_workflow: ModuleType) -> None:
    assert guard_git_workflow.violations("git push --force-with-lease origin feat/x") == []


def test_committing_on_main_is_blocked(
    guard_git_workflow: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guard_git_workflow, "current_branch", lambda: "main")

    problems = guard_git_workflow.violations("git commit -m 'feat: x'")

    assert any("Direct commit on main" in problem for problem in problems)


def test_committing_on_a_feature_branch_is_allowed(
    guard_git_workflow: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guard_git_workflow, "current_branch", lambda: "feat/phase-1-data-model")

    assert guard_git_workflow.violations("git commit -m 'feat: x'") == []


def test_unrelated_commands_pass_through(guard_git_workflow: ModuleType) -> None:
    assert guard_git_workflow.violations("uv run pytest") == []
    assert guard_git_workflow.violations("git status") == []


def test_detached_head_does_not_read_as_main(
    guard_git_workflow: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guard_git_workflow, "current_branch", lambda: None)

    assert guard_git_workflow.violations("git commit -m 'feat: x'") == []
