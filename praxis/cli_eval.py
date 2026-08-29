"""The two commands that run Half A: `praxis extract` and `praxis eval`.

Their own module rather than more of `praxis/cli.py`, which is at the size the
style guide calls a file, and because these two are the first commands that
*grade* rather than report. `cli.py` registers them, so the command surface is
still enumerable in one place.

Three decisions, each of which would otherwise be rediscovered by whoever hits
it.

**`extract` exits zero even when citations were refused.** `ingest` does the
opposite, and the difference is not an inconsistency: the segmenter answers in
block numbers and its offsets are read off the grid, so a refused span there is
a bug. An extraction agent quotes, and offline the mock draws its quotation and
its cited ordinal independently -- so almost everything is refused, and that is
the gate working (ADR 0016). A non-zero exit would mean `praxis extract` fails
on every offline run, which is every run without a key.

**`eval` grades into a store of its own, never the configured one.** Ingesting
a synthetic corpus into an owner's store would put twelve invented decisions
into the memory the product exists to keep. `--keep` writes that scratch store
to a path instead, because a number nobody can dig into is a number nobody
believes -- which is the reason `praxis.eval.harness.evaluate` takes a store
rather than opening one.

**The provenance line is not decoration.** A metrics table with no note of the
provider, the corpus seed and the prompt versions that produced it cannot be
compared with the next one, and comparing them is the entire point of writing
it down.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from praxis.agents.extraction import ExtractionPipeline
from praxis.agents.results import ExtractionRun
from praxis.config.settings import Settings, get_settings
from praxis.corpus.groundtruth import GROUND_TRUTH_FILENAME, load_ground_truth
from praxis.eval.harness import evaluate
from praxis.eval.report import as_json, as_markdown
from praxis.llm.errors import ProviderError
from praxis.llm.factory import provider_for
from praxis.llm.trace import new_run_id
from praxis.obs.logging import configure_logging
from praxis.prompts.library import every_prompt
from praxis.store.connection import MEMORY, connect
from praxis.store.errors import StoreError
from praxis.store.migrations import migrate
from praxis.store.repository import Repository, open_repository
from praxis.store.traces import SqliteTraceSink

console = Console()

status = Console(stderr=True)
"""Where `eval` says what it did, rather than what it measured.

Kept off stdout so that `praxis eval corpus > table.md` writes the table and
nothing else. The table is an artefact; "wrote x" is a remark to the person who
typed the command.
"""

GRADED_PROMPTS: tuple[str, ...] = (
    "scan_for_decisions",
    "structure_decision",
    "extract_assumptions",
    "extract_estimates",
    "classify_work",
    "match_outcome",
    "formalize_assumption",
    "judge_contradiction",
    "match_event",
)
"""Every prompt a graded run reads, in the order the agents run.

Named here rather than derived from every prompt that ships, so that a prompt
nothing in this run used cannot appear in the provenance line as though it had
produced these numbers -- `answer_why_not` is shipped and `praxis eval` never
reads it.

