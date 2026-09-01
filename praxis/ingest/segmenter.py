"""The first agent: grouping blocks into spans, without ever seeing an offset.

`SegmenterAgent` is shown a numbered grid of blocks and asked which contiguous
runs of them form one unit of meaning. It answers in **block numbers**. The
byte offsets are then read off the grid by this module and the text is sliced
out of the document by `Span.covering`, so the model has no way to express a
citation of text that is not there. That is ADR 0011, and it is the reason
`VerifierAgent` has never yet had to reject a span this agent produced --
which is not a reason to stop checking.

Three deterministic rules turn an answer into spans, and each one exists
because the failure it handles has to be handled *somewhere*:

- **A group that cannot be honoured is dropped, not repaired.** Out of range,
  reversed, overlapping something already accepted, or wider than
  `MAX_BLOCKS_PER_GROUP`: rejected with a reason, and the reason is carried out
  in the result rather than logged and forgotten. Guessing at what a bad group
  meant would put a model's mistake into the store wearing this module's name.
- **Every block ends up in exactly one span.** Blocks no accepted group claims
  become spans of their own, so the grid's coverage property survives
  segmentation and nothing can be dropped by a model that ignored it.
- **A window that produces nothing usable degrades to the floor.** One span per
  block is paragraph-level segmentation: a worse answer, and a correct one.

Refusal and unrepairable output are treated as a bad answer about *this
document* and degrade the same way. Anything else the seam raises -- a
transport failure, the cost ceiling -- is about the run rather than the
document, and is left to propagate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from praxis.domain.records import Document, Span
from praxis.ingest.blocks import Block, blocks_in
from praxis.llm.errors import MalformedOutputError, ProviderRefusalError
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS, ask_for
from praxis.llm.types import LLMRequest, Message, MessageRole
from praxis.obs.logging import get_logger
from praxis.prompts.library import Prompt, load

_log = get_logger(__name__)

SEGMENTER_NAME: Final = "SegmenterAgent"
"""Spelled as the routing table spells it, so the model tier follows the name."""

DEFAULT_WINDOW_BLOCKS: Final = 40
"""How many blocks are put in front of the model at once.

Groups cannot cross a window, which is a real limit and a cheap one: a unit of
meaning that runs for forty blocks is a section, not a unit. Bounded so that a
long document costs a predictable number of calls and each prompt stays well
inside the scan tier's context -- ADR 0011, assumption 3.
"""

MAX_BLOCKS_PER_GROUP: Final = 12
"""The widest run a single group may claim.

Without this, the cheapest answer a model can give is one group covering
everything, which is a span that cites the document rather than a claim in it.
A citation that wide tells a reader nothing about where to look.
"""

SEGMENT_TASK: Final = "group_blocks"
"""Also the name of the prompt file this agent reads -- ADR 0014."""


def _prompt() -> Prompt:
    """The system prompt for this call, newest version.

    Read per call rather than bound at import, so that a version bump is a file
    landing in `praxis/prompts/texts/` and nothing else. Cheap: the library
    caches its directory walk.
    """
    return load(SEGMENT_TASK)


class BlockGroup(BaseModel):
    """One run of blocks the model says belongs together.

    `label` is not stored anywhere. It is asked for because a model that only
    emits pairs of numbers can emit them without having read anything, and
    naming the unit is the cheapest way to make the answer depend on the text.
    It stays visible in the trace.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    first_block: int
    last_block: int


