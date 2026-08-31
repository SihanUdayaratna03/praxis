"""Holding the segmenter to a partition of its own document.

The properties come first because they are the reason the design is shaped
this way: whatever a model answers, the spans that come out cover every block
exactly once, resolve against the document, and are the same on the second run.
A test suite that only checked the happy path would be checking the mock.

The mechanism tests use a provider that returns plans chosen by the test. It is
a real `LLMProvider` subclass rather than a stand-in for one, so every
assertion below still goes through routing, the ledger and the trace sink --
the Phase 2 lesson about test doubles built out of the real object.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from praxis.config.models import MOCK_MODEL_ID, ModelRole, ModelSpec, role_for_agent
from praxis.config.settings import ProviderName, Settings
from praxis.domain.ids import DocumentId, span_id_for
from praxis.domain.records import Document
from praxis.domain.spans import verify_span
from praxis.ingest.adapters import MARKDOWN_ADAPTER, document_from
from praxis.ingest.blocks import blocks_in
from praxis.ingest.segmenter import (
    MAX_BLOCKS_PER_GROUP,
    SEGMENTER_NAME,
    BlockGroup,
    SegmentationPlan,
    SegmenterAgent,
)
from praxis.llm.errors import ProviderRefusalError, ProviderUnavailableError
from praxis.llm.mock import MockProvider
from praxis.llm.provider import LLMProvider
from praxis.llm.structured import REPAIR_ATTEMPTS
from praxis.llm.trace import MemoryTraceSink
from praxis.llm.types import LLMRequest, LLMResponse, StopReason, TokenUsage

AT = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)

BODY = """\
# Store location

We chose SQLite over Postgres for the graph store.
Nobody will run a server for a single-writer tool.

- The migration is assumed to take at most six weeks.
- Rollback is a rebuild, so the blast radius is small.

| phase | hours |
| ----- | ----- |
| 2 | 5.6 |

> Quoted from the review.

