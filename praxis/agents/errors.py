"""What can go wrong between a span and a stored record.

The distinction this module exists to hold is the one `SegmenterAgent` already
draws and the one every Half A agent inherits: **a bad answer about one document
is not a broken run.** A model that refuses, truncates or invents a citation has
failed at this document, and the pipeline records that and carries on. A cost
ceiling, a transport failure or a store that will not write has failed at the
*run*, and those are `praxis.llm.errors` and `praxis.store.errors` propagating
through untouched.

Nothing here is raised by an agent at a caller who could act on it per
document. Extraction failures are *returned* -- as a rejection carried in the
result -- because a run over two hundred documents that dies on the third has
said nothing about the other hundred and ninety-seven, and because the rate at
which extractions are refused is a number the eval harness reports rather than
an exception nobody counts.
"""

from __future__ import annotations

from enum import StrEnum


class ExtractionError(Exception):
    """A failure in the extraction layer itself, not in a model's answer.

    Deliberately thin, and deliberately not raised on a bad answer. It covers
    the cases where the *code* is wrong -- an offering built across two
    documents, a candidate resolved against the wrong listing -- which are bugs
    to fail loudly on rather than data to be tolerant of.
    """


class Refusal(StrEnum):
    """Why one extraction did not enter the store.

    Every member is a *different* defect with a different response in a live
    run, which is why this is an enum rather than a free-text reason. The eval
    harness groups by it, so a run that refuses two hundred extractions says
    which two hundred failures they were.
    """

    UNOFFERED_SPAN = "unoffered_span"
    """The agent cited a passage number it was not shown. Impossible to express
    as a fabricated span id thanks to ADR 0015, and still possible as an
    out-of-range integer."""

    MIS_ATTRIBUTED_QUOTE = "mis_attributed_quote"
    """The quotation is in a passage the agent was shown, but not the one it
    cited. The model read the material and pointed at the wrong part of it."""

    FABRICATED_QUOTE = "fabricated_quote"
    """The quotation is in none of the passages the agent was shown. This is the
    hallucinated citation invariant 6 is about."""

    SPAN_DOES_NOT_RESOLVE = "span_does_not_resolve"
    """`VerifierAgent` could not re-read the cited span against its document at
    all -- which, for a span this pipeline produced, means the document changed
    underneath the run."""

    INCOHERENT_RECORD = "incoherent_record"
    """The answer was well cited and could not be made into a valid record: a
    decision with no rejected option, a quantity that is not a number, a status
    that contradicts its evaluation time. The record models are the authority
    and they refused it."""

    EMPTY_ANSWER = "empty_answer"
    """The model was asked and found nothing. Not an error -- most spans hold no
    decision -- but recorded, because "found nothing" and "was never asked" are
    different rows in an eval table."""

    NO_USABLE_ANSWER = "no_usable_answer"
    """The model refused, or never produced output satisfying its schema within
    the repair budget. A bad answer about this document, handled like one."""
