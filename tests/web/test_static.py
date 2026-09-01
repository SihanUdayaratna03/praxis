"""The frontend's own gate. There is no npm here, so this is the linter.

The rule worth mechanising is ADR 0036's third assumption: the page renders
with no network beyond the local server. A `<script src="https://cdn...">` is
one paste away and would not fail anything else in this repository -- the suite
would stay green and the dashboard would go blank on the machine it was built
to serve.

The rest is what a build step would have caught: a stylesheet that is
referenced and missing, an id the script writes to that no element has.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from praxis.web.server import STATIC_ROOT

PAGES = sorted(STATIC_ROOT.glob("*.html"))
ASSETS = sorted(p for p in STATIC_ROOT.iterdir() if p.is_file())

REMOTE = re.compile(r"""(?:src|href|url)\s*[=(]\s*["']?(https?:)?//""", re.IGNORECASE)
"""Anything pointing off this origin, protocol-relative URLs included."""

WRITES_TO = re.compile(r"""getElementById\(\s*["']([^"']+)["']\s*\)""")
"""Every id the scripts look up."""

DECLARES_ID = re.compile(r"""(?<![-\w])id=["']([A-Za-z][-\w]*)["']""")
"""Every id declared in markup, including the markup a script builds.

A view that renders its own container declares the id in a template string
rather than in a page, and that is still a declaration.
"""


class Ids(HTMLParser):
    """Collect every id and every local reference a page declares."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.refs: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if found.get("id"):
            self.ids.add(str(found["id"]))
        for name in ("src", "href"):
            value = found.get(name)
            if value and value.startswith("/static/"):
                self.refs.add(value)


def parsed(page: Path) -> Ids:
    """Read one page."""
    reader = Ids()
    reader.feed(page.read_text(encoding="utf-8"))
    return reader


def test_there_are_pages_to_check():
    """A guard over an empty directory passes and means nothing."""
    assert PAGES, "no pages found under praxis/web/static"


@pytest.mark.parametrize("asset", ASSETS, ids=lambda p: p.name)
def test_no_asset_reaches_a_remote_origin(asset: Path):
    """ADR 0036 assumption 3, as a check rather than as a sentence."""
    hits = REMOTE.findall(asset.read_text(encoding="utf-8"))
    assert not hits, (
        f"{asset.name} references a remote origin. The dashboard has to render with no "
        f"network beyond the local server, so every asset is served from /static."
    )


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_page_parses(page: Path):
    """The cheapest thing a build step would have done."""
    parsed(page)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_referenced_asset_exists(page: Path):
    """A stylesheet that 404s is a page that renders unstyled and green tests."""
    for ref in parsed(page).refs:
        assert (STATIC_ROOT / ref.removeprefix("/static/")).is_file(), f"{page.name} -> {ref}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_page_declares_a_title_and_a_language(page: Path):
    text = page.read_text(encoding="utf-8")
    assert "<title>" in text
    assert 'lang="en"' in text


def test_every_id_the_scripts_write_to_is_declared_somewhere():
    """The failure mode a renamed element causes, which is a silent blank field."""
    declared: set[str] = set()
    for page in PAGES:
        declared |= parsed(page).ids
    scripts = sorted(STATIC_ROOT.glob("*.js"))
    for script in scripts:
        declared |= set(DECLARES_ID.findall(script.read_text(encoding="utf-8")))
    for script in scripts:
        for target in WRITES_TO.findall(script.read_text(encoding="utf-8")):
            assert target in declared, f"{script.name} writes to #{target}, which nothing declares"


@pytest.mark.parametrize(
    "line",
    [
        '<script src="https://cdn.example.com/chart.js"></script>',
        '<link rel="stylesheet" href="http://fonts.example.com/inter.css" />',
        '<script src="//cdn.example.com/d3.js"></script>',
        "@font-face { src: url(https://fonts.example.com/inter.woff2); }",
    ],
)
def test_the_remote_detector_catches_a_paste_it_has_never_seen(line: str):
    """Every real asset passes, so the passing suite proves only that no CDN
    reference exists -- not that one would be noticed. This runs the same
    reader over text that does reach out."""
    assert REMOTE.search(line)


def test_the_id_detector_catches_a_lookup_nothing_declares(tmp_path):
    """Watched failing, like the others. A typo is the case this exists for."""
    assert not DECLARES_ID.findall('const el = document.getElementById("typo");')
    assert DECLARES_ID.findall('<div id="real"></div>')


def test_the_detector_does_not_flag_a_local_reference():
    """The false positive that would get this check deleted."""
    assert not REMOTE.search('<link rel="stylesheet" href="/static/praxis.css" />')
    assert not REMOTE.search('<a href="#thesis">How it works</a>')
