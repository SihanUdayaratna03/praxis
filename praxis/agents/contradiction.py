"""ContradictionDetector: two claims that cannot both hold, and the edge saying so.

`LinkType.CONTRADICTS` has existed since Phase 1 and nothing has ever written
one. This writes them, and the shape of the agent is the interesting part rather
than the writing: **three stages, and only the last one is a model.**

```
  records ──▶ blocking ──────▶ arithmetic ──────▶ a model
              deterministic    deterministic      the residue only
              O(n·k), capped   disjoint ranges    one call per batch
```

1. `praxis.agents.blocking` proposes the pairs that could possibly be about the
   same thing. Everything else is never compared at all, which is what keeps the
   cost off `n²`.
2. `praxis.predicates.intervals` settles every pair whose conflict is a matter
   of arithmetic. `index_size_gb <= 50` against `index_size_gb > 50` permits
   disjoint sets of numbers, and a model has no privileged access to that fact.
   Those edges carry confidence 1.0 and cost nothing.
3. What is left is genuinely a judgement -- two claims in prose about the same
   subject -- and only that reaches the `reason` tier, in one call per batch.

`Contradiction.settled_by` records which stage decided, so the eval table can
report the two recalls apart. That matters more than it sounds: a low
contradiction recall means something different if blocking never proposed the
pair, if the arithmetic could not read the predicates, or if the model was asked
and said no, and those are three different things to go and fix.

**A false contradiction is the expensive direction to be wrong in.** It puts a
decision in front of a person who finds nothing wrong with it, and the next
finding they see gets less attention. So every stage here refuses rather than
guesses: blocking will not propose on one shared word, the arithmetic returns
nothing when it cannot read a predicate, and a judgement below the confidence
threshold is dropped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from praxis.agents.blocking import BlockingIndex, Candidate, subject_keys, word_keys
from praxis.agents.results import Contradiction, DetectionResult, Settlement
from praxis.domain.ids import SpanId
from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Decision, Link
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.predicates.ast import identifiers_in
from praxis.predicates.errors import PredicateSyntaxError
from praxis.predicates.intervals import conflict, constraints_of
from praxis.predicates.parser import parse
from praxis.prompts.library import load

_log = get_logger(__name__)

DETECTOR_NAME: Final = "ContradictionDetector"
"""Spelled as ADR 0006's routing table spells it."""

JUDGE_TASK: Final = "judge_contradiction"
"""The prompt file this agent reads -- ADR 0014."""

DEFAULT_MAX_PAIRS: Final = 60
"""How many candidate pairs one detection may consider.

The cost ceiling, and deliberately a count of *pairs* rather than of calls: the
arithmetic settles some of them for free, so the number of model calls is
smaller than this and varies with how well the corpus was formalized.
"""

DEFAULT_BATCH: Final = 12
"""Pairs per judging call.

The same reasoning ADR 0015 applies to a span offering: beyond about a dozen
entries an ordinal stops being something a model addresses reliably, and a
mis-addressed judgement asserts a contradiction between the wrong two records.
"""

DEFAULT_MIN_CONFIDENCE: Final = 0.6
"""Below this a claimed contradiction is dropped rather than written.

Asymmetric on purpose. A missed contradiction is a gap; a false one is a person
sent to re-read a decision that was fine, and the cost of that lands on every
finding they see afterwards.
"""

CERTAIN: Final = 1.0
"""The confidence an arithmetic contradiction carries. Not a judgement: the two
predicates permit disjoint sets of numbers, and two runs agree about that."""


class Judgement(BaseModel):
    """What one call says about one pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair_ordinal: int | None
    contradicts: bool
    rationale: str | None
    confidence: float = Field(ge=0.0, le=1.0)


class Judgements(BaseModel):
    """Everything one judging call says."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    judgements: tuple[Judgement, ...]


@dataclass(frozen=True, slots=True)
class _Claim:
    """One record reduced to what contradiction detection needs.

    A view rather than the record, for the reason `praxis.eval.matching.Claim`
    is one: assumptions and decisions are different models and this cares about
    three things -- what it says, what quantity it constrains, and where it was
    read from.
    """

    record_id: str
    statement: str
    predicate: str
    span_id: SpanId

    @property
    def keys(self) -> frozenset[str]:
        """The blocking keys this record carries."""
        return subject_keys(self._identifiers()) | word_keys(self.statement, self.predicate)

    def _identifiers(self) -> frozenset[str]:
        """The quantities its predicate constrains, or none if it does not parse."""
        if not self.predicate:
            return frozenset()
        try:
            return identifiers_in(parse(self.predicate))
        except PredicateSyntaxError:
            return frozenset()


