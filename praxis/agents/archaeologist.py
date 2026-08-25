"""ArchaeologistAgent: answering "why not X" years later, out of the record alone.

**The specification for this agent was not written down, and this docstring is
where the judgement call is recorded so it can be reviewed rather than assumed
correct.** Three fragments exist in the repository and nothing else:

- `praxis.domain.records.RejectedOption`: "Recording the reason is what lets
  `ArchaeologistAgent` answer *why not X* years later without anyone having to
  remember."
- `praxis.corpus.topics` and `praxis.corpus.generator`: one subject appears as an
  ADR, as the meeting it came out of, and as the status update that closed it,
  "which is what makes `ContradictionDetector` and `ArchaeologistAgent`
  gradeable later."
- ADR 0006 routes it to `reason`.

From those, the contract taken here is **retrieval and grounding, not
generation**, which is what "Half A -- provenance" means and what the rest of
this half already does. See ADR 0020.

The property that follows is the whole design: **the model selects, the store
speaks.** A model is asked which recorded decision the question is about and
which of its rejected options the question names -- both by reference into a
listing it was shown -- and the answer is then assembled from the stored
`RejectedOption.reason`, the decision's own fields, and the current status of
the assumptions it rests on. Nothing the model writes reaches the answer.

An invented option is therefore not something this agent can express: the named
option is checked against the decision's own `rejected` tuple before anything is
assembled, which is `praxis.agents.citation`'s argument applied to a different
kind of citation. And an answer nobody recorded is refused rather than composed,
because someone asking "why not X" and being told about a decision that never
considered X will believe it -- it arrives with a date and a name on it.

Retrieval is the store's FTS5 index, so the question never causes a scan. The
question is reduced to quoted terms first: `Repository.search` passes its
argument to FTS5 unchanged and owns none of its syntax, so a question mark or a
hyphen would arrive as a match expression and come back as a `StoreQueryError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from praxis.agents.errors import Refusal
from praxis.domain.enums import AssumptionStatus, RecordKind
from praxis.domain.ids import SpanId
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, RejectedOption
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.prompts.library import load
from praxis.store.repository import Repository

_log = get_logger(__name__)

ARCHAEOLOGIST_NAME: Final = "ArchaeologistAgent"
"""Spelled as ADR 0006's routing table spells it."""

WHY_NOT_TASK: Final = "answer_why_not"
"""The prompt file this agent reads -- ADR 0014."""

DEFAULT_CANDIDATES: Final = 8
"""Decisions offered per question.

Under ADR 0015's ceiling on how many entries an ordinal can address
unambiguously, and well under it because each entry here is a whole decision
with its rejected options rather than a passage.
"""

SEARCH_LIMIT: Final = 40
"""Hits to pull before narrowing to decisions.

Larger than `DEFAULT_CANDIDATES` because a hit may be an assumption or a span,
and the decision that answers the question is often reached through one of
those rather than matched directly.
"""

MIN_TERM_LENGTH: Final = 3
"""Below this a term matches most of the corpus and narrows nothing."""

_TERM: Final = re.compile(r"[a-z][a-z0-9]*")
"""Terms, after case folding.

Every term is quoted before it reaches FTS5. `Repository.search` documents that
it passes its argument through unchanged, so an unquoted `why not?` is a match
expression with a syntax error in it rather than a search.
"""


