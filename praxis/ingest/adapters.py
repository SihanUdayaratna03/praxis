"""Normalising a source into the exact bytes every span offset addresses.

Deterministic parsing, never a model -- `SourceAdapter` is in `NON_LLM_AGENTS`
and `praxis doctor` fails if it ever acquires a route.

The whole package rests on one sentence: **`Document.content` is the authority,
not the file on disk.** An offset is an offset into these bytes, so what
normalisation does or does not do here decides what a citation means everywhere
downstream. Three transformations, and the reason each is safe:

- **The byte-order mark is dropped.** A leading U+FEFF is an encoding artefact,
  not text. Left in place it would sit inside the first span of every Windows
  document and be quoted back by every agent that cites one.
- **`\\r\\n` and a bare `\\r` become `\\n`.** Otherwise the same document
  checked out on two platforms produces two different byte lengths, two
  different content hashes and two different sets of span ids -- and the
  content-addressed id scheme in ADR 0008 would stop being a de-duplication
  mechanism.
- **Text is normalised to NFC.** The same visible character can be one code
  point or two, and a model quoting text back in the other spelling would fail
  `VerifierAgent` for a reason that has nothing to do with hallucination.

And what is deliberately *not* done: no trailing-whitespace stripping, no
re-wrapping, no blank-line collapsing, no added final newline. Each would be
harmless to read and would move every offset after it, and none of them buys
anything -- the block grid already ignores whitespace between blocks. A
normalisation step that is not needed is a normalisation step that can be
wrong.

**JSON is the exception worth stating plainly.** A JSON source is re-rendered
as one `path: value` block per leaf, and the document's offsets address that
rendering rather than the file. Rendering it is the alternative to leaving
prose trapped behind string escapes, where every quotation would carry a
literal `\\n` and no span would resolve against anything a human recognises.
The rendering is a pure function of the parsed document, so re-ingesting the
same file produces the same offsets and the same span ids.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Final, Protocol

from praxis.domain.enums import SourceKind
from praxis.domain.ids import DocumentId
from praxis.domain.records import Document
from praxis.ingest.errors import (
    EmptySourceError,
    MalformedSourceError,
    SourceDecodeError,
    UnsupportedSourceError,
)

ADAPTER_NAME: Final = "SourceAdapter"
"""The actor recorded on a document this package wrote.

Spelled the same as the entry in `praxis.config.models.NON_LLM_AGENTS`, so the
name in the audit trail is the same name the routing table refuses to route.
"""

MAX_TITLE_CHARS: Final = 120
"""Above this a first line is prose, not a title.

A title is a convenience for a human reading `praxis store stats`; guessing one
out of a paragraph would put a sentence fragment in a column people skim.
"""

BLOCK_SEPARATOR: Final = "\n\n"
"""What separates two blocks of text anywhere Praxis writes one.

