#!/usr/bin/env python3
"""Run the quality gate on a file the moment it is edited.

A rule that is only written down is a rule that gets discovered at PR time.
This runs ruff (with fixes) and, for library code, mypy strict, on the single
file that just changed. Exit 2 hands the failure straight back to the model
while it still has the change in mind.

Scoped to one file on purpose: the full suite belongs in CI, not in the inner
loop, and a hook that takes ten seconds is a hook someone will disable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TYPE_CHECKED_ROOTS = ("praxis",)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        text=True,
        check=False,
    )


def _relative_parts(path: Path) -> tuple[str, ...]:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).parts
    except ValueError:
        return path.parts


def verify(path: Path) -> list[str]:
    """Return failure reports for the given Python file."""
    reports: list[str] = []
    target = str(path)

    fix = _run(["uv", "run", "--quiet", "ruff", "check", "--fix", target])
    if fix.returncode != 0:
        reports.append("ruff check (unfixable):\n" + (fix.stdout or fix.stderr).strip())

    fmt = _run(["uv", "run", "--quiet", "ruff", "format", target])
    if fmt.returncode != 0:
        reports.append("ruff format:\n" + (fmt.stdout or fmt.stderr).strip())

    if _relative_parts(path)[:1] == TYPE_CHECKED_ROOTS[:1]:
        types = _run(["uv", "run", "--quiet", "mypy", target])
        if types.returncode != 0:
            reports.append("mypy --strict:\n" + (types.stdout or types.stderr).strip())

    return reports


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    raw_path = str((payload.get("tool_input") or {}).get("file_path") or "")
    if not raw_path.endswith(".py"):
        return 0

    path = Path(raw_path)
    if not path.is_file():
        return 0

    reports = verify(path)
    if not reports:
        return 0

    print(f"Quality gate failed for {path}:", file=sys.stderr)
    for report in reports:
        print(report, file=sys.stderr)
    print("\nFix this now rather than at PR time.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
