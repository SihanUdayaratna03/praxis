"""The ladder, run over the corpus the generator really writes.

The claims worth asserting here are the ones ADR 0035 rests on, and none of
them is a number: that the top rung reproduces an ordinary `praxis eval`, that
the floor rung makes no segmentation call, that each rung gets a store nobody
else wrote to, and that the ladder is cumulative in the order the pipeline runs.

The metric values are not asserted, for the reason `test_harness.py` gives:
offline they are a property of the mock. What is asserted is that the columns
the brief named are present and comparable between rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest
from praxis.config.settings import Settings
from praxis.corpus.generator import Controls, generate_corpus
from praxis.eval.ablation import LADDER, AblationTable, Rung, ablate, in_memory_store, row_for
from praxis.eval.harness import evaluate
from praxis.eval.stages import Stages
from praxis.llm.mock import MockProvider
from praxis.store.repository import Repository
from praxis.store.traces import SqliteTraceSink

AT = datetime(2026, 9, 1, tzinfo=UTC)
SEED = 20260901
DOCUMENTS = 6
CONTROLS = Controls(clean=1, adversarial=1, orphans=1)
REVISIONS = 1


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One small corpus for the module. The ladder runs it once per rung."""
    root = tmp_path_factory.mktemp("ablation-corpus")
    generate_corpus(
        root,
        documents=DOCUMENTS,
        revisions=REVISIONS,
        controls=CONTROLS,
        seed=SEED,
        generated_at=AT,
    )
    return root


def a_provider(repository: Repository, run_id: str) -> MockProvider:
    """The coherently-citing mock, which is what `praxis eval` runs (ADR 0034).

    Traced into the rung's own store, because the cost column is read back out
    of the trace table rather than counted as the calls are made.
    """
    return MockProvider(
        sink=SqliteTraceSink(repository.connection),
        settings=Settings(),
        run_id=run_id,
        cite_coherently=True,
    )


@pytest.fixture(scope="module")
def table(corpus: Path) -> AblationTable:
    """The whole ladder, run once and read by every test below."""
    return ablate(corpus, a_provider, at=AT)


class TestTheLadderItself:
    def test_the_ladder_is_cumulative_in_the_order_the_pipeline_runs(self) -> None:
        """ADR 0035's shape, asserted rather than left to the tuple's spelling.

        Every rung turns on every stage the rung below it had. A ladder that
        turned one off somewhere would print a difference with two causes.
        """
        turned_on = [
            {
                name
                for name in ("estimation", "memory", "calibration", "fusion", "governance")
                if getattr(rung.stages, name)
            }
            for rung in LADDER
        ]
        for below, above in pairwise(turned_on):
            assert below <= above

    def test_the_floor_is_the_only_rung_that_segments_on_the_floor(self) -> None:
        assert [rung.name for rung in LADDER if rung.stages.floor_only] == ["floor"]

    def test_the_top_rung_runs_every_stage(self) -> None:
        assert LADDER[-1].stages == Stages()

    def test_every_rung_is_named_once(self) -> None:
        names = [rung.name for rung in LADDER]
        assert len(names) == len(set(names))


