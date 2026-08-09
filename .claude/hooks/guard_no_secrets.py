#!/usr/bin/env python3
"""Refuse to let a credential enter the repository.

Two entry points, one rule set:

* ``--staged`` scans the staged diff. Wired into pre-commit, so it also fires
  for commits made by a human or by any tool that is not Claude Code.
* No arguments reads a Claude Code ``PreToolUse`` payload on stdin and blocks
  the write before the file is ever touched.

Exit 2 is the Claude Code blocking convention: stdout is ignored and stderr is
handed back to the model as the reason. Pre-commit treats any non-zero exit as
failure, so the same code serves both.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# Paths whose whole job is to talk about credential shapes. Scanning them for
# credential shapes finds only themselves.
EXEMPT_PREFIXES: tuple[str, ...] = (
    ".claude/hooks/",
    "tests/hooks/",
)

# Values that are obviously placeholders rather than live secrets.
PLACEHOLDER = re.compile(
    r"^(|x{3,}|\.{3}|changeme|your[-_ ]?\w+|<[^>]+>|\$\{[^}]+\}|(?:place)?holder)$",
    re.IGNORECASE,
)

VENDOR_PATTERNS: dict[str, re.Pattern[str]] = {
    "Anthropic API key": re.compile(r"sk-" + r"ant-[A-Za-z0-9_\-]{20,}"),
    "OpenAI API key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "AWS access key id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}\b"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

# key = "something long", where the value is not a placeholder.
ASSIGNMENT = re.compile(
    r"""(?ix)
    \b (?: api[_-]?key | secret(?:[_-]?key)? | password | passwd
         | auth[_-]?token | access[_-]?token | client[_-]?secret )
    \s* [:=] \s*
    ["']? (?P<value> [^\s"',]{12,} ) ["']?
    """
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git and decode its output as UTF-8, whatever the console codepage is.

    subprocess's text=True decodes with the locale encoding, which on Windows
    is cp1252. A repository file containing anything outside that range -- a
    box-drawing character in a diagram, a non-Latin identifier -- then raises
    inside subprocess's reader thread, leaving stdout as None. errors="replace"
    is correct here because this scanner only ever pattern-matches; a mangled
    character cannot turn a non-secret into a secret.
    """
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )


def _is_exempt(path: str) -> bool:
    """Whether a path is one of the files allowed to discuss credential shapes.

    Matched anywhere in the path rather than only at the start: the staged scan
    sees repository-relative paths, while a Claude Code tool payload carries an
    absolute one, and both must resolve to the same answer.
    """
    normalised = path.replace("\\", "/")
    return any(prefix in normalised for prefix in EXEMPT_PREFIXES)


def scan_text(text: str, origin: str) -> list[str]:
    """Return a human-readable finding for every credential-shaped hit."""
    findings: list[str] = []
    for label, pattern in VENDOR_PATTERNS.items():
        if pattern.search(text):
            findings.append(f"{origin}: looks like a live {label}")
    for match in ASSIGNMENT.finditer(text):
        value = match.group("value")
        if not PLACEHOLDER.match(value):
            findings.append(f"{origin}: credential-shaped assignment {match.group(0)[:48]!r}")
    return findings


def _dotenv_violation(path: str) -> str | None:
    name = Path(path).name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return f"{path}: real dotenv files are never committed; edit .env.example instead"
    return None


def check_staged() -> int:
    """Scan everything currently staged for commit."""
    listing = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    if listing.returncode != 0:
        print(f"could not list staged files: {listing.stderr}", file=sys.stderr)
        return 1

    findings: list[str] = []
    for name in listing.stdout.split():
        if _is_exempt(name):
            continue
        dotenv = _dotenv_violation(name)
        if dotenv:
            findings.append(dotenv)
            continue
        blob = _git("show", f":{name}")
        if blob.returncode == 0 and blob.stdout:
            findings.extend(scan_text(blob.stdout, name))

    return _report(findings)


def check_tool_call() -> int:
    """Inspect a Claude Code PreToolUse payload arriving on stdin."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0  # Never block on a payload we cannot parse.

    tool_input = payload.get("tool_input") or {}
    path = str(tool_input.get("file_path") or "")

    findings: list[str] = []
    if path and not _is_exempt(path):
        dotenv = _dotenv_violation(path)
        if dotenv:
            findings.append(dotenv)
        written = "\n".join(
            str(tool_input.get(key) or "") for key in ("content", "new_string", "command")
        )
        findings.extend(scan_text(written, path or "tool input"))

    return _report(findings)


def _report(findings: list[str]) -> int:
    if not findings:
        return 0
    print("BLOCKED: credential material detected.", file=sys.stderr)
    for finding in findings:
        print(f"  - {finding}", file=sys.stderr)
    print(
        "\nSecrets belong in .env, which is gitignored. Document the variable "
        "in .env.example with an empty value instead.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str]) -> int:
    if "--staged" in argv:
        return check_staged()
    return check_tool_call()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
