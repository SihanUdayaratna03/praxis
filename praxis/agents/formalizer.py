"""AssumptionFormalizer: a sentence a person wrote into an expression a machine can check.

Phase 1 gave `Assumption` a `predicate` and an `expiry_condition` and documented
both as "held as text until the Phase 5 DSL parses it". Phase 4's extractor fills
them with whatever the document said, which is sometimes a real expression and
usually a paraphrase. This is the agent that makes the difference matter: after
it runs, a predicate either parses -- and `AssumptionMonitor` can evaluate it --
or it does not, and the monitor will refuse to breach on it.

Four decisions.

**Nothing is dropped. An assumption that will not compile is written anyway,
marked.** The instinct `DecisionScout` set and the segmenter's floor repeated:
degrade quality, never correctness. A best-effort predicate is stored with its
confidence capped at `UNCHECKABLE_CEILING` and the audit row saying why, because
the monitor cannot breach on a predicate that does not parse and a person can
fix what they can see. Silence cannot be fixed by anyone.

**The stored predicate is the *rendered* form, not the model's spelling.**
`x<=50` and `x <= 50` become one string, so two assumptions about the same
quantity land in the same blocking bucket in `ContradictionDetector` rather than
in two. Normalising is safe because the round trip is property-tested: rendering
cannot change what a predicate claims.

**One retry, and it is shown the parse error.** A model asked for a grammar gets
it wrong in ways it can fix when told, and a single bounded retry is worth far
more than a wider grammar. Two would be a repair loop, and
`praxis.llm.structured` already owns that concept for the *schema*; this is the
narrower question of whether the answer is well formed in a different language.

**Re-running costs nothing.** An assumption whose predicate already parses is
skipped without a model call, and `already_formalized` asks the audit trail
whether this agent has written a version of the record before. Both read state
the store already holds -- no column, no migration, no second bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from praxis.agents.errors import Refusal
from praxis.domain.records import Assumption, Span
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.predicates.ast import render
from praxis.predicates.errors import ExpiryError, PredicateSyntaxError
from praxis.predicates.expiry import parse_expiry, render_expiry
from praxis.predicates.parser import parse
from praxis.prompts.library import Prompt, load

_log = get_logger(__name__)

FORMALIZER_NAME: Final = "AssumptionFormalizer"
"""Spelled as ADR 0006's routing table spells it, so the tier follows the name."""

FORMALIZE_TASK: Final = "formalize_assumption"
"""Also the name of the prompt file this agent reads -- ADR 0014."""

UNCHECKABLE_CEILING: Final = 0.3
"""The most confidence a formalization that will not parse may carry.

A cap rather than a multiplier, because the claim being made is not "somewhat
less certain" but "this is not machine-checkable and is kept so a person can
repair it". The prompt asks for the same number below the same threshold, so an
honest model and this ceiling agree rather than fighting.
"""

FALLBACK_PREDICATE_NOTE: Final = "no predicate was offered"
FALLBACK_EXPIRY_NOTE: Final = "no expiry condition was offered"


class FormalizationAnswer(BaseModel):
    """What one call says about one assumption.

    Every field the answer may omit is nullable rather than absent, which is the
    asymmetry `praxis.llm.structured` documents: the structured dialect requires
    each declared property to be present, so "I have nothing" is a null.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    predicate: str | None
    expiry_condition: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    subject: str | None
    """The one quantity the predicate is mostly about. Not stored -- the
    identifiers are read off the parsed predicate, which cannot disagree with
    it. Asked for because naming it makes the model choose one."""

    note: str | None


@dataclass(frozen=True, slots=True)
class Formalization:
    """One assumption compiled, or the best attempt at compiling it.

    Attributes:
        assumption: The next version of the record, not yet written. The caller
            writes it, so the agent holds no store -- the seam every Phase 4
            agent takes.
        checkable: Whether both the predicate and the expiry condition parse.
            The only thing that decides whether `AssumptionMonitor` can reach a
            verdict, and therefore the number the eval table reports.
        note: Why it is not checkable, or what was lost. Empty when nothing was.
        calls: Model calls made, the retry included.
    """

    assumption: Assumption
    checkable: bool
    note: str
    calls: int

    @property
    def reason(self) -> str:
        """The audit row's reason, which is the field a person actually reads."""
        if self.checkable:
            return f"compiled to a checkable predicate: {self.assumption.predicate}"
        return f"kept as a best attempt, not machine-checkable: {self.note}"


