"""WorkClassifier: which kind of work an estimate is about, on a shared axis.

`Estimate.work_class` is the axis `BiasDetective` groups by, which makes it a
*key* rather than a label, and this is the agent that owns it. Phase 4 said so
before Phase 6 existed: `AssumptionExtractor` writes `unclassified` and
`praxis/agents/extractor.py` records why in as many words -- "a row it can
recognise as unclassified is one it can revise; a row that says `data-migration`
because that seemed likely is one nobody will ever look at again."

So this agent revises rather than writes. Every estimate reaching it already has
a verified citation, and it keeps that citation: a classification is a judgement
*about a record*, not a new claim about the document, and the record's `span_id`
is unchanged by it. There is no second citation path here because there is
nothing new to cite.

**The failure this agent exists to prevent is not a wrong class. It is two
spellings of one class.** A wrong class is visible -- somebody reads
`infrastructure` on a mobile estimate and fixes it. A near-duplicate is
invisible: `data-migration` and `database-migration` quietly turn one person's
ten migrations into two sets of five, and a sample that was just large enough
for `BiasDetective` to speak becomes two samples it refuses on. Nothing raises,
nothing looks wrong, and the calibration history is worth less than it was.

Two mechanisms hold against that, and they are deliberately not the same one.

**The vocabulary is offered, not assumed.** The classifier is shown the classes
already in the store and asked to prefer one. It may propose a new class, and
the answer says which it did -- so a run that invents six new classes for six
estimates is visible as a number rather than discovered later as a fragmented
history. A closed vocabulary was the alternative and it is wrong for a product
that does not know what its users build.

**Spelling is repaired, meaning is not.** `Data Migration` and `data migration`
are one class written twice and become one. Anything that is not a run of words
is not massaged into one -- it is `unclassified`, which is a row somebody can
act on. That normalisation is `work_class_of`, and it lives here now because
this is the module that owns the field; `AssumptionExtractor` calls into it so
that one spelling rule cannot become two.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from praxis.agents.errors import Refusal
from praxis.domain.records import Estimate, Span
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.prompts.library import load

_log = get_logger(__name__)

CLASSIFIER_NAME: Final = "WorkClassifier"
"""Spelled as `praxis.config.models` spells it, so the tier follows the name."""

CLASSIFY_TASK: Final = "classify_work"
"""Also the name of the prompt file this agent reads -- ADR 0014."""

UNCLASSIFIED: Final = "unclassified"
"""What `work_class` says when nothing has established the kind of work.

Re-exported from here rather than defined twice: `praxis.agents.extractor`
introduced the constant in Phase 4 and this module is what finally acts on it,
so both names resolve to one object and a rename cannot leave half the codebase
behind.
"""

MAX_KNOWN_CLASSES: Final = 40
"""How many existing classes are put in front of the model at once.

A cap rather than the whole vocabulary, because the listing is the prompt and a
store with three hundred classes would produce a prompt nobody could read and a
choice nobody could make. Forty is well past the point where a real
organisation's vocabulary has stabilised; a store past it has a fragmentation
problem the prompt cannot fix, and truncating quietly is better than pretending
the model weighed all of them.
"""

_CLASS_RE: Final = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
"""`WorkClass`'s own spelling, so this module cannot drift from the record."""


def work_class_of(stated: str | None) -> str:
    """Normalise a stated class, or say the work was not classified.

    Only spelling is repaired: `Data Migration` and `data migration` are the
    same class written by two models, and letting both through would halve a
    sample `BiasDetective` already refuses to answer below `n = 5`. Anything
    that is not a run of words is not repaired into one -- it is `unclassified`,
    which is a row a person can act on.

    Args:
        stated: What a model said, or `None` if it said nothing.

    Returns:
        The class in the record's own spelling, or `UNCLASSIFIED`.
    """
    if stated is None:
        return UNCLASSIFIED
    kebab = "-".join(stated.lower().split())
    return kebab if _CLASS_RE.fullmatch(kebab) else UNCLASSIFIED


def is_classified(estimate: Estimate) -> bool:
    """Whether this estimate already sits on the axis calibration groups by.

    A property of the record rather than of the run, which is what lets a second
    pass over an unchanged store cost nothing -- the same argument
    `praxis.agents.formalization` makes about a predicate that already parses.
    """
    return estimate.work_class != UNCLASSIFIED