class TestWhatTheTableHolds:
    def test_a_row_lands_for_every_rung_in_ladder_order(self, table: AblationTable) -> None:
        assert [row.rung.name for row in table.rows] == [rung.name for rung in LADDER]

    def test_the_documents_are_the_same_for_every_rung(self, table: AblationTable) -> None:
        """Otherwise no two rows are comparable and no difference means anything.

        The count includes the revision note and the three control documents,
        which is why it is read off the generator rather than spelled here.
        """
        assert table.documents == DOCUMENTS + REVISIONS + CONTROLS.total()

    def test_a_rung_can_be_found_by_name(self, table: AblationTable) -> None:
        assert table.row("floor") is not None
        assert table.row("no such rung") is None

    def test_the_floor_rung_never_pays_the_segmenter(self, table: AblationTable) -> None:
        """ADR 0035 assumption 2. The cost column is only comparable if this holds.

        Asserted on which agents were paid rather than on the amounts: offline
        every amount is zero, so a rung that made a call it should not have
        would still total nothing.
        """
        floor = table.row("floor")
        assert floor is not None
        assert "SegmenterAgent" not in floor.cost_by_agent
        assert "SegmenterAgent" in table.rows[1].cost_by_agent

    def test_the_floor_rung_makes_fewer_calls_than_the_rung_above_it(
        self, table: AblationTable
    ) -> None:
        """The saving is the segmentation calls, which `run.calls` cannot see."""
        floor = table.row("floor")
        assert floor is not None
        assert floor.calls < table.rows[1].calls

    def test_every_row_reports_the_columns_the_brief_named(self, table: AblationTable) -> None:
        for row in table.rows:
            assert isinstance(row.extraction.precision, Decimal)
            assert isinstance(row.extraction.recall, Decimal)
            assert isinstance(row.citation_integrity, Decimal)
            assert isinstance(row.breach_detection.recall, Decimal)
            assert isinstance(row.abstention_precision, Decimal)
            assert isinstance(row.fusion_recall, Decimal)
            assert isinstance(row.cost_per_document, Decimal)

    def test_no_rung_reports_a_recall_over_one(self, table: AblationTable) -> None:
        """The defect the coherent mock exposed, checked on every rung."""
        for row in table.rows:
            assert row.fusion_recall <= 1
            assert row.extraction.recall <= 1
            assert row.extraction.precision <= 1


class TestWhatEachRungAdds:
    def test_the_rungs_below_fusion_write_no_edges_to_recall(self, table: AblationTable) -> None:
        for name in ("floor", "segmenter", "+ estimation", "+ memory", "+ calibration"):
            row = table.row(name)
            assert row is not None
            assert row.fusion_recall == Decimal(0)

    def test_below_governance_every_finding_abstains_for_want_of_a_challenge(
        self, table: AblationTable
    ) -> None:
        """A precision of 1 that is trivial, and has to be readable as trivial.

        The gate is arithmetic (ADR 0032), so it is recomputed on any store.
        Below the governance rung nothing argued the findings, so every one
        fails `never_challenged` and none is emitted. The precision is 1 because
        the abstentions really do fail a rule -- `emitted` is the column that
        says the layer did not run.
        """
        for name in ("floor", "segmenter", "+ estimation", "+ memory", "+ calibration"):
            row = table.row(name)
            assert row is not None
            assert row.emitted == 0

    def test_calibration_reports_no_measurement_on_a_corpus_this_size(
        self, table: AblationTable
    ) -> None:
        """ADR 0035 said this before the table was run, rather than after.

        No group reaches `MINIMUM_SAMPLE` at this many documents, so the MAE
        column is *not measured* rather than an improvement of zero.
        """
        row = table.row("+ calibration")
        assert row is not None
        assert not row.mae.measured


class TestTheTopRungIsAnOrdinaryEval:
    def test_the_top_rung_reproduces_what_praxis_eval_grades(self, corpus: Path) -> None:
        """ADR 0035 assumption 1. A drift here means every rung moved."""
        with in_memory_store() as repository:
            direct = evaluate(
                repository, a_provider(repository, "direct"), corpus, at=AT, run_id="direct"
            )
        rung = Rung("+ governance", "everything", Stages())
        laddered = ablate(corpus, a_provider, at=AT, ladder=(rung,)).rows[0]

        assert laddered == row_for(rung, direct)


class TestEachRungGetsItsOwnStore:
    def test_a_rung_never_reads_what_another_rung_wrote(self, corpus: Path) -> None:
        """ADR 0035 assumption 3, asserted by counting the stores handed out."""
        opened: list[Repository] = []

        @contextmanager
        def counted() -> Iterator[Repository]:
            with in_memory_store() as repository:
                opened.append(repository)
                yield repository

        ablate(corpus, a_provider, at=AT, ladder=LADDER[:2], store=counted)

        assert len(opened) == 2
        assert opened[0] is not opened[1]