It grew in Phase 6 and the reason is worth stating: this was
`EXTRACTION_PROMPTS`, and its own docstring warned against letting a Half B
version appear on a Half A table. `praxis eval` now runs both halves and the
three memory passes, so the table it prints carries Formalization, Monitoring,
Contradictions and Estimation sections -- and a provenance line naming only the
three extraction prompts leaves two thirds of the numbers unattributed, which is
the failure the line exists to prevent rather than the one it was guarding
against.
"""


def extract() -> None:
    """Read decisions, assumptions and estimates out of the store's documents.

    Runs all three Half A agents over every document the store already holds
    spans for: the scout marks passages, the structurer turns each into a
    decision, and the extractor reads the assumptions it rests on -- writing an
    estimate and its `estimated_as` edge wherever an assumption turns out to be
    a quantified bet.

    A document that already holds decisions is recognised and skipped, so this
    is safe to run again after ingesting more sources.

    Refused citations are reported and do not fail the run: offline they are
    the norm and they are the citation gate doing its job.
    """
    settings = get_settings()
    configure_logging(settings)

    # One run id for the traces and for the audit rows, so a number in the
    # store and the call that produced it can be joined afterwards. Two would
    # be two runs that happened to be the same command.
    run_id = new_run_id()

    try:
        with configured_store(settings) as repository:
            provider = provider_for(
                settings, sink=SqliteTraceSink(repository.connection), run_id=run_id
            )
            run = ExtractionPipeline(repository, provider).extract_store(run_id=run_id)
    except ProviderError as exc:
        console.print(f"[bold red]praxis extract: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    _report_extraction(run, settings)


def eval_corpus(
    corpus: Annotated[
        Path,
        typer.Argument(help="The corpus root -- the directory holding documents/ and its key."),
    ],
    json_path: Annotated[
        Path | None,
        typer.Option("--json", help="Write the numbers as data, for the ablation table."),
    ] = None,
    markdown_path: Annotated[
        Path | None,
        typer.Option("--markdown", help="Write the table a phase report carries."),
    ] = None,
    keep: Annotated[
        Path | None,
        typer.Option(help="Keep the scratch store at this path so a number can be dug into."),
    ] = None,
) -> None:
    """Ingest a corpus, extract from it, and grade the result against its key.

    The whole of Half A measured end to end: precision, recall and field
    accuracy per kind, how honest the citations were, and the recall over the
    `estimated_as` edges the corpus labels -- the one number that is a claim
    about the thesis rather than about extraction.

    Nothing is written to the configured store. The corpus is synthetic and its
    decisions were never made by anyone.
    """
    settings = get_settings()
    configure_logging(settings)

    if not (corpus / GROUND_TRUTH_FILENAME).is_file():
        # Two lines rather than one, because the first carries a path and rich
        # wraps at the console width -- which put a line break through the
        # middle of the command being suggested on an 80-column terminal. The
        # remedy is the half that has to survive being read.
        console.print(f"[bold red]praxis eval: no {GROUND_TRUTH_FILENAME} in {corpus}[/bold red]")
        console.print("Generate one with 'praxis corpus generate'.")
        raise typer.Exit(code=1)

    run_id = new_run_id()

    try:
        with _scratch(settings, keep) as repository:
            provider = provider_for(
                settings, sink=SqliteTraceSink(repository.connection), run_id=run_id
            )
            result = evaluate(repository, provider, corpus, at=datetime.now(UTC), run_id=run_id)
    except (ProviderError, StoreError, OSError, ValueError) as exc:
        console.print(f"[bold red]praxis eval: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc

    provenance = provenance_of(settings, corpus)
    table = as_markdown(result, provenance=provenance, offline=settings.is_offline)
    # Soft-wrapped and unmarkedup, so what reaches the terminal is the same
    # markdown that reaches `--markdown`. Rich would otherwise fold a table row
    # at the console width and restyle anything that looked like its own markup,
    # and a row folded in half is no longer a row anyone can paste into a report.
    console.print(table, soft_wrap=True, markup=False, highlight=False)
    _write_if_asked(json_path, as_json(result, provenance=provenance))
    _write_if_asked(markdown_path, table)
    if keep is not None:
        status.print(f"scratch store kept at {keep}")


def provenance_of(settings: Settings, corpus: Path) -> dict[str, str]:
    """What produced a set of numbers, in the form the report renders.

    The provider, the corpus's own seed -- read off the key rather than off the
    configuration, because a corpus generated with one seed and graded on a
    machine set to another would otherwise be labelled with the wrong one --
    and the version of every prompt the three agents read.
    """
    latest = {prompt.name: prompt.version for prompt in every_prompt()}
    return {
        "provider": settings.llm_provider.value,
        "corpus_seed": str(load_ground_truth(corpus).seed),
        **{name: f"v{latest[name]}" for name in GRADED_PROMPTS if name in latest},
    }


@contextmanager
def configured_store(settings: Settings) -> Iterator[Repository]:
    """The owner's store, reported as a sentence when it cannot be opened.

    Public because `praxis.cli_monitor`'s four commands all open the same store
    the same way. A second copy would be a second place for "what does a
    missing store print" to be answered, and the two would drift.
    """
    try:
        repository = open_repository(settings, create=False)
    except StoreError as exc:
        console.print(f"[bold red]praxis: {exc}[/bold red]")
        raise typer.Exit(code=1) from exc
    try:
        yield repository
    finally:
        repository.close()


@contextmanager
def _scratch(settings: Settings, keep: Path | None) -> Iterator[Repository]:
    """A store of this run's own, in memory unless a path was asked for."""
    if keep is not None:
        keep.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(
        MEMORY if keep is None else keep, journal_mode=settings.journal_mode, create=True
    )
    try:
        migrate(connection)
        repository = Repository(connection)
    except BaseException:
        connection.close()
        raise
    try:
        yield repository
    finally:
        repository.close()


def _write_if_asked(path: Path | None, text: str) -> None:
    """Write a rendering out, creating the directory it lands in."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    status.print(f"wrote {path}")


def _report_extraction(run: ExtractionRun, settings: Settings) -> None:
    """Print what an extraction run wrote and what the gate stopped."""
    recognised = sum(1 for result in run.documents if result.already_extracted)
    console.print("[bold green]praxis extract: OK[/bold green]")
    console.print(f"  documents  {len(run.documents) - recognised} read, {recognised} already done")
    console.print(f"  decisions  {run.decisions} written from {_candidates(run)} candidates")
    console.print(f"  assumption {run.assumptions} written, {run.estimates} of them estimates")
    console.print(f"  model      {run.calls} calls via {settings.llm_provider.value}")
    if run.blind_windows:
        console.print(f"  [yellow]blind      {run.blind_windows} windows never answered about")
    if run.refused:
        console.print(f"  [yellow]refused    {len(run.refused)} claims cited something unusable")
        for refusal, count in sorted(_by_refusal(run).items(), key=lambda pair: -pair[1]):
            console.print(f"               {count} {refusal}")


def _candidates(run: ExtractionRun) -> int:
    """Passages the scout marked, before any was structured."""
    return sum(result.candidates for result in run.documents)


def _by_refusal(run: ExtractionRun) -> dict[str, int]:
    """How many claims each defect cost, keyed by the refusal's own value."""
    counts: dict[str, int] = {}
    for entry in run.refused:
        counts[entry.refusal.value] = counts.get(entry.refusal.value, 0) + 1
    return counts
