"""The two seams, asserted over the source rather than trusted to review.

`praxis.llm` claims to be the only part of Praxis that knows a network exists,
and `praxis.store` claims the same about SQLite. Both claims are worth exactly
as much as the rule that nothing crosses them -- and both are the kind of rule
that survives the commit that states it and dies three phases later, when one
convenient import makes a seam decorative without anything failing.

So the check is mechanical: parse every module in the package, read what it
imports, and fail the build on a crossing. It reads the source with `ast`
rather than importing anything, because an import-based check would pass on a
machine where the optional SDK is not installed -- which is exactly the machine
CI runs on.
"""

from __future__ import annotations

import ast
from pathlib import Path

import praxis
import pytest
from praxis.config.models import NON_LLM_AGENTS

PACKAGE_ROOT = Path(praxis.__file__).resolve().parent

NETWORK_MODULES = frozenset(
    {
        # Vendor SDKs.
        "anthropic",
        "openai",
        # HTTP clients, including the ones that arrive as transitive
        # dependencies and are therefore importable without being declared.
        "httpx",
        "requests",
        "aiohttp",
        "urllib3",
        "http",
        "urllib",
        "socket",
        "ssl",
        "websockets",
        # The ASGI server. It is what actually binds the socket, and it was
        # not in this set because no web framework existed when the set was
        # written -- so a dashboard would have imported cleanly and left the
        # seam decorative. ADR 0037.
        "uvicorn",
    }
)
"""Anything whose presence means a module can reach the network.

`http`, `urllib`, `socket` and `ssl` are standard library and would not appear
in a dependency audit, which is the reason to name them here: the seam has to
hold against the stdlib too, or "no HTTP client" means only "no third-party
HTTP client".
"""

LLM_SEAM = "praxis/llm/anthropic.py"
"""The vendor SDK's side of the network."""

WEB_SEAM = "praxis/web/server.py"
"""The dashboard's side of it. ADR 0037.

`praxis serve` has to open a listening socket somewhere, and this is where.
Named as a path rather than inferred from a directory, so that adding a third
seam is a visible edit to this test rather than a file dropped in a folder.
"""

SEAMS = frozenset({LLM_SEAM, WEB_SEAM})
"""Every module allowed through. Two, and they are both named."""

WEB_FRAMEWORK_MODULES = frozenset({"fastapi", "starlette"})
"""The request framework, which is a different question from the network.

FastAPI and Starlette parse requests; they open nothing. So they are not in
`NETWORK_MODULES` -- a rule that put them there would force every route into
the one seam file. They are confined to `praxis/web/` instead, which is the
claim actually worth holding: the framework must not leak into an agent, the
store or the eval harness.
"""

WEB_PACKAGE = "praxis/web/"
"""The only package that may know what a request is."""


