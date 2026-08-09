"""Commit subjects are a Phase 12 corpus, so they have to parse."""

from __future__ import annotations

from types import ModuleType

import pytest


@pytest.mark.parametrize(
    "subject",
    [
        "feat: add the model registry",
        "feat(config): add the model registry",
        "fix(hooks): decode git output as utf-8",
        "docs: record ADRs 0001-0007",
        "chore!: drop python 3.11 support",
    ],
)
def test_conventional_subjects_are_accepted(guard_commit_message: ModuleType, subject: str) -> None:
    assert guard_commit_message.check(subject) == []


@pytest.mark.parametrize(
    ("subject", "why"),
    [
        ("added the model registry", "no type"),
        ("feat add the model registry", "no colon"),
        ("feat: Add the model registry", "capitalised summary"),
        ("feat: add the model registry.", "trailing period"),
        ("wibble: add the model registry", "unknown type"),
    ],
)
def test_non_conventional_subjects_are_rejected(
    guard_commit_message: ModuleType, subject: str, why: str
) -> None:
    assert guard_commit_message.check(subject), why


def test_overlong_subjects_are_rejected(guard_commit_message: ModuleType) -> None:
    subject = "feat: " + "x" * 80

    problems = guard_commit_message.check(subject)

    assert any("under 72" in problem for problem in problems)


def test_a_body_must_be_separated_by_a_blank_line(guard_commit_message: ModuleType) -> None:
    message = "feat: add a thing\nthe body starts immediately"

    problems = guard_commit_message.check(message)

    assert any("blank line" in problem for problem in problems)


def test_a_well_formed_body_is_accepted(guard_commit_message: ModuleType) -> None:
    message = "feat: add a thing\n\nBecause the reason for it is not in the diff."

    assert guard_commit_message.check(message) == []


@pytest.mark.parametrize(
    "subject",
    [
        "Merge branch 'feat/phase-0-foundation'",
        'Revert "feat: add a thing"',
        "fixup! feat: add a thing",
    ],
)
def test_git_generated_subjects_are_left_alone(
    guard_commit_message: ModuleType, subject: str
) -> None:
    """These are git's to format, not ours to reject."""
    assert guard_commit_message.check(subject) == []


def test_comment_lines_are_ignored(guard_commit_message: ModuleType) -> None:
    message = "feat: add a thing\n\nbody\n# Please enter the commit message..."

    assert guard_commit_message.check(message) == []


def test_an_empty_message_is_left_to_git(guard_commit_message: ModuleType) -> None:
    assert guard_commit_message.check("") == []
