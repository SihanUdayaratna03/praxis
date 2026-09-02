"""The README makes checkable claims. These check them.

A stranger's first run comes out of this file, so the commands in it have to
exist and the numbers in it have to be the numbers. Every failure here is the
README having drifted from the code, which is the only way it ever goes wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import praxis
import pytest
import typer
from praxis.cli import app
from praxis.demo.seed import seed
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository

REPO = Path(praxis.__file__).resolve().parent.parent
README = (REPO / "README.md").read_text(encoding="utf-8")

_INVOCATION = re.compile(r"^uv run praxis ([a-z][a-z -]*)", re.MULTILINE)
_SKIP_ARG = re.compile(r"^-")


def commands() -> set[tuple[str, ...]]:
    """Every `uv run praxis ...` in the README, as a command path."""
    found = set()
    for match in _INVOCATION.finditer(README):
        words = tuple(w for w in match.group(1).split() if not _SKIP_ARG.match(w))
        if words:
            found.add(words)
    return found


def resolve(path: tuple[str, ...]) -> object | None:
    """Walk the click tree to the command a path names, or `None`."""
    node: object = typer.main.get_command(app)
    for word in path:
        subcommands = getattr(node, "commands", None)
        if subcommands is None or word not in subcommands:
            return None
        node = subcommands[word]
    return node


def test_the_readme_invokes_commands_that_exist():
    """A renamed command has to break the README, not survive it."""
    assert commands()
    missing = sorted(" ".join(path) for path in commands() if resolve(path) is None)
    assert missing == []


@pytest.mark.parametrize(
    ("claim", "why"),
    [
        ("uv run praxis demo seed", "the demo path is the quickstart"),
        ("uv run praxis serve", "the dashboard is how a stranger looks at it"),
        ("segmenter_f1 - paragraph_floor_f1 >= 0.05", "the breach is the finding shown"),
        ("PRAXIS_LLM_PROVIDER=mock", "invariant 1: no credentials to run anything"),
    ],
)
def test_the_readme_still_says_the_load_bearing_things(claim, why):
    assert claim in README, why


def test_every_internal_link_resolves():
    links = re.findall(r"\]\((?!https?:)([^)#]+)", README)
    assert links
    assert sorted({link for link in links if not (REPO / link).exists()}) == []


def test_the_seeded_record_count_is_the_one_the_readme_quotes():
    """The number a stranger sees on their first run, held to the real files."""
    quoted = re.search(r"It writes ([\d,]+) records", README)
    assert quoted is not None

    connection = connect(MEMORY)
    migrate(connection)
    repository = Repository(connection)
    report = seed(repository, adr_dir=REPO / "docs" / "adr", dogfood_dir=REPO / "docs" / "dogfood")
    written = (
        report.documents
        + report.spans
        + report.decisions
        + report.assumptions
        + report.estimates
        + report.outcomes
        + report.links
    )
    repository.close()
    assert written == int(quoted.group(1).replace(",", ""))