def source_files() -> list[Path]:
    """Every module in the package, in a stable order."""
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def imports_of(path: Path) -> set[str]:
    """Return the top-level module names a file imports.

    Only the root of a dotted path is kept: `import http.client` and
    `from urllib.request import urlopen` both reduce to the package that
    matters, and `from praxis.llm.anthropic import ...` reduces to `praxis`,
    which is how a module named after the SDK avoids being confused with it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def relative(path: Path) -> str:
    """The package-relative path, spelled the same way on every platform."""
    return path.relative_to(PACKAGE_ROOT.parent).as_posix()


@pytest.mark.parametrize("path", source_files(), ids=relative)
def test_only_the_named_seams_can_reach_the_network(path: Path):
    """No module but the two named seams may import an SDK, a client or a server."""
    crossings = imports_of(path) & NETWORK_MODULES
    if relative(path) in SEAMS:
        return
    assert not crossings, (
        f"{relative(path)} imports {sorted(crossings)}. Only {sorted(SEAMS)} may reach the "
        f"network; everything else speaks LLMRequest, LLMResponse, or a store read."
    )


@pytest.mark.parametrize("seam", sorted(SEAMS))
def test_the_seam_actually_exists(seam: str):
    """A test that passes because the file it guards was deleted is not a test."""
    assert (PACKAGE_ROOT.parent / seam).is_file()


def test_the_llm_seam_does_import_the_sdk():
    """And the whole check is vacuous if nothing was ever on the other side."""
    assert "anthropic" in imports_of(PACKAGE_ROOT.parent / LLM_SEAM)


def test_the_web_seam_does_import_the_server():
    """The same argument for the second seam."""
    assert "uvicorn" in imports_of(PACKAGE_ROOT.parent / WEB_SEAM)


@pytest.mark.parametrize("path", source_files(), ids=relative)
def test_the_web_framework_stops_at_the_web_package(path: Path):
    """An agent, the store and the eval harness must not know what a request is.

    Separate from the network rule because it is a separate claim. Importing
    FastAPI opens nothing; it just means a module has started answering HTTP,
    and the place that is allowed to do that is `praxis/web/`.
    """
    crossings = imports_of(path) & WEB_FRAMEWORK_MODULES
    if relative(path).startswith(WEB_PACKAGE):
        return
    assert not crossings, (
        f"{relative(path)} imports {sorted(crossings)}. The web framework stops at "
        f"{WEB_PACKAGE}; everything below it is called, not routed to."
    )


@pytest.mark.parametrize(
    "line",
    [
        "import httpx",
        "import http.client",
        "from urllib.request import urlopen",
        "import anthropic",
        "import uvicorn",
    ],
)
def test_the_detector_catches_a_crossing_it_has_never_seen(tmp_path, line):
    """A guard nobody has watched fail is a guard nobody knows the shape of.

    Every real module passes, so the passing suite proves only that no
    crossing exists -- not that one would be noticed. This runs the same
    reader over a module that does cross.
    """
    offender = tmp_path / "convenient.py"
    offender.write_text(f"{line}\n", encoding="utf-8")
    assert imports_of(offender) & NETWORK_MODULES


@pytest.mark.parametrize("line", ["import fastapi", "from starlette.requests import Request"])
def test_the_framework_detector_catches_a_crossing_too(tmp_path, line):
    """The second rule, watched failing as well. Same argument as above."""
    offender = tmp_path / "routed.py"
    offender.write_text(f"{line}\n", encoding="utf-8")
    assert imports_of(offender) & WEB_FRAMEWORK_MODULES


def test_the_web_seam_is_inside_the_web_package():
    """The two rules overlap on one file, and that is deliberate rather than luck.

    If the seam ever moved outside `praxis/web/` the framework rule would start
    failing on it, which is a confusing way to learn that the layout changed.
    """
    assert WEB_SEAM.startswith(WEB_PACKAGE)


def test_a_praxis_module_named_after_the_sdk_is_not_the_sdk(tmp_path):
    """The one false positive worth ruling out explicitly.

    `praxis.llm.anthropic` is a Praxis module. A check that matched on the
    name rather than the import root would flag every file that imports the
    live provider, and the cure for that noise is usually to delete the check.
    """
    importer = tmp_path / "elsewhere.py"
    importer.write_text("from praxis.llm.anthropic import AnthropicProvider\n", encoding="utf-8")
    assert not imports_of(importer) & NETWORK_MODULES


@pytest.mark.parametrize(
    "path", [p for p in source_files() if p.parent.name == "llm"], ids=relative
)
def test_the_model_layer_never_imports_the_store(path: Path):
    """`TraceSink` is a Protocol precisely so this stays true.

    The dependency runs the other way: `praxis.store.traces` imports
    `praxis.llm.trace` to write a row. If it ever ran both ways, the provider
    seam would need a database to answer a question, and the promise that a
    mock run needs no configuration at all would quietly become false.
    """
    assert "praxis.store" not in _dotted_imports(path)


@pytest.mark.parametrize(
    "path",
    [p for p in source_files() if p.parent.name != "store"],
    ids=relative,
)
def test_only_the_store_knows_the_database_driver(path: Path):
    """`sqlite3` stops at `praxis.store`, in the failure path as well as the query path."""
    assert "sqlite3" not in imports_of(path)


@pytest.mark.parametrize(
    "path", [p for p in source_files() if p.parent.name == "predicates"], ids=relative
)
def test_the_predicate_language_never_reaches_a_model(path: Path):
    """Invariant 3 names the predicate evaluator, so it is checked as code.

    `praxis.predicates` is not an agent and has no entry in `NON_LLM_AGENTS`,
    which is exactly why this test exists: the registry cannot describe a
    package, and the way determinism would be lost here is one convenient
    import -- a formalizer asking a model to *evaluate* a predicate rather than
    to write one. A predicate whose truth depends on sampling is not a
    predicate, and this is where that stops being a sentence in a document.
    """
    assert "praxis.llm" not in _dotted_imports(path)


DETERMINISTIC_MODULES = {
    "BiasDetective": "praxis/agents/bias.py",
    "CalibratorAgent": "praxis/agents/calibrator.py",
    "CollateralAgent": "praxis/agents/collateral.py",
    "FusionBridge": "praxis/agents/fusion.py",
    "ScoringAgent": "praxis/agents/scoring.py",
    "SourceAdapter": "praxis/ingest/adapters.py",
    "VerifierAgent": "praxis/ingest/verifier.py",
}
"""Where each deterministic agent implemented so far lives.

`NON_LLM_AGENTS` says these must never make a model call and `role_for_agent`
refuses to route them, but both of those are about a *name*. This maps the name
onto the file, so the rule is checked against the code rather than against the
registry that describes it.
"""


@pytest.mark.parametrize(
    ("agent", "module"), sorted(DETERMINISTIC_MODULES.items()), ids=sorted(DETERMINISTIC_MODULES)
)
def test_a_deterministic_agent_cannot_reach_a_model(agent, module):
    """Invariant 3, checked in the place it would actually be broken.

    A verifier that could call a model is not a verifier, and the way that
    would happen is one convenient import in a file nobody re-read -- not
    somebody editing `NON_LLM_AGENTS` to remove the name.
    """
    assert agent in NON_LLM_AGENTS
    assert "praxis.llm" not in _dotted_imports(PACKAGE_ROOT.parent / module)


def test_the_deterministic_modules_named_here_all_exist():
    """A parametrised test over paths that were deleted passes silently."""
    for module in DETERMINISTIC_MODULES.values():
        assert (PACKAGE_ROOT.parent / module).is_file()


def _dotted_imports(path: Path) -> set[str]:
    """Return the two-level module names a file imports, e.g. `praxis.store`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        names = (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
        found.update(".".join(name.split(".")[:2]) for name in names if name)
    return found
