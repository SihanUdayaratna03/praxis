"""The prompt library: what an agent was told, kept where it can be cited.

A prompt is the largest untyped input in this system and the one most likely to
be changed casually. `SegmenterAgent` shipped its system prompt as a module
constant, which was fine while there was one agent and stops being fine the
moment a metric is computed over an extraction: a table saying "recall 0.72"
means nothing unless the prompt that produced it can still be read. ADR 0014
argues the three decisions this module encodes.

- **A prompt lives in a file, and the filename is its metadata.** `name` and
  `version` are read off `<task>.v<n>.md` rather than out of a header inside the
  file, because a header can disagree with the file it sits in and a filename
  cannot. The same reasoning, and the same `importlib.resources` mechanism, as
  `praxis.store.migrations`.
- **The name *is* the task name.** `LLMRequest.task` and the prompt file agree
  by construction rather than by convention, so `task` and `prompt_id` on a
  trace row can never describe two different things.
- **A version bump is a new file, never an edit.** Nothing here can enforce
  that at runtime -- a file that has been edited looks exactly like a file that
  was always that way. `tests/prompts/test_library.py` pins the SHA-256 of every
  shipped prompt, so editing one without bumping its version fails the suite.
  That is the enforcement, and it is deliberate that it is a test: the property
  being protected is about the repository's history, which is not something a
  running process can see.

Interpolation is `string.Template`, not `str.format`. Prompts contain JSON
examples and predicate syntax, both of which are full of braces, and a prompt
language that requires escaping them is a prompt language that will eventually
be escaped wrongly.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from string import Template
from typing import Final

PROMPT_ANCHOR: Final = "praxis.prompts"
TEXTS_DIRECTORY: Final = "texts"

_FILENAME_RE: Final = re.compile(
    r"^(?P<name>[a-z0-9]+(?:_[a-z0-9]+)*)\.v(?P<version>\d+)\.md$",
)
"""`<task>.v<n>.md`. Snake case, matching `LLMRequest.task`, and a bare integer
version so that ordering is numeric rather than lexicographic -- `v10` sorts
after `v9`, which a zero-padded scheme would also give and a string sort would
not."""


class PromptError(Exception):
    """Something is wrong with the prompt library itself."""


class PromptNotFoundError(PromptError):
    """No prompt of that name, or no such version of it."""


class PromptRenderError(PromptError):
    """A prompt was rendered without a value its template needs.

    Raised rather than left as an unsubstituted `$placeholder`, because a
    placeholder that reaches a model is a prompt that quietly asks a different
    question -- and it would be visible only to whoever reads the trace.
    """


@dataclass(frozen=True, slots=True)
class Prompt:
    """One version of one prompt, as it shipped."""

    name: str
    """The task it serves, spelled as `LLMRequest.task` spells it."""

    version: int
    template: str
    """The file's exact text. Rendered through `render`, never used raw by an
    agent -- a prompt with no placeholders still goes through it, so that adding
    one later is not a change at every call site."""

    @property
    def id(self) -> str:
        """`<name>@v<n>` -- what a trace row records and a report cites."""
        return f"{self.name}@v{self.version}"

    @property
    def sha256(self) -> str:
        """The digest of the text, so a trace can prove which bytes it read.

        Recorded alongside the id rather than instead of it. The id is what a
        person reads; the digest is what catches a file that was edited in
        place, which is the failure the id alone cannot see.
        """
        return hashlib.sha256(self.template.encode("utf-8")).hexdigest()

    def render(self, **values: object) -> str:
        """Substitute the template's placeholders, refusing to leave one behind.

        Args:
            **values: One entry per `$placeholder` in the template.

        Returns:
            The finished system prompt.

        Raises:
            PromptRenderError: if a placeholder has no value, or the template
                contains a malformed `$` expression.
        """
        try:
            return Template(self.template).substitute(values)
        except (KeyError, ValueError) as exc:
            message = f"{self.id} could not be rendered: {exc}"
            raise PromptRenderError(message) from exc


@lru_cache(maxsize=1)
def every_prompt() -> tuple[Prompt, ...]:
    """Every prompt this build ships, by name then ascending version.

    Read through `importlib.resources` rather than by walking `__file__`, so an
    installed wheel and a source checkout resolve the same files.

    Raises:
        PromptError: if a file in the directory is not a prompt filename. A
            stray file is a prompt somebody meant to ship and misnamed, and
            skipping it silently is how an agent ends up on the wrong version.
    """
    directory = files(PROMPT_ANCHOR) / TEXTS_DIRECTORY
    found: list[Prompt] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        match = _FILENAME_RE.match(entry.name)
        if match is None:
            message = f"{entry.name!r} is not a prompt filename; they are named like scan_it.v1.md"
            raise PromptError(message)
        found.append(
            Prompt(
                name=match.group("name"),
                version=int(match.group("version")),
                template=entry.read_text(encoding="utf-8"),
            )
        )
    return tuple(sorted(found, key=lambda prompt: (prompt.name, prompt.version)))


def versions_of(name: str) -> tuple[int, ...]:
    """Every version of one prompt that ships, ascending. Empty if none do."""
    return tuple(prompt.version for prompt in every_prompt() if prompt.name == name)


def load(name: str, *, version: int | None = None) -> Prompt:
    """Return one prompt, defaulting to the newest version of it.

    Args:
        name: The task the prompt serves.
        version: A specific version. Agents leave this unset and get the
            newest, which is what makes a version bump a one-file change; a
            replay of an old run passes the version the trace recorded.

    Raises:
        PromptNotFoundError: if there is no such prompt, or no such version.
    """
    candidates = [prompt for prompt in every_prompt() if prompt.name == name]
    if not candidates:
        known = ", ".join(sorted({prompt.name for prompt in every_prompt()})) or "nothing"
        message = f"no prompt named {name!r}; this build ships {known}"
        raise PromptNotFoundError(message)
    if version is None:
        return candidates[-1]
    for prompt in candidates:
        if prompt.version == version:
            return prompt
    shipped = ", ".join(str(prompt.version) for prompt in candidates)
    message = f"{name!r} has no version {version}; this build ships {shipped}"
    raise PromptNotFoundError(message)