class ExcavationAnswer(BaseModel):
    """What one call says: two references and nothing this agent will repeat."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_ordinal: int | None
    rejected_option: str | None
    answered: bool
    confidence: float = Field(ge=0.0, le=1.0)
    note: str | None


@dataclass(frozen=True, slots=True)
class RestingAssumption:
    """An assumption a decision rested on, and what has become of it.

    The part that makes an archaeologist worth having in a system that also
    monitors: "we chose this because we assumed that, and that assumption
    broke in June" is a different sentence from "we chose this".
    """

    assumption: Assumption
    status: AssumptionStatus

    @property
    def still_holds(self) -> bool:
        """Whether the last verdict was that it holds."""
        return self.status is AssumptionStatus.HOLDING


@dataclass(frozen=True, slots=True)
class Excavation:
    """An answer assembled out of stored records.

    Attributes:
        question: What was asked, unchanged.
        answer: The answer, composed from stored fields only. No text a model
            produced appears in it.
        decision: The decision the question was about.
        rejected: The option the question named, as that decision recorded it.
        resting_on: The assumptions the decision rested on, with their current
            status.
        citations: Every span behind the answer -- the decision's, and one per
            assumption. Invariant 6 reaching a query rather than an extraction.
        confidence: The model's confidence in the *selection*. The answer's own
            content is the record's and carries no confidence.
        note: Why this decision answers the question, in the model's words.
            Kept apart from `answer` on purpose, and never spliced into it.
        calls: Model calls made.
    """

    question: str
    answer: str
    decision: Decision
    rejected: RejectedOption
    resting_on: tuple[RestingAssumption, ...]
    citations: tuple[SpanId, ...]
    confidence: float
    note: str
    calls: int


@dataclass(frozen=True, slots=True)
class Unanswered:
    """A question the record does not answer, and why.

    Returned rather than raised, and never accompanied by a guess. A refusal
    here is the cheap outcome; a wrong match is the expensive one.
    """

    question: str
    refusal: Refusal
    detail: str
    calls: int


class ArchaeologistAgent:
    """Answers "why not X" from what a store holds, or says it cannot."""

    name: Final = ARCHAEOLOGIST_NAME

    def __init__(
        self,
        provider: LLMProvider,
        *,
        candidates: int = DEFAULT_CANDIDATES,
        max_attempts: int = REPAIR_ATTEMPTS,
    ) -> None:
        """Wire the agent to a provider it did not choose.

        Args:
            provider: The seam, from `provider_for`.
            candidates: Decisions offered per question.
            max_attempts: Attempts per call, repairs included.

        Raises:
            ValueError: if fewer than one candidate would be offered.
        """
        if candidates < 1:
            message = f"a question needs at least one candidate, got {candidates}"
            raise ValueError(message)
        self._provider = provider
        self._candidates = candidates
        self._max_attempts = max_attempts

    def ask(self, question: str, repository: Repository) -> Excavation | Unanswered:
        """Answer one question from the record, or refuse it.

        The repository is taken directly rather than through a caller that
        holds it, unlike every other agent in this package. For those the store
        is where the answer goes; here it *is* the question's subject matter,
        and an agent that could not read it would have nothing to be.

        Args:
            question: A "why not X" question, in a person's own words.
            repository: The store to search and walk.

        Returns:
            An answer assembled from records, or a refusal naming what was
            missing.

        Raises:
            ProviderError: for failures about the run rather than this question.
            StoreError: if the search itself fails.
        """
        offered = self._decisions_for(question, repository)
        if not offered:
            return Unanswered(
                question=question,
                refusal=Refusal.EMPTY_ANSWER,
                detail="nothing in the store matched the question",
                calls=0,
            )
        answer, calls = self._answer(question, offered)
        if answer is None:
            return Unanswered(
                question=question,
                refusal=Refusal.NO_USABLE_ANSWER,
                detail="the model refused or never satisfied the schema",
                calls=calls,
            )
        return self._assemble(question, answer, offered, repository, calls=calls)

    def _decisions_for(self, question: str, repository: Repository) -> tuple[Decision, ...]:
        """The decisions a question might be about, best match first."""
        terms = _terms_of(question)
        if not terms:
            return ()
        seen: dict[str, Decision] = {}
        for hit in repository.search(terms, limit=SEARCH_LIMIT):
            found = self._decision_behind(hit.record_id, hit.kind, repository)
            if found is not None and found.id not in seen:
                seen[found.id] = found
            if len(seen) >= self._candidates:
                break
        return tuple(seen.values())

    def _decision_behind(
        self, record_id: str, kind: RecordKind, repository: Repository
    ) -> Decision | None:
        """The decision a hit points at, directly or through one edge.

        An assumption is a hit worth following: a question about a subject often
        matches the assumption's wording rather than the decision's, and the
        `assumes` edge is exactly the record of which decision rested on it.
        """
        if kind is RecordKind.DECISION:
            return repository.get(Decision, record_id)
        if kind is not RecordKind.ASSUMPTION:
            return None
        for link in repository.links_to(record_id, types=(LinkType.ASSUMES,)):
            found = repository.get(Decision, link.source_id)
            if found is not None:
                return found
        return None

    def _answer(
        self, question: str, offered: tuple[Decision, ...]
    ) -> tuple[ExcavationAnswer | None, int]:
        """Ask which decision and which rejected option the question names."""
        prompt = load(WHY_NOT_TASK)
        request = LLMRequest(
            agent=self.name,
            task=WHY_NOT_TASK,
            system=prompt.render(),
            messages=(Message(role=MessageRole.USER, content=_listing(question, offered)),),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={"candidates": str(len(offered))},
        )
        try:
            result = ask_for(
                self._provider, request, ExcavationAnswer, max_attempts=self._max_attempts
            )
        except (MalformedOutputError, ProviderRefusalError) as exc:
            _log.warning("excavation_failed", agent=self.name, reason=type(exc).__name__)
            return None, exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return result.value, result.attempts

    def _assemble(
        self,
        question: str,
        answer: ExcavationAnswer,
        offered: tuple[Decision, ...],
        repository: Repository,
        *,
        calls: int,
    ) -> Excavation | Unanswered:
        """Build the answer out of records, refusing anything not in one."""
        if not answer.answered:
            return Unanswered(
                question=question,
                refusal=Refusal.EMPTY_ANSWER,
                detail="no recorded decision rejected the option this question names",
                calls=calls,
            )
        ordinal = answer.decision_ordinal
        if ordinal is None or not 0 <= ordinal < len(offered):
            # The same defect `UNOFFERED_SPAN` names one layer down: a reference
            # to something the agent was not shown. Grouped with it in the eval
            # table because the response to it is the same.
            return Unanswered(
                question=question,
                refusal=Refusal.UNOFFERED_SPAN,
                detail=f"the answer cites candidate {ordinal}, which was not offered",
                calls=calls,
            )
        decision = offered[ordinal]
        rejected = _recorded_option(decision, answer.rejected_option)
        if rejected is None:
            return Unanswered(
                question=question,
                refusal=Refusal.FABRICATED_QUOTE,
                detail=(
                    f"{decision.id} never rejected {answer.rejected_option!r}; "
                    f"it rejected {', '.join(option.option for option in decision.rejected)}"
                ),
                calls=calls,
            )
        resting = _resting_on(decision, repository)
        return Excavation(
            question=question,
            answer=_composed(decision, rejected, resting),
            decision=decision,
            rejected=rejected,
            resting_on=resting,
            citations=(decision.span_id, *(found.assumption.span_id for found in resting)),
            confidence=answer.confidence,
            note=(answer.note or "").strip(),
            calls=calls,
        )


def _resting_on(decision: Decision, repository: Repository) -> tuple[RestingAssumption, ...]:
    """The assumptions a decision rested on, with what has become of each."""
    found: list[RestingAssumption] = []
    for link in repository.links_from(decision.id, types=(LinkType.ASSUMES,)):
        assumption = repository.get(Assumption, link.target_id)
        if assumption is not None:
            found.append(RestingAssumption(assumption=assumption, status=assumption.status))
    return tuple(found)


def _composed(
    decision: Decision, rejected: RejectedOption, resting: tuple[RestingAssumption, ...]
) -> str:
    """The answer, assembled from stored fields and nothing else.

    Every clause here is a field of a record. That is the point of the agent: a
    sentence that reads well and is not in the record would be worse than no
    answer, because it arrives with a date and a name on it and will be
    believed.
    """
    lines = [
        f"{rejected.option} was rejected: {rejected.reason}.",
        f"{decision.decision_maker} chose {decision.chosen} instead, "
        f"on {decision.decided_at.date().isoformat()} ({decision.id}).",
    ]
    if resting:
        lines.append("It rested on:")
        lines += [f"- {found.assumption.statement} [{found.status.value}]" for found in resting]
    broken = [found for found in resting if found.status is AssumptionStatus.BREACHED]
    if broken:
        lines.append(
            f"{len(broken)} of those has since been breached, so this decision is worth re-reading."
            if len(broken) == 1
            else f"{len(broken)} of those have since been breached, so this decision "
            f"is worth re-reading."
        )
    return "\n".join(lines)


def _recorded_option(decision: Decision, named: str | None) -> RejectedOption | None:
    """The rejected option a name refers to, or `None` if the decision has none.

    Matched after case folding and whitespace collapsing and no looser than
    that. A fuzzy match would let "Postgres" answer for a decision that rejected
    "Postgres full-text search" -- which is usually right and is exactly the
    kind of usually-right this agent must not do, because the reason attached to
    it would be presented as a quotation.
    """
    if not named:
        return None
    wanted = _folded(named)
    return next((option for option in decision.rejected if _folded(option.option) == wanted), None)


def _folded(text: str) -> str:
    """A rejected option's name, reduced to what two spellings of it share."""
    return " ".join(text.split()).casefold()


def _terms_of(question: str) -> str:
    """A question as an FTS5 match expression that cannot be malformed.

    Every term is quoted and joined with `OR`. `Repository.search` passes its
    argument to FTS5 unchanged and says so, so a bare question -- with its
    question mark, its hyphens and its apostrophes -- is a match expression with
    a syntax error in it rather than a search.
    """
    terms = [term for term in _TERM.findall(question.casefold()) if len(term) >= MIN_TERM_LENGTH]
    return " OR ".join(f'"{term}"' for term in dict.fromkeys(terms))


def _listing(question: str, offered: tuple[Decision, ...]) -> str:
    """The question and the numbered decisions one call is shown."""
    lines = [f"Question: {question}", ""]
    for ordinal, decision in enumerate(offered):
        lines.append(f"[{ordinal}] {decision.title}")
        lines.append(f"    chose: {decision.chosen}")
        lines.append("    rejected:")
        lines += [f"      - {option.option}" for option in decision.rejected]
        lines.append("")
    return "\n".join(lines)