```python
answer = 42
```
"""


def document(text: str = BODY, doc_id: str = "DOC-0001") -> Document:
    source = MARKDOWN_ADAPTER.normalise(text.encode("utf-8"), source_uri="d.md")
    return document_from(source, doc_id=DocumentId(doc_id), ingested_at=AT)


class PlannedProvider(LLMProvider):
    """Answers with plans the test chose, through the whole seam.

    Reports itself as the mock because the provider column is not what any test
    here is about; everything else -- routing, pricing, the trace row -- is the
    real path.
    """

    name = ProviderName.MOCK
    bills = False

    def __init__(self, *plans: SegmentationPlan | Exception, **kwargs) -> None:
        super().__init__(**kwargs)
        self.plans = list(plans)
        self.requests: list[LLMRequest] = []

    def _invoke(self, request: LLMRequest, spec: ModelSpec) -> LLMResponse:
        self.requests.append(request)
        planned = self.plans.pop(0) if self.plans else SegmentationPlan(groups=())
        if isinstance(planned, Exception):
            raise planned
        return LLMResponse(
            text=planned.model_dump_json(),
            model_id=MOCK_MODEL_ID,
            usage=TokenUsage(input_tokens=10, output_tokens=10),
            stop_reason=StopReason.END_TURN,
        )


def plan(*runs: tuple[int, int]) -> SegmentationPlan:
    return SegmentationPlan(
        groups=tuple(
            BlockGroup(label=f"group {first}-{last}", first_block=first, last_block=last)
            for first, last in runs
        )
    )


def planned(settings: Settings, *plans: SegmentationPlan | Exception) -> PlannedProvider:
    return PlannedProvider(*plans, sink=MemoryTraceSink(), settings=settings)


def block_ranges(document_: Document) -> list[tuple[int, int]]:
    return [(block.start_byte, block.end_byte) for block in blocks_in(document_)]


# -- the properties ----------------------------------------------------------

DOCUMENTS = (
    st.lists(
        st.sampled_from(
            [
                "# A heading",
                "Ordinary prose about a decision.",
                "- a bullet with an assumption in it",
                "| a | table |",
                "> a quotation",
                "```\ncode\n```",
                "",
                "prose with unicode é中\U0001f600 in it",
            ]
        ),
        min_size=1,
        max_size=25,
    )
    .map("\n\n".join)
    .filter(lambda body: body.strip() != "")
)


@given(DOCUMENTS)
@hypothesis_settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40)
def test_the_spans_cover_every_block_exactly_once(settings, body):
    """Segmentation inherits the grid's coverage: a model that ignores a block
    cannot make its text disappear."""
    doc = document(body)
    result = SegmenterAgent(MockProvider(sink=MemoryTraceSink(), settings=settings)).segment(
        doc, at=AT
    )

    for block in result.blocks:
        holders = [
            span
            for span in result.spans
            if span.start_byte <= block.start_byte and block.end_byte <= span.end_byte
        ]
        assert len(holders) == 1, f"block {block.index} is in {len(holders)} spans"


@given(DOCUMENTS)
@hypothesis_settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40)
def test_every_span_resolves_against_the_document_it_cites(settings, body):
    """The invariant the whole phase exists to establish, asserted with the
    same function VerifierAgent uses."""
    doc = document(body)
    result = SegmenterAgent(MockProvider(sink=MemoryTraceSink(), settings=settings)).segment(
        doc, at=AT
    )

    assert result.spans
    for span in result.spans:
        assert verify_span(span, doc).ok
        assert span.id == span_id_for(doc.id, span.start_byte, span.end_byte)


@given(DOCUMENTS)
@hypothesis_settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=40)
def test_spans_are_ordered_and_never_overlap(settings, body):
    result = SegmenterAgent(MockProvider(sink=MemoryTraceSink(), settings=settings)).segment(
        document(body), at=AT
    )

    previous = 0
    for span in result.spans:
        assert span.start_byte >= previous
        previous = span.end_byte
    assert len(result.spans) <= len(result.blocks)


@given(DOCUMENTS)
@hypothesis_settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=25)
def test_two_runs_over_one_document_produce_the_same_spans(settings, body):
    """Phase 4's determinism requirement, which starts here: a pipeline whose
    spans moved between runs would make every eval number incomparable."""
    doc = document(body)
    first = SegmenterAgent(MockProvider(sink=MemoryTraceSink(), settings=settings)).segment(
        doc, at=AT
    )
    second = SegmenterAgent(MockProvider(sink=MemoryTraceSink(), settings=settings)).segment(
        doc, at=AT
    )

    assert first.spans == second.spans


# -- honouring a good answer -------------------------------------------------


def test_a_group_becomes_one_span_covering_exactly_those_blocks(settings):
    doc = document()
    ranges = block_ranges(doc)
    provider = planned(settings, plan((0, 1)))

    result = SegmenterAgent(provider).segment(doc, at=AT)

    assert result.spans[0].start_byte == ranges[0][0]
    assert result.spans[0].end_byte == ranges[1][1]
    assert result.spans[0].text == doc.content[ranges[0][0] : ranges[1][1]]
    assert result.rejections == ()


def test_blocks_no_group_claimed_become_spans_of_their_own(settings):
    doc = document()
    blocks = blocks_in(doc)
    result = SegmenterAgent(planned(settings, plan((0, 1)))).segment(doc, at=AT)

    assert len(result.spans) == len(blocks) - 1
    assert result.spans[-1].text == blocks[-1].text


def test_an_answer_with_no_groups_at_all_is_the_floor(settings):
    """A plan with an empty list fails its own schema, so this is the case
    where a model proposes nothing usable rather than nothing at all."""
    doc = document()
    result = SegmenterAgent(planned(settings, plan((99, 99)))).segment(doc, at=AT)

    assert len(result.spans) == len(result.blocks)
    assert result.degraded is False  # the model answered; the answer was refused


# -- refusing a bad one ------------------------------------------------------


def test_a_group_outside_the_blocks_offered_is_refused(settings):
    doc = document()
    result = SegmenterAgent(planned(settings, plan((0, 500)))).segment(doc, at=AT)

    assert [rejection.reason for rejection in result.rejections] == [
        f"blocks 0-{len(result.blocks) - 1} were offered"
    ]
    assert len(result.spans) == len(result.blocks)


def test_a_reversed_group_is_refused(settings):
    result = SegmenterAgent(planned(settings, plan((3, 1)))).segment(document(), at=AT)

    assert result.rejections[0].reason == "the run ends before it begins"


def test_a_group_wider_than_the_limit_is_refused(settings):
    body = "\n\n".join(f"paragraph {index}" for index in range(MAX_BLOCKS_PER_GROUP + 4))
    doc = document(body)

    result = SegmenterAgent(planned(settings, plan((0, MAX_BLOCKS_PER_GROUP)))).segment(doc, at=AT)

    assert result.rejections[0].reason == (
        f"a group may cover at most {MAX_BLOCKS_PER_GROUP} blocks"
    )


def test_the_widest_allowed_group_is_honoured(settings):
    """The boundary the test above sits one past, because an off-by-one in a
    limit is invisible from one side."""
    body = "\n\n".join(f"paragraph {index}" for index in range(MAX_BLOCKS_PER_GROUP + 4))

    result = SegmenterAgent(planned(settings, plan((0, MAX_BLOCKS_PER_GROUP - 1)))).segment(
        document(body), at=AT
    )

    assert result.rejections == ()


def test_the_earlier_of_two_overlapping_groups_wins(settings):
    """Stated rather than emergent, so the answer does not depend on the order
    a model happened to list them in."""
    doc = document()
    listed_late_first = SegmenterAgent(planned(settings, plan((2, 4), (0, 3)))).segment(doc, at=AT)
    listed_early_first = SegmenterAgent(planned(settings, plan((0, 3), (2, 4)))).segment(doc, at=AT)

    assert listed_late_first.spans == listed_early_first.spans
    assert listed_late_first.rejections[0].reason == "overlaps the group at blocks 0-3"


def test_a_group_touching_the_previous_one_at_its_edge_overlaps_it(settings):
    result = SegmenterAgent(planned(settings, plan((0, 2), (2, 3)))).segment(document(), at=AT)

    assert result.rejections[0].reason == "overlaps the group at blocks 0-2"


# -- windows -----------------------------------------------------------------


def test_a_long_document_is_asked_about_one_window_at_a_time(settings):
    doc = document("\n\n".join(f"paragraph {index}" for index in range(7)))
    provider = planned(settings, plan((0, 1)), plan((3, 4)), plan((6, 6)))

    result = SegmenterAgent(provider, window_blocks=3).segment(doc, at=AT)

    assert len(provider.requests) == 3
    assert result.calls == 3
    assert result.rejections == ()


def test_a_group_may_not_cross_a_window_boundary(settings):
    """A real limit, and a cheap one: a unit of meaning that runs past a whole
    window is a section rather than a unit."""
    doc = document("\n\n".join(f"paragraph {index}" for index in range(6)))
    provider = planned(settings, plan((0, 4)), plan((3, 4)))

    result = SegmenterAgent(provider, window_blocks=3).segment(doc, at=AT)

    assert result.rejections[0].reason == "blocks 0-2 were offered"


def test_the_prompt_shows_the_numbers_the_answer_must_use(settings):
    doc = document()
    provider = planned(settings, plan((0, 1)))

    SegmenterAgent(provider).segment(doc, at=AT)

    prompt = provider.requests[0].messages[0].content
    assert "[0] heading" in prompt
    assert "[2] list_item" in prompt


def test_the_call_carries_the_document_it_is_about(settings):
    provider = planned(settings, plan((0, 1)))

    SegmenterAgent(provider).segment(document(), at=AT)

    assert provider.requests[0].metadata["doc_id"] == "DOC-0001"


# -- degrading ---------------------------------------------------------------


def test_a_refusal_degrades_this_document_rather_than_stopping_the_run(settings):
    doc = document()
    result = SegmenterAgent(planned(settings, ProviderRefusalError(SEGMENTER_NAME))).segment(
        doc, at=AT
    )

    assert result.degraded is True
    assert result.degraded_windows == 1
    assert len(result.spans) == len(result.blocks)
    assert all(verify_span(span, doc).ok for span in result.spans)


def test_output_that_never_parses_degrades_the_same_way(settings):
    doc = document()
    sink = MemoryTraceSink()
    misbehaving = MockProvider(sink=sink, settings=settings, malformed_share=1.0)

    result = SegmenterAgent(misbehaving).segment(doc, at=AT)

    assert result.degraded is True
    assert result.calls == REPAIR_ATTEMPTS
    assert len(result.spans) == len(result.blocks)


def test_a_failure_about_the_run_is_not_swallowed(settings):
    """Transport, credentials, the cost ceiling. Degrading a document because
    the budget ran out would produce a corpus that quietly stopped being
    segmented."""
    provider = planned(settings, ProviderUnavailableError("no network"))

    with pytest.raises(ProviderUnavailableError):
        SegmenterAgent(provider).segment(document(), at=AT)


def test_only_the_window_that_failed_degrades(settings):
    doc = document("\n\n".join(f"paragraph {index}" for index in range(6)))
    provider = planned(settings, ProviderRefusalError(SEGMENTER_NAME), plan((3, 4)))

    result = SegmenterAgent(provider, window_blocks=3).segment(doc, at=AT)

    assert result.degraded_windows == 1
    assert len(result.spans) == 5


# -- the edges ---------------------------------------------------------------


def test_a_document_with_nothing_addressable_produces_no_spans_and_no_calls(settings):
    """The adapter refuses such a source, so this document is built directly.
    The agent still has to survive one arriving from anywhere else."""
    provider = planned(settings)
    blank = Document(
        id=DocumentId("DOC-0002"),
        source_uri="blank.md",
        source_kind=MARKDOWN_ADAPTER.source_kind,
        content="   \n\n  \n",
        ingested_at=AT,
        created_at=AT,
        created_by="test",
    )

    result = SegmenterAgent(provider).segment(blank, at=AT)

    assert result.spans == ()
    assert provider.requests == []


def test_a_window_of_no_blocks_is_refused_when_the_agent_is_built(settings):
    """Rather than silently producing a document with no calls and no groups."""
    with pytest.raises(ValueError, match="at least one block"):
        SegmenterAgent(planned(settings), window_blocks=0)


def test_the_agent_asks_for_a_role_and_never_for_a_model():
    """Invariant 2, from the agent's side: the name is the whole of what it
    supplies, and the routing table decides the rest."""
    assert role_for_agent(SEGMENTER_NAME) is ModelRole.SCAN


def test_every_span_records_the_agent_that_cut_it(settings):
    result = SegmenterAgent(planned(settings, plan((0, 1)))).segment(document(), at=AT)

    assert all(span.created_by == SEGMENTER_NAME for span in result.spans)
    assert all(span.created_at == AT for span in result.spans)


# -- the floor on purpose -----------------------------------------------------


class TestFloorOnly:
    """`floor_only=True` is the ablation's baseline rung: one span per block."""

    def test_it_makes_no_call_at_all(self, settings):
        """Not a wasted call whose answer is thrown away.

        The rung's cost is reported per document, so a baseline that spent the
        scan tier and then ignored it would misprice the layer above it.
        """
        provider = planned(settings, plan((0, 1)))
        result = SegmenterAgent(provider, floor_only=True).segment(document(), at=AT)

        assert provider.requests == []
        assert result.calls == 0

    def test_every_block_becomes_its_own_span(self, settings):
        doc = document()
        result = SegmenterAgent(planned(settings), floor_only=True).segment(doc, at=AT)

        assert len(result.spans) == len(result.blocks)
        assert [(span.start_byte, span.end_byte) for span in result.spans] == block_ranges(doc)
        assert all(verify_span(span, doc).ok for span in result.spans)

    def test_it_reports_every_window_as_degraded(self, settings):
        """The floor is a worse answer honestly labelled, not a silent one."""
        result = SegmenterAgent(planned(settings), floor_only=True).segment(document(), at=AT)

        assert result.degraded is True
        assert result.degraded_windows == 1

    def test_a_document_with_no_blocks_still_produces_nothing(self, settings):
        """The no-blocks path returns before the flag is ever consulted."""
        blank = Document(
            id=DocumentId("DOC-0003"),
            source_uri="blank.md",
            source_kind=MARKDOWN_ADAPTER.source_kind,
            content="   \n\n  \n",
            ingested_at=AT,
            created_at=AT,
            created_by="test",
        )

        result = SegmenterAgent(planned(settings), floor_only=True).segment(blank, at=AT)

        assert result.spans == ()