@dataclass(frozen=True, slots=True)
class FormalizationRefusal:
    """One assumption nothing could be said about, and why.

    Distinct from an uncheckable formalization: this is the model refusing or
    never satisfying the schema, so there is no attempt to keep. Reported rather
    than raised, for the reason `praxis.agents.errors` gives -- a run over two
    hundred assumptions that dies on the third has said nothing about the rest.
    """

    assumption_id: str
    refusal: Refusal
    detail: str
    calls: int


def is_checkable(assumption: Assumption) -> bool:
    """Whether this assumption's predicate and expiry both parse.

    The whole of what "formalized" means, and deliberately not a stored flag.
    A flag can disagree with the text beside it; a parse cannot.

    Args:
        assumption: The record as the store holds it.

    Returns:
        True when `AssumptionMonitor` could reach a verdict about it.
    """
    return _parses(assumption.predicate) and _parses_expiry(assumption.expiry_condition)


class AssumptionFormalizer:
    """Compiles one assumption's predicate and expiry condition."""

    name: Final = FORMALIZER_NAME

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_attempts: int = REPAIR_ATTEMPTS,
        retries: int = 1,
    ) -> None:
        """Wire the agent to a provider it did not choose.

        Args:
            provider: The seam, from `provider_for`.
            max_attempts: Attempts per call for the *schema*, repairs included.
            retries: How many times to go back with a parse error. One by
                default: a model told its expression is malformed usually fixes
                it once, and a second round buys little for another call.

        Raises:
            ValueError: if `retries` is negative.
        """
        if retries < 0:
            message = f"a retry count cannot be negative, got {retries}"
            raise ValueError(message)
        self._provider = provider
        self._max_attempts = max_attempts
        self._retries = retries

    def formalize(
        self, assumption: Assumption, evidence: Span, *, at: datetime
    ) -> Formalization | FormalizationRefusal:
        """Compile one assumption, or say why nothing could be compiled.

        Args:
            assumption: The record, as the store holds it.
            evidence: The span it cites, re-read by the caller. Shown to the
                model because the passage often writes the predicate the author
                intended, and copying that beats inferring one.
            at: When this ran. Timezone-aware, invariant 5.

        Returns:
            The next version of the record and whether it is checkable, or a
            refusal when the model produced nothing usable at all.

        Raises:
            ProviderError: for failures about the run rather than this record.
        """
        answer, calls = self._answer(assumption, evidence)
        if answer is None:
            return FormalizationRefusal(
                assumption_id=assumption.id,
                refusal=Refusal.NO_USABLE_ANSWER,
                detail="the model refused or never satisfied the schema",
                calls=calls,
            )
        predicate, predicate_note = _compiled_predicate(answer, assumption)
        expiry, expiry_note = _compiled_expiry(answer, assumption)
        checkable = not predicate_note and not expiry_note
        note = "; ".join(part for part in (predicate_note, expiry_note, answer.note or "") if part)
        return Formalization(
            assumption=assumption.model_copy(
                update={
                    "predicate": predicate,
                    "expiry_condition": expiry,
                    "confidence": _confidence(assumption, answer, checkable=checkable),
                    "created_at": at,
                }
            ),
            checkable=checkable,
            note=note,
            calls=calls,
        )

    def _answer(
        self, assumption: Assumption, evidence: Span
    ) -> tuple[FormalizationAnswer | None, int]:
        """Ask, and ask once more with the parse error if the answer will not parse."""
        prompt = load(FORMALIZE_TASK)
        calls = 0
        complaint = ""
        answer: FormalizationAnswer | None = None
        for _ in range(self._retries + 1):
            attempt, spent = self._ask(prompt, assumption, evidence, complaint)
            calls += spent
            if attempt is None:
                return answer, calls
            answer = attempt
            complaint = _parse_complaint(attempt)
            if not complaint:
                return answer, calls
        return answer, calls

    def _ask(
        self, prompt: Prompt, assumption: Assumption, evidence: Span, complaint: str
    ) -> tuple[FormalizationAnswer | None, int]:
        """One call. `None` means the model said nothing this agent can use."""
        messages = [Message(role=MessageRole.USER, content=_brief(assumption, evidence))]
        if complaint:
            messages.append(Message(role=MessageRole.USER, content=complaint))
        request = LLMRequest(
            agent=self.name,
            task=FORMALIZE_TASK,
            system=prompt.render(),
            messages=tuple(messages),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={"assumption_id": assumption.id, "span_id": evidence.id},
        )
        try:
            result = ask_for(
                self._provider, request, FormalizationAnswer, max_attempts=self._max_attempts
            )
        except (MalformedOutputError, ProviderRefusalError) as exc:
            _log.warning(
                "formalization_failed",
                agent=self.name,
                assumption_id=assumption.id,
                reason=type(exc).__name__,
            )
            return None, exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return result.value, result.attempts