def claim_of(record: Assumption | Decision) -> _Claim:
    """Reduce a record to the view detection works over.

    A decision has no predicate, which is not a special case: it simply carries
    no subject key and is blocked on prose alone.

    Args:
        record: An assumption or a decision.

    Returns:
        The view.
    """
    if isinstance(record, Assumption):
        return _Claim(
            record_id=record.id,
            statement=record.statement,
            predicate=record.predicate,
            span_id=record.span_id,
        )
    return _Claim(
        record_id=record.id,
        statement=f"{record.title}: {record.chosen}",
        predicate="",
        span_id=record.span_id,
    )


class ContradictionDetector:
    """Finds pairs of records that cannot both hold."""

    name: Final = DETECTOR_NAME

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        max_pairs: int = DEFAULT_MAX_PAIRS,
        batch: int = DEFAULT_BATCH,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        max_attempts: int = REPAIR_ATTEMPTS,
    ) -> None:
        """Wire the agent to a provider it did not choose, or to none at all.

        Args:
            provider: The seam, from `provider_for`. **Optional**: without one
                the arithmetic stage still runs and still writes every
                contradiction it can prove. Degrade quality, never correctness.
            max_pairs: The most candidate pairs to consider.
            batch: Pairs per judging call.
            min_confidence: Below this a claimed contradiction is dropped.
            max_attempts: Attempts per call, repairs included.

        Raises:
            ValueError: if any bound is outside what it means.
        """
        if max_pairs < 0 or batch < 1:
            message = f"max_pairs is a count and batch is at least one, got {max_pairs}, {batch}"
            raise ValueError(message)
        if not 0.0 <= min_confidence <= 1.0:
            message = f"a confidence threshold is between 0 and 1, got {min_confidence}"
            raise ValueError(message)
        self._provider = provider
        self._max_pairs = max_pairs
        self._batch = batch
        self._min_confidence = min_confidence
        self._max_attempts = max_attempts

    def detect(
        self,
        records: Sequence[Assumption | Decision],
        *,
        at: datetime,
        known: Sequence[tuple[str, str]] = (),
    ) -> DetectionResult:
        """Find every pair of these records that cannot both hold.

        Args:
            records: Assumptions and decisions, in any order.
            at: When this ran. Timezone-aware, invariant 5.
            known: Pairs already linked, in either direction, so a re-run does
                not pay to re-decide what the store already asserts.

        Returns:
            The edges to write, and what each stage did.

        Raises:
            ProviderError: for failures about the run rather than one batch.
        """
        claims = {claim.record_id: claim for claim in (claim_of(record) for record in records)}
        index = BlockingIndex()
        for claim in claims.values():
            index.add(claim.record_id, claim.keys)
        blocking = index.propose(limit=self._max_pairs, excluding=known)

        settled: list[Contradiction] = []
        residue: list[Candidate] = []
        for candidate in blocking.candidates:
            proved = _proved(claims[candidate.left], claims[candidate.right], at=at)
            if proved is not None:
                settled.append(proved)
                continue
            residue.append(candidate)
        judged, calls = self._judged(residue, claims, at=at)
        return DetectionResult(
            contradictions=(*settled, *judged),
            blocking=blocking,
            judged=len(residue),
            calls=calls,
        )

    def _judged(
        self,
        residue: Sequence[Candidate],
        claims: dict[str, _Claim],
        *,
        at: datetime,
    ) -> tuple[list[Contradiction], int]:
        """Ask about the pairs arithmetic could not settle, in batches."""
        if self._provider is None or not residue:
            return [], 0
        found: list[Contradiction] = []
        calls = 0
        for start in range(0, len(residue), self._batch):
            batch = residue[start : start + self._batch]
            answer, spent = self._ask(batch, claims)
            calls += spent
            if answer is not None:
                found.extend(self._accepted(answer, batch, claims, at=at))
        return found, calls

    def _ask(
        self, batch: Sequence[Candidate], claims: dict[str, _Claim]
    ) -> tuple[Judgements | None, int]:
        """One judging call. `None` means the answer was unusable."""
        if self._provider is None:  # pragma: no cover -- `_judged` has checked
            return None, 0
        prompt = load(JUDGE_TASK)
        request = LLMRequest(
            agent=self.name,
            task=JUDGE_TASK,
            system=prompt.render(),
            messages=(Message(role=MessageRole.USER, content=_listing(batch, claims)),),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={"pairs": str(len(batch))},
        )
        try:
            result = ask_for(self._provider, request, Judgements, max_attempts=self._max_attempts)
        except (MalformedOutputError, ProviderRefusalError) as exc:
            # A bad answer about this batch is not a broken run. The pairs in it
            # are simply not judged, which is reported as a gap rather than as a
            # set of contradictions nobody asserted.
            _log.warning(
                "contradiction_judging_failed",
                agent=self.name,
                pairs=len(batch),
                reason=type(exc).__name__,
            )
            return None, exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return result.value, result.attempts

    def _accepted(
        self,
        answer: Judgements,
        batch: Sequence[Candidate],
        claims: dict[str, _Claim],
        *,
        at: datetime,
    ) -> list[Contradiction]:
        """The judgements this agent is willing to write as edges."""
        found: list[Contradiction] = []
        seen: set[int] = set()
        for judgement in answer.judgements:
            ordinal = judgement.pair_ordinal
            if not judgement.contradicts or judgement.confidence < self._min_confidence:
                continue
            if ordinal is None or not 0 <= ordinal < len(batch) or ordinal in seen:
                continue
            seen.add(ordinal)
            candidate = batch[ordinal]
            found.append(
                _edge(
                    claims[candidate.left],
                    claims[candidate.right],
                    rationale=(judgement.rationale or "").strip() or _bare_rationale(candidate),
                    confidence=judgement.confidence,
                    settled_by=Settlement.MODEL,
                    at=at,
                )
            )
        return found