class SegmentationPlan(BaseModel):
    """Every group the model proposes for one window of blocks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    groups: tuple[BlockGroup, ...] = Field(min_length=1)
    """At least one. An empty plan is not an answer -- a model with nothing to
    say about a window should say every block stands alone, which is a plan."""


@dataclass(frozen=True, slots=True)
class GroupRejection:
    """A group that was not honoured, and why.

    Carried out of the agent rather than logged and dropped: the rate at which
    a model proposes ungroupable runs is one of the few things about model
    quality this pipeline can measure without a labelled corpus.
    """

    first_block: int
    last_block: int
    reason: str


@dataclass(frozen=True, slots=True)
class Segmentation:
    """What one document's segmentation produced, and what it cost.

    Attributes:
        spans: The spans, in document order. Non-overlapping, and together
            covering every block exactly once.
        blocks: The grid they were cut from.
        rejections: Groups that could not be honoured.
        calls: Model calls made, repairs included.
        degraded_windows: Windows that fell back to one span per block because
            the model refused or never produced a usable answer.
    """

    spans: tuple[Span, ...]
    blocks: tuple[Block, ...]
    rejections: tuple[GroupRejection, ...] = ()
    calls: int = 0
    degraded_windows: int = 0

    @property
    def degraded(self) -> bool:
        """Whether any part of this document fell back to the floor."""
        return self.degraded_windows > 0


class SegmenterAgent:
    """Cuts a document into addressable spans, one window of blocks at a time."""

    name: Final = SEGMENTER_NAME

    def __init__(
        self,
        provider: LLMProvider,
        *,
        window_blocks: int = DEFAULT_WINDOW_BLOCKS,
        max_attempts: int = REPAIR_ATTEMPTS,
        floor_only: bool = False,
    ) -> None:
        """Wire the agent to a provider it did not choose.

        Args:
            provider: The seam, from `provider_for`. The agent never names an
                implementation -- ADR 0005's first assumption.
            window_blocks: Blocks per call.
            max_attempts: Attempts per call, repairs included.
            floor_only: Skip the model and take the floor for every window.
                What the ablation's baseline rung runs on. It costs no call, so
                the cost column stays honest about a rung that made none.

        Raises:
            ValueError: if the window is not positive, which would make a
                document silently produce no calls and no groups.
        """
        if window_blocks < 1:
            message = f"a window must hold at least one block, got {window_blocks}"
            raise ValueError(message)
        self._provider = provider
        self._window_blocks = window_blocks
        self._max_attempts = max_attempts
        self._floor_only = floor_only

    def segment(self, document: Document, *, at: datetime) -> Segmentation:
        """Cut a document into spans.

        Args:
            document: The document to segment. Its content is the authority for
                every offset produced.
            at: When this ran, for the records written. Timezone-aware.

        Returns:
            The spans and the evidence of how they were arrived at.

        Raises:
            ProviderError: for failures about the run rather than about this
                document -- transport, credentials, the cost ceiling.
        """
        blocks = blocks_in(document)
        if not blocks:
            return Segmentation(spans=(), blocks=())

        accepted: list[tuple[int, int]] = []
        rejections: list[GroupRejection] = []
        calls = 0
        degraded = 0
        for window in _windows(blocks, self._window_blocks):
            plan, attempts = self._plan_for(document, window)
            calls += attempts
            if plan is None:
                degraded += 1
                continue
            claimed, refused = _honourable(plan, window)
            accepted.extend(claimed)
            rejections.extend(refused)

        return Segmentation(
            spans=_spans_for(document, blocks, accepted, at=at),
            blocks=blocks,
            rejections=tuple(rejections),
            calls=calls,
            degraded_windows=degraded,
        )

    def _plan_for(
        self, document: Document, window: tuple[Block, ...]
    ) -> tuple[SegmentationPlan | None, int]:
        """Ask for one window's groups, returning `None` if the answer was unusable."""
        if self._floor_only:
            return None, 0
        prompt = _prompt()
        request = LLMRequest(
            agent=self.name,
            task=SEGMENT_TASK,
            system=prompt.render(max_blocks=MAX_BLOCKS_PER_GROUP),
            messages=(Message(role=MessageRole.USER, content=_render(window)),),
            prompt_id=prompt.id,
            prompt_sha=prompt.sha256,
            metadata={
                "doc_id": document.id,
                "first_block": str(window[0].index),
                "last_block": str(window[-1].index),
            },
        )
        try:
            result = ask_for(
                self._provider, request, SegmentationPlan, max_attempts=self._max_attempts
            )
        except (MalformedOutputError, ProviderRefusalError) as exc:
            # A bad answer about this document, not a broken run. The floor is
            # a correct segmentation, so the document still gets ingested.
            _log.warning(
                "segmentation_degraded",
                agent=self.name,
                doc_id=document.id,
                first_block=window[0].index,
                reason=type(exc).__name__,
            )
            return None, exc.attempts if isinstance(exc, MalformedOutputError) else 1
        return result.value, result.attempts


