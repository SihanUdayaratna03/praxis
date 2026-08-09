#!/usr/bin/env python3
"""Enforce the branching rules from CLAUDE.md at the point of action.

Two rules are worth more than the prose that describes them:

1. Nothing is committed directly on ``main``. Every phase arrives through a
   reviewed, CI-green pull request.
2. Nothing is ever squashed. The granular commit history *is* the artifact of
   this project; a squash merge destroys it irreversibly.

Reads a Claude Code ``PreToolUse`` payload for ``Bash`` on stdin and exits 2 to
block, with the reason on stderr.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

PROTECTED_BRANCH = "main"

COMMIT = re.compile(r"\bgit\s+(?:-\S+\s+|--\S+(?:=\S+)?\s+)*commit\b")
SQUASH = re.compile(r"--squash\b")
FORCE_PUSH = re.compile(r"\bgit\s+push\b.*(?:--force(?!-with-lease)|(?<!\w)-f(?!\w))")


def current_branch() -> str | None:
    # encoding is pinned rather than left to the locale: text=True decodes with
    # the console codepage, which is cp1252 on Windows and raises on any branch
    # name outside it.
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S607
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def violations(command: str) -> list[str]:
    found: list[str] = []

    if COMMIT.search(command) and current_branch() == PROTECTED_BRANCH:
        found.append(
            f"Direct commit on {PROTECTED_BRANCH}. Branch first:\n"
            f"    git checkout -b feat/phase-N-<slug>\n"
            f"{PROTECTED_BRANCH} only moves through a merged pull request."
        )

    if SQUASH.search(command):
        found.append(
            "Squashing is forbidden in this repository. The per-commit history "
            "is a deliverable, not scaffolding. Use --merge / --no-ff."
        )

    if FORCE_PUSH.search(command):
        found.append(
            "Force push detected. If you genuinely need to rewrite a pushed "
            "branch, use --force-with-lease and never on " + PROTECTED_BRANCH + "."
        )

    return found


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not command:
        return 0

    found = violations(command)
    if not found:
        return 0

    print("BLOCKED by the Praxis git workflow rules:", file=sys.stderr)
    for item in found:
        print(f"  - {item}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
