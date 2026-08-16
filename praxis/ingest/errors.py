"""What can go wrong between a file on disk and a span in the store.

The distinctions here are the ones a caller acts on differently: fix the file,
add an adapter, or fix a bug in Praxis. Two failures answered the same way do
not get two classes -- the same argument `praxis.llm.errors` makes about the
provider seam.

Every one of these is about *one source*. A corpus ingestion catches them per
document and carries on, because a run that dies on the third file of two
hundred has told nobody anything about the other one hundred and ninety-seven.
"""

from __future__ import annotations

from pathlib import Path


class IngestionError(Exception):
    """Base class for every failure raised out of `praxis.ingest`."""


class UnsupportedSourceError(IngestionError):
    """Raised when nothing in the adapter registry claims a file.

    Deliberately raised rather than falling back to the text adapter. Reading a
    PDF as UTF-8 text succeeds often enough to produce a document full of
    mojibake, and every span cut from it would be technically valid and
    worthless -- which is a far more expensive failure than being told to add
    an adapter.

    Attributes:
        path: The file nothing claimed.
        suffix: Its extension, as the registry saw it.
    """

    def __init__(self, path: Path, supported: tuple[str, ...]) -> None:
        """Record the file and what the registry does understand.

        Args:
            path: The file nothing claimed.
            supported: The extensions that are handled, for the message.
        """
        self.path = path
        self.suffix = path.suffix.lower()
        super().__init__(
            f"nothing ingests {self.suffix or 'a file with no extension'} ({path}); "
            f"this build reads {', '.join(supported)}"
        )


class SourceDecodeError(IngestionError):
    """Raised when a source's bytes are not UTF-8.

    Not repaired with `errors='replace'`. A replacement character changes the
    byte length of the text every span offset is measured against, so a
    silently repaired document is one whose citations are all subtly wrong
    rather than one that failed.
    """


class MalformedSourceError(IngestionError):
    """Raised when a source decodes but is not the shape its adapter expects.

    In practice: a `.json` file that is not JSON. Separate from
    `SourceDecodeError` because the bytes were fine and the content was not,
    which is a different thing to go and fix.
    """


class EmptySourceError(IngestionError):
    """Raised when a source normalises to nothing worth addressing.

    A document with no non-whitespace character cannot carry a span, so
    ingesting one would write a record no citation can ever point at. Reported
    rather than stored, because an empty file in a corpus is nearly always a
    mistake somebody wants told about.
    """