def _windows(blocks: tuple[Block, ...], size: int) -> tuple[tuple[Block, ...], ...]:
    """Cut the grid into the runs put in front of the model, in order."""
    return tuple(blocks[start : start + size] for start in range(0, len(blocks), size))


def _render(window: tuple[Block, ...]) -> str:
    """Render one window as the numbered listing the model reads.

    The kind is included because the scanner already knew it and it says
    something the text alone does not -- that two lines are rows of one table,
    that a run of characters is code and not prose.
    """
    return "\n\n".join(f"[{block.index}] {block.kind.value}\n{block.text}" for block in window)


def _honourable(
    plan: SegmentationPlan, window: tuple[Block, ...]
) -> tuple[list[tuple[int, int]], list[GroupRejection]]:
    """Keep the groups that can be honoured exactly, and say why the rest cannot.

    Overlaps are resolved by taking the earliest group first and refusing
    anything that runs into it, because a rule that is arbitrary and stated
    beats one that is arbitrary and emergent: two runs over one document must
    produce the same spans, and "whichever the model listed first" is not
    stable when a model lists them in a different order.
    """
    first, last = window[0].index, window[-1].index
    kept: list[tuple[int, int]] = []
    refused: list[GroupRejection] = []
    for group in sorted(plan.groups, key=lambda g: (g.first_block, g.last_block)):
        reason = _refusal(group, first, last, kept)
        if reason is None:
            kept.append((group.first_block, group.last_block))
        else:
            refused.append(
                GroupRejection(
                    first_block=group.first_block, last_block=group.last_block, reason=reason
                )
            )
    return kept, refused


def _refusal(group: BlockGroup, first: int, last: int, kept: list[tuple[int, int]]) -> str | None:
    """Why a group cannot be honoured, or `None` if it can."""
    if not (first <= group.first_block <= last and first <= group.last_block <= last):
        return f"blocks {first}-{last} were offered"
    if group.last_block < group.first_block:
        return "the run ends before it begins"
    if group.last_block - group.first_block + 1 > MAX_BLOCKS_PER_GROUP:
        return f"a group may cover at most {MAX_BLOCKS_PER_GROUP} blocks"
    if kept and group.first_block <= kept[-1][1]:
        return f"overlaps the group at blocks {kept[-1][0]}-{kept[-1][1]}"
    return None


def _spans_for(
    document: Document,
    blocks: tuple[Block, ...],
    accepted: list[tuple[int, int]],
    *,
    at: datetime,
) -> tuple[Span, ...]:
    """Turn accepted groups and leftover blocks into one span each.

    Walking the grid rather than the groups is what makes the coverage property
    hold: a block no group claimed still becomes a span, so text cannot go
    missing because a model did not mention it.
    """
    ends = dict(accepted)
    spans: list[Span] = []
    index = 0
    while index < len(blocks):
        start = blocks[index]
        # A block's index is its position in the grid, and every accepted group
        # was checked against the window it came from, so this cannot run off
        # the end -- if it ever does, an IndexError here is the right noise.
        finish = blocks[ends.get(start.index, start.index)]
        spans.append(
            Span.covering(
                document,
                start.start_byte,
                finish.end_byte,
                created_by=SEGMENTER_NAME,
                created_at=at,
            )
        )
        index = finish.index + 1
    return tuple(spans)