def _brief(assumption: Assumption, evidence: Span) -> str:
    """What the model is shown, and the only place a quotation reaches it.

    The passage is included because the corpus's own documents write the
    predicate the author meant -- an ADR says ``In predicate form:
    `index_size_gb <= 50`.`` Copying that is better evidence than inferring one,
    and it is also what lets the offline provider answer with a predicate that
    really occurs rather than with a sentence (`praxis.llm.synthesis`).
    """
    stored = assumption.predicate.strip() or "(nothing)"
    return (
        f"Assumption: {assumption.statement}\n\n"
        f"Read from this passage:\n{evidence.text}\n\n"
        f"Currently stored as its predicate, and not parsing: {stored}\n"
        f"Currently stored as its expiry condition: "
        f"{assumption.expiry_condition.strip() or '(nothing)'}"
    )


def _parse_complaint(answer: FormalizationAnswer) -> str:
    """What to send back when an answer will not parse, or empty when it will."""
    problems: list[str] = []
    predicate = (answer.predicate or "").strip()
    expiry = (answer.expiry_condition or "").strip()
    try:
        parse(predicate)
    except PredicateSyntaxError as exc:
        problems.append(f"The predicate {predicate!r} does not parse: {exc.problem}.")
    try:
        parse_expiry(expiry)
    except (ExpiryError, PredicateSyntaxError) as exc:
        problems.append(f"The expiry condition {expiry!r} does not parse: {exc}.")
    if not problems:
        return ""
    return " ".join([*problems, "Rewrite both in the grammar above and answer again."])


def _compiled_predicate(answer: FormalizationAnswer, assumption: Assumption) -> tuple[str, str]:
    """The predicate to store, and the reason it is not checkable if it is not.

    A predicate that parses is stored *rendered*, so two spellings of one claim
    become one string. One that does not is stored as the model wrote it, which
    is the best attempt there is -- and it is marked by the fact that it does
    not parse, which is a property nothing can get out of step with.
    """
    offered = (answer.predicate or "").strip()
    if not offered:
        return assumption.predicate, FALLBACK_PREDICATE_NOTE
    try:
        return render(parse(offered)), ""
    except PredicateSyntaxError as exc:
        return offered, f"the predicate does not parse ({exc.problem})"


def _compiled_expiry(answer: FormalizationAnswer, assumption: Assumption) -> tuple[str, str]:
    """The expiry condition to store, and why it is not checkable if it is not."""
    offered = (answer.expiry_condition or "").strip()
    if not offered:
        return assumption.expiry_condition, FALLBACK_EXPIRY_NOTE
    try:
        return render_expiry(parse_expiry(offered)), ""
    except (ExpiryError, PredicateSyntaxError) as exc:
        return offered, f"the expiry condition does not parse ({exc})"


def _confidence(assumption: Assumption, answer: FormalizationAnswer, *, checkable: bool) -> float:
    """How much to believe the compiled record.

    Two probabilities about two different questions -- is this an assumption the
    decision rests on, and is this predicate a faithful rendering of it -- so
    they multiply. An uncheckable result is then capped rather than scaled,
    because the claim is categorical: it cannot be evaluated at all.
    """
    combined = assumption.confidence * answer.confidence
    return min(combined, UNCHECKABLE_CEILING) if not checkable else combined


def _parses(source: str) -> bool:
    """Whether a predicate parses, without raising."""
    try:
        parse(source)
    except PredicateSyntaxError:
        return False
    return True


def _parses_expiry(source: str) -> bool:
    """Whether an expiry condition parses, without raising."""
    try:
        parse_expiry(source)
    except (ExpiryError, PredicateSyntaxError):
        return False
    return True