The JSON rendering uses it, and so does the corpus generator, which is why it
is named once here rather than spelled in both."""

_BOM: Final = "﻿"
_ROOT_LABEL: Final = "value"
"""What a JSON document that is a bare scalar gets called in its rendering."""

_ATX_HEADING: Final = re.compile(r"^\s{0,3}(#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
_TITLE_KEYS: Final = ("title", "subject", "name", "summary")


@dataclass(frozen=True, slots=True)
class NormalisedSource:
    """One source, decoded and normalised, before it becomes a record.

    Separate from `Document` because a document needs an id, and ids are the
    store's to allocate. An adapter that had to reach a database to normalise a
    file would be a parser with a dependency it has no use for.

    Attributes:
        source_uri: Where this came from. Kept verbatim so a finding can point
            a human back at the file.
        source_kind: Which adapter produced the text.
        text: The exact content every span offset is measured against.
        title: A human-readable name, when the source offered one.
    """

    source_uri: str
    source_kind: SourceKind
    text: str
    title: str | None = None


class SourceAdapter(Protocol):
    """One way of turning bytes into addressable text.

    New formats -- email, chat exports, transcripts -- arrive as new
    implementations and change nothing else, which is what `BACKLOG.md` means
    when it says the later adapters are additive.
    """

    source_kind: ClassVar[SourceKind]

    def normalise(self, raw: bytes, *, source_uri: str) -> NormalisedSource:
        """Decode and normalise one source.

        Raises:
            SourceDecodeError: if the bytes are not UTF-8.
            MalformedSourceError: if the content is not the shape expected.
            EmptySourceError: if nothing addressable survives normalisation.
        """
        ...


class MarkdownAdapter:
    """Markdown, kept as written.

    Markdown is already plain text, so there is nothing to render and nothing
    to strip: the marks are part of the document, and a heading a model reads
    is a heading a human wrote. The only thing read out of it is the title.
    """

    source_kind: ClassVar[SourceKind] = SourceKind.MARKDOWN

    def normalise(self, raw: bytes, *, source_uri: str) -> NormalisedSource:
        """Normalise markdown, titling it from its first heading."""
        text = require_content(normalise_bytes(raw, source_uri), source_uri)
        return NormalisedSource(
            source_uri=source_uri,
            source_kind=self.source_kind,
            text=text,
            title=_markdown_title(text),
        )


class TextAdapter:
    """Plain text, kept as written.

    Titled only when the source clearly offered one: a short first line
    followed by a blank line, which is how a status update or a meeting note is
    actually laid out. Anything else is left untitled rather than guessed at.
    """

    source_kind: ClassVar[SourceKind] = SourceKind.TEXT

    def normalise(self, raw: bytes, *, source_uri: str) -> NormalisedSource:
        """Normalise plain text, titling it only when it looks titled."""
        text = require_content(normalise_bytes(raw, source_uri), source_uri)
        return NormalisedSource(
            source_uri=source_uri,
            source_kind=self.source_kind,
            text=text,
            title=_text_title(text),
        )


class JsonAdapter:
    """JSON, rendered leaf by leaf so its prose is addressable.

    Every leaf becomes one block, `path: value`, with a multi-line value put on
    the lines after its path so the prose is quoted verbatim rather than
    escaped. Order follows the document rather than being sorted: key order in
    an export usually reflects how it was written, and re-ordering it would
    move offsets for no reason a reader could see.
    """

    source_kind: ClassVar[SourceKind] = SourceKind.JSON

    def normalise(self, raw: bytes, *, source_uri: str) -> NormalisedSource:
        """Parse JSON and render it as addressable text.

        Raises:
            MalformedSourceError: if the source is not valid JSON.
        """
        decoded = normalise_bytes(raw, source_uri)
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError as exc:
            message = f"{source_uri} is not valid JSON: {exc}"
            raise MalformedSourceError(message) from exc
        rendered = require_content(_canonicalise(_render_json(parsed)), source_uri)
        return NormalisedSource(
            source_uri=source_uri,
            source_kind=self.source_kind,
            text=rendered,
            title=_json_title(parsed),
        )


MARKDOWN_ADAPTER: Final = MarkdownAdapter()
TEXT_ADAPTER: Final = TextAdapter()
JSON_ADAPTER: Final = JsonAdapter()

_BY_SUFFIX: Final[Mapping[str, SourceAdapter]] = MappingProxyType(
    {
        ".md": MARKDOWN_ADAPTER,
        ".markdown": MARKDOWN_ADAPTER,
        ".txt": TEXT_ADAPTER,
        ".text": TEXT_ADAPTER,
        "": TEXT_ADAPTER,
        ".json": JSON_ADAPTER,
    }
)

SUPPORTED_SUFFIXES: Final[tuple[str, ...]] = tuple(
    sorted(suffix or "(no extension)" for suffix in _BY_SUFFIX)
)


def adapter_for(path: Path) -> SourceAdapter:
    """Return the adapter that claims a file, by extension.

    By extension rather than by sniffing the content, because the answer has to
    be the same on every machine and for every re-ingestion: a content sniffer
    that changed its mind about one file would change every span id cut from it.

    Raises:
        UnsupportedSourceError: if nothing claims it. Never falls back to the
            text adapter -- see that class for why.
    """
    try:
        return _BY_SUFFIX[path.suffix.lower()]
    except KeyError as exc:
        raise UnsupportedSourceError(path, SUPPORTED_SUFFIXES) from exc


def read_source(path: Path) -> NormalisedSource:
    """Read one file and normalise it with whichever adapter claims it.

    Raises:
        UnsupportedSourceError: if no adapter claims the extension.
        SourceDecodeError: if the bytes are not UTF-8.
        MalformedSourceError: if the content is not the shape expected.
        EmptySourceError: if nothing addressable survives normalisation.
        OSError: if the file cannot be read. Left untranslated: a missing file
            is the caller's problem to report, not this package's to rename.
    """
    return adapter_for(path).normalise(path.read_bytes(), source_uri=path.as_posix())


def normalise_bytes(raw: bytes, source_uri: str) -> str:
    """Decode UTF-8 and apply the three normalisations offsets depend on.

    Raises:
        SourceDecodeError: if the bytes are not UTF-8. Never repaired with a
            replacement character, which would change the byte length every
            offset is measured against.
    """
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        message = (
            f"{source_uri} is not UTF-8: {exc.reason} at byte {exc.start}. "
            f"Convert it rather than letting a replacement character move every span offset."
        )
        raise SourceDecodeError(message) from exc
    return _canonicalise(decoded.removeprefix(_BOM))


def require_content(text: str, source_uri: str) -> str:
    """Return the text, or refuse a source no span could ever address.

    Raises:
        EmptySourceError: if the text holds no non-whitespace character.
    """
    if not text.strip():
        message = f"{source_uri} normalises to whitespace, so no span could ever address it"
        raise EmptySourceError(message)
    return text


def document_from(
    source: NormalisedSource,
    *,
    doc_id: DocumentId,
    ingested_at: datetime,
    created_by: str = ADAPTER_NAME,
) -> Document:
    """Build the `Document` record for a normalised source.

    Args:
        source: The normalised text and what is known about where it came from.
        doc_id: Allocated by the store, which is the only thing that can.
        ingested_at: When this was read. Timezone-aware, invariant 5.
        created_by: The actor for the audit trail.
    """
    title = (source.title or "").strip()
    return Document(
        id=doc_id,
        source_uri=source.source_uri,
        source_kind=source.source_kind,
        title=title or None,
        content=source.text,
        ingested_at=ingested_at,
        created_at=ingested_at,
        created_by=created_by,
    )


def _canonicalise(text: str) -> str:
    """Apply the newline and Unicode normalisations, and nothing else."""
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def _markdown_title(text: str) -> str | None:
    """The first ATX heading in a markdown document, if it has one."""
    for line in text.splitlines():
        match = _ATX_HEADING.match(line)
        if match is not None:
            return _fit_title(match.group("title"))
    return None


def _text_title(text: str) -> str | None:
    """A plain-text title, only where the layout clearly declares one."""
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        return None
    followed_by_blank = len(lines) == 1 or not lines[1].strip()
    return _fit_title(lines[0]) if followed_by_blank else None


def _json_title(parsed: Any) -> str | None:
    """The first title-shaped top-level string in a JSON document."""
    if not isinstance(parsed, Mapping):
        return None
    for key in _TITLE_KEYS:
        candidate = parsed.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return _fit_title(candidate)
    return None


def _fit_title(candidate: str) -> str | None:
    """Accept a title only if it is short enough to be one."""
    stripped = candidate.strip()
    return stripped if 0 < len(stripped) <= MAX_TITLE_CHARS else None


def json_leaves(parsed: Any) -> tuple[tuple[str, str], ...]:
    """Return a parsed JSON document's leaves, each with its dotted path.

    Public because the corpus generator builds a JSON document out of leaves
    and has to know the byte offset of each one in the *rendering*, which is
    what a span addresses. Sharing the function is what makes those offsets
    right by construction rather than by a search over the finished text --
    ADR 0012's rejected shortcut.
    """
    return tuple(_leaves(parsed, ""))


def render_leaf(path: str, value: str) -> str:
    """Render one leaf as its block: `path: value`, or the value on its own lines."""
    label = path or _ROOT_LABEL
    return f"{label}:\n{value}" if "\n" in value else f"{label}: {value}"


def render_leaves(leaves: Sequence[tuple[str, str]]) -> str:
    """Join rendered leaves into the text a JSON document normalises to."""
    return BLOCK_SEPARATOR.join(render_leaf(path, value) for path, value in leaves) + "\n"


def _render_json(parsed: Any) -> str:
    """Render a parsed JSON document as one `path: value` block per leaf."""
    return render_leaves(json_leaves(parsed))


def _leaves(value: Any, path: str) -> Iterator[tuple[str, str]]:
    """Walk a parsed JSON document, yielding each leaf with its dotted path.

    An empty container yields its own literal rather than nothing, so a key
    whose value is `[]` stays visible in the rendering -- "the field is there
    and it is empty" and "the field is absent" are different facts, and an
    agent reading a status export will be asked to tell them apart.
    """
    if isinstance(value, Mapping):
        if not value:
            yield path, "{}"
        for key, item in value.items():
            yield from _leaves(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        if not value:
            yield path, "[]"
        for index, item in enumerate(value):
            yield from _leaves(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value
    else:
        yield path, json.dumps(value)