def _proved(left: _Claim, right: _Claim, *, at: datetime) -> Contradiction | None:
    """A contradiction two predicates make certain, or `None` if they do not.

    `None` is not "they agree" -- it is "arithmetic has nothing to say", and the
    pair goes on to the tier that can have an opinion.
    """
    rationale = _first_conflict(left.predicate, right.predicate)
    if rationale is None:
        return None
    return _edge(
        left,
        right,
        rationale=rationale,
        confidence=CERTAIN,
        settled_by=Settlement.ARITHMETIC,
        at=at,
    )


def _first_conflict(left: str, right: str) -> str | None:
    """Why two predicates cannot both hold, or `None`."""
    for source in (left, right):
        if not source:
            return None
    try:
        first, second = constraints_of(parse(left)), constraints_of(parse(right))
    except PredicateSyntaxError:
        return None
    return next(
        (found for one in first for other in second if (found := conflict(one, other)) is not None),
        None,
    )


def _edge(  # noqa: PLR0913 -- an edge and its provenance, none of it derivable
    left: _Claim,
    right: _Claim,
    *,
    rationale: str,
    confidence: float,
    settled_by: Settlement,
    at: datetime,
) -> Contradiction:
    """Build the `contradicts` edge between two claims.

    The span cited is the *left* record's, and that is a choice rather than an
    accident: a symmetric edge is stored once, so citing both would mean
    inventing a second field, and citing whichever record happened to be
    processed first would make the edge depend on iteration order. Ordering the
    pair by id makes the citation a function of the pair.
    """
    return Contradiction(
        link=Link.between(
            LinkType.CONTRADICTS,
            left.record_id,
            right.record_id,
            rationale=rationale,
            confidence=confidence,
            created_by=DETECTOR_NAME,
            created_at=at,
            span_id=left.span_id,
        ),
        settled_by=settled_by,
        rationale=rationale,
    )


def _bare_rationale(candidate: Candidate) -> str:
    """What an edge says when a model asserted one and explained nothing.

    Kept rather than dropped -- the pair was still judged to conflict, and the
    subject it was proposed on is a real thing to say. A `Link.rationale` may
    not be empty, so the alternative is losing the finding over a missing
    sentence.
    """
    subjects = ", ".join(candidate.subjects)
    about = f"about {subjects}" if subjects else "about the same subject"
    return f"judged to conflict {about}, with no further explanation given"


def _listing(batch: Sequence[Candidate], claims: dict[str, _Claim]) -> str:
    """The numbered pairs one judging call is shown."""
    lines: list[str] = []
    for ordinal, candidate in enumerate(batch):
        left, right = claims[candidate.left], claims[candidate.right]
        lines.append(f"[{ordinal}]")
        lines.append(f"  A. {_describe(left)}")
        lines.append(f"  B. {_describe(right)}")
        lines.append("")
    return "\n".join(lines)


def _describe(claim: _Claim) -> str:
    """One claim as the judging call sees it."""
    if claim.predicate:
        return f"{claim.statement} (in predicate form: `{claim.predicate}`)"
    return claim.statement
