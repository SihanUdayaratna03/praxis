#!/usr/bin/env python3
"""Reject commit messages that are not Conventional Commits.

Run as a pre-commit ``commit-msg`` hook, which passes the path of the pending
message file as the only argument.

The subject line is the index of this repository's history. If it is not
machine-parseable, the phase reports and the eventual self-analysis in the
final phase have nothing to read.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)

SUBJECT = re.compile(
    r"^(?P<type>" + "|".join(TYPES) + r")"
    r"(?:\((?P<scope>[a-z0-9][a-z0-9._-]*)\))?"
    r"(?P<breaking>!)?"
    r": (?P<summary>[a-z].*[^.])$"
)

# git generates these itself; they are not ours to reformat.
GENERATED = re.compile(r"^(Merge|Revert|fixup!|squash!|Applying) ")

MAX_SUBJECT = 72


def check(message: str) -> list[str]:
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    subject = lines[0].strip() if lines else ""

    if not subject or GENERATED.match(subject):
        return []

    problems: list[str] = []

    if not SUBJECT.match(subject):
        problems.append(
            f"subject does not match <type>(<scope>): <summary>\n"
            f"      got:   {subject}\n"
            f"      types: {', '.join(TYPES)}\n"
            f"      summary must start lower-case and must not end with a period"
        )

    if len(subject) > MAX_SUBJECT:
        problems.append(f"subject is {len(subject)} chars; keep it under {MAX_SUBJECT}")

    if len(lines) > 1 and lines[1].strip():
        problems.append("leave a blank line between the subject and the body")

    return problems


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: guard_commit_message.py <path-to-commit-msg>", file=sys.stderr)
        return 1

    problems = check(Path(argv[0]).read_text(encoding="utf-8"))
    if not problems:
        return 0

    print("Commit message rejected:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