class ClassAnswer(BaseModel):
    """What the model says one estimate's work is.

    `why` is not stored on the record. It is asked for because a model that
    emits only a label can emit it without having read anything, and naming what
    in the passage decides it is the cheapest way to make the answer depend on
    the text. It becomes the audit row's reason, so it is readable afterwards.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    work_class: str | None
    existing: bool = False
    """Whether the model picked a class it was shown or proposed a new one.

    Reported rather than inferred by comparing strings: an agent inventing a new
    class for every estimate is a fragmenting vocabulary, and it is worth being
    a number in the eval table before it is a discovery six phases later.
    """

    why: str | None
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True, slots=True)
class Classification:
    """One estimate moved onto the calibration axis.

    Attributes:
        estimate: The record with `work_class` filled in, not yet written. Its
            `span_id` is untouched -- a classification is a judgement about a
            record, not a new claim about a document -- and so is `created_by`,
            which names the agent that *produced* the record rather than the one
            that last revised it. `AssumptionFormalizer` leaves it alone for the
            same reason, and the audit trail is where "who wrote this version"
            is answered. Overwriting it here would break every question asked of
            the store in the form "which agent's records are these", including
            the one `praxis.agents.estimation` asks to know it has already read
            a document.
        work_class: The class assigned, already normalised.
        proposed: Whether this class was new to the store rather than chosen
            from what it already held.
        reason: What the model said decides it. Becomes the audit row's reason.
        confidence: How sure the model was.
        calls: Model calls made, repairs included.
    """

    estimate: Estimate
    work_class: str
    proposed: bool
    reason: str
    confidence: float
    calls: int = 0

    @property
    def classified(self) -> bool:
        """Whether this actually moved the estimate off `unclassified`."""
        return self.work_class != UNCLASSIFIED


@dataclass(frozen=True, slots=True)
class ClassificationRefusal:
    """An estimate the model said nothing usable about.

    Distinct from a classification that came back `unclassified`: that is an
    answer, and this is the absence of one. The eval table reports them apart
    because the responses differ -- one is a row to look at, the other is a call
    to make again with a better prompt.

    Attributes:
        estimate_id: What was being classified.
        refusal: From the vocabulary the eval harness groups by.
        detail: The evidence against it.
        calls: Model calls made before giving up.
    """

    estimate_id: str
    refusal: Refusal
    detail: str
    calls: int = 0


class WorkClassifier:
    """Puts an estimate on the axis calibration is computed along. Scan tier."""

    name: Final = CLASSIFIER_NAME

    def __init__(self, provider: LLMProvider, *, max_attempts: int = REPAIR_ATTEMPTS) -> None:
        """Wire the agent to a provider it did not choose.

        Args:
            provider: The seam, from `provider_for`. The agent never names an
                implementation -- ADR 0005's first assumption.
            max_attempts: Attempts per call, repairs included.
        """
        self._provider = provider
        self._max_attempts = max_attempts

    def classify(
        self,
        estimate: Estimate,
        evidence: Span,
        known: tuple[str, ...] = (),
        *,
        at: datetime,
    ) -> Classification | ClassificationRefusal:
        """Decide what kind of work one estimate covers.

        Args:
            estimate: The record to classify. Returned revised, not written.
            evidence: The span it was read from, re-read for context. Already
                verified when the estimate was written, and unchanged by this.
            known: Classes already in the store, so the model can converge on a
                shared vocabulary rather than inventing a parallel one.
            at: When this ran. Timezone-aware, invariant 5.

        Returns:
            The estimate with a class, or a refusal naming what went wrong.

        Raises:
            ProviderError: for failures about the run rather than this record.
        """
        answer, calls = self._answer_for(estimate, evidence, known)
        if answer is None:
            return ClassificationRefusal(
                estimate_id=estimate.id,
                refusal=Refusal.NO_USABLE_ANSWER,
                detail="the model refused or never satisfied the schema",
                calls=calls,
            )
        assigned = work_class_of(answer.work_class)
        return Classification(
            estimate=estimate.model_copy(update={"work_class": assigned, "created_at": at}),
            work_class=assigned,
            proposed=assigned != UNCLASSIFIED and assigned not in known,
            reason=_reason_for(assigned, answer),
            confidence=answer.confidence,
            calls=calls,
        )

    def _answer_for(
        self, estimate: Estimate, evidence: Span, known: tuple[str, ...]
    ) -> tuple[ClassAnswer | None, int]:
        """Ask about one estimate, returning `None` if the answer was unusable."""
        prompt = load(CLASSIFY_TASK)
        request = LLMRequest(
            agent=self.name,
            task=CLASSIFY_TASK,
            system=prompt.render(),
            messages=(Message(role=MessageRole.USER, content=_asked(estimate, evidence, known)),),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={"estimate_id": estimate.id, "known_classes": str(len(known))},
        )
        try:
            result = ask_for(self._provider, request, ClassAnswer, max_attempts=self._max_attempts)
        except (MalformedOutputError, ProviderRefusalError) as exc:
            # A bad answer about one estimate, not a broken run.
            _log.warning(
                "classification_unusable",
                agent=self.name,
                estimate_id=estimate.id,
                reason=type(exc).__name__,
            )
            return None, exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return result.value, result.attempts


def _asked(estimate: Estimate, evidence: Span, known: tuple[str, ...]) -> str:
    """What the model is shown: the estimate, its passage, and the vocabulary.

    The existing classes are numbered in the same bracketed shape every offering
    in this pipeline uses. That is not decoration: `praxis.llm.synthesis` draws
    an offline answer from labels the prompt really presented, so a listing in
    the house shape is one the mock can answer plausibly instead of inventing a
    class from nothing.
    """
    vocabulary = (
        "\n".join(f"[{index}] {name}" for index, name in enumerate(known[:MAX_KNOWN_CLASSES]))
        if known
        else "(none yet -- this is the first classified estimate in this store)"
    )
    return (
        f"## The estimate\n\n"
        f"Subject: {estimate.subject}\n"
        f"Owner: {estimate.owner}\n"
        f"Predicted: {estimate.active_quantity} {estimate.unit.value} of hands-on work, "
        f"{estimate.blocked_quantity} blocked\n\n"
        f"## The passage it was read from\n\n{evidence.text}\n\n"
        f"## Classes already in use\n\n{vocabulary}\n"
    )


def _reason_for(assigned: str, answer: ClassAnswer) -> str:
    """The audit row's reason, which is the one field a person will read.

    Never empty: `AuditEvent.reason` is `NonEmptyStr`, so a model that answered
    with a class and no explanation would otherwise fail the write rather than
    the classification -- a store error standing in for a thin answer.
    """
    said = (answer.why or "").strip()
    if assigned == UNCLASSIFIED:
        return said or "the passage does not make the kind of work clear"
    return said or f"classified as {assigned} without a stated reason"
