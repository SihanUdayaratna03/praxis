"""The CLI surface, exercised the way a user or CI would invoke it."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from praxis import __version__, cli_eval
from praxis.agents.extraction import ExtractionPipeline
from praxis.agents.results import DocumentExtraction, ExtractionRun
from praxis.cli import app
from praxis.config.settings import get_settings
from praxis.corpus.groundtruth import verify_corpus
from praxis.domain.enums import SourceKind
from praxis.domain.ids import DocumentId
from praxis.domain.records import Document
from praxis.llm.errors import ProviderError
from praxis.store.errors import StoreError
from praxis.store.migrations import latest_version
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _uncached_settings() -> None:
    """Each test resolves settings from its own patched environment."""
    get_settings.cache_clear()


def test_version_prints_the_package_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_bare_invocation_shows_help_rather_than_failing_silently() -> None:
    result = runner.invoke(app, [])

    assert "doctor" in result.output
    assert "config" in result.output


def test_doctor_passes_on_a_default_offline_install() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_doctor_fails_when_configured_to_need_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRAXIS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("PRAXIS_ANTHROPIC_API_KEY", "placeholder")
    get_settings.cache_clear()

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_doctor_answers_a_real_call_rather_than_reading_the_setting() -> None:
    # The offline claim is the phase's central one, and a routing table, a
    # schema walk and a trace write all have to work before it is true. None
    # of that is visible in a configuration value, so doctor makes the call.
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "one structured call answered" in result.output


def test_doctor_reports_what_the_offline_call_cost_in_tokens() -> None:
    # Zero tokens would mean the answer was empty, which is the failure this
    # probe exists to catch and the one a passing exit code would hide.
    result = runner.invoke(app, ["doctor"])

    assert " 0 tokens" not in result.output


def test_doctor_creates_the_runtime_directories() -> None:
    runner.invoke(app, ["doctor"])

    assert get_settings().trace_dir.is_dir()


def test_config_reports_the_provider_and_the_routing_table() -> None:
    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    assert "llm_provider" in result.output
    assert "mock" in result.output
    assert "Model routing" in result.output


def test_config_never_prints_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAXIS_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("PRAXIS_ANTHROPIC_API_KEY", "placeholder")
    get_settings.cache_clear()

    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    assert "placeholder" not in result.output
    assert "set" in result.output


def test_a_broken_env_file_surfaces_as_an_error_not_a_wrong_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting the live provider with no key must not silently fall back."""
    monkeypatch.setenv("PRAXIS_LLM_PROVIDER", "anthropic")
    get_settings.cache_clear()

    result = runner.invoke(app, ["config"])

    assert result.exit_code != 0


# --- the store commands ---------------------------------------------------


def test_init_creates_a_store_at_the_configured_path() -> None:
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert get_settings().db_path.exists()
    assert "created" in result.output


def test_init_reports_the_schema_it_brought_the_store_to() -> None:
    result = runner.invoke(app, ["init"])

    # Against the shipped migration count rather than a literal: the number
    # this asserted was 2 until a migration was added, and a test that has to
    # be edited by every schema change is a test people learn to edit without
    # reading.
    assert "schema" in result.output
    assert f"version {latest_version()}" in result.output


def test_init_can_be_run_twice() -> None:
    # Migrations are forward-only and skip what is applied, so this is the
    # command an owner runs after pulling rather than a one-time step.
    first = runner.invoke(app, ["init"])
    second = runner.invoke(app, ["init"])

    assert first.exit_code == 0
    assert second.exit_code == 0, second.output
    assert "existing" in second.output


def test_store_stats_reports_an_empty_store() -> None:
    runner.invoke(app, ["init"])

    result = runner.invoke(app, ["store", "stats"])

    assert result.exit_code == 0, result.output
    assert "Records" in result.output
    assert "assumes" in result.output
    assert "0 versions written" in result.output


def test_store_stats_lists_a_kind_nobody_has_written_yet() -> None:
    # Zeros are printed rather than omitted: "no estimates" and "estimates are
    # not a thing this store knows about" are different answers.
    runner.invoke(app, ["init"])

    result = runner.invoke(app, ["store", "stats"])

    assert "estimate" in result.output


def test_store_stats_reports_a_missing_store_instead_of_creating_one() -> None:
    result = runner.invoke(app, ["store", "stats"])

    assert result.exit_code == 1
    assert "praxis init" in result.output
    assert not get_settings().db_path.exists()


def test_bare_store_shows_its_subcommands() -> None:
    result = runner.invoke(app, ["store"])

    assert "stats" in result.output


# --- ADR 0010: the sync-root warning --------------------------------------


def test_doctor_warns_when_the_store_sits_under_a_sync_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRAXIS_DATA_DIR", str(tmp_path / "OneDrive" / "praxis"))
    get_settings.cache_clear()

    result = runner.invoke(app, ["doctor"])

    # Warns and still passes. A deliberate override is legitimate, and a tool
    # that refuses to run is a tool that gets worked around.
    assert result.exit_code == 0, result.output
    assert "warning" in result.output
    assert "OneDrive" in result.output


def test_doctor_says_nothing_about_a_safe_location() -> None:
    result = runner.invoke(app, ["doctor"])

    assert "warning" not in result.output


def test_init_warns_before_it_writes_to_a_synced_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PRAXIS_DATA_DIR", str(tmp_path / "Dropbox" / "praxis"))
    get_settings.cache_clear()

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert "warning" in result.output
    assert "PRAXIS_JOURNAL_MODE=delete" in result.output


# --- Phase 3: ingestion and the corpus ------------------------------------


def test_corpus_generate_writes_a_corpus_that_verifies(tmp_path: Path) -> None:
    root = tmp_path / "corpus"

    result = runner.invoke(
        app, ["corpus", "generate", str(root), "--documents", "4", "--revisions", "0"]
    )

    assert result.exit_code == 0, result.output
    assert "OK" in result.output
    assert verify_corpus(root) == ()
    assert "4 distractors" in result.output


def test_corpus_generate_takes_its_seed_from_the_configuration(tmp_path: Path) -> None:
    """So that a corpus regenerated on another machine is the same corpus,
    without anyone having to remember a number."""
    result = runner.invoke(
        app, ["corpus", "generate", str(tmp_path / "c"), "--documents", "2", "--revisions", "0"]
    )

    assert f"seed {get_settings().seed}" in result.output


def test_corpus_generate_reports_a_bad_request_as_a_sentence(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["corpus", "generate", str(tmp_path / "c"), "--documents", "0", "--revisions", "0"]
    )

    assert result.exit_code == 1
    assert "at least one document" in result.output


def test_ingest_reads_a_corpus_into_the_store(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    runner.invoke(app, ["corpus", "generate", str(corpus), "--documents", "3", "--revisions", "0"])
    runner.invoke(app, ["init"])

    result = runner.invoke(app, ["ingest", str(corpus / "documents")])

    assert result.exit_code == 0, result.output
    assert "3 written" in result.output
    assert "0 refused" in result.output
    assert "calls via mock" in result.output


def test_ingest_recognises_a_corpus_it_has_already_read(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    runner.invoke(app, ["corpus", "generate", str(corpus), "--documents", "2", "--revisions", "0"])
    runner.invoke(app, ["init"])
    runner.invoke(app, ["ingest", str(corpus / "documents")])

    result = runner.invoke(app, ["ingest", str(corpus / "documents")])

    assert result.exit_code == 0, result.output
    assert "0 written, 2 already present" in result.output


def test_ingest_exits_non_zero_when_a_source_fails(tmp_path: Path) -> None:
    runner.invoke(app, ["init"])
    broken = tmp_path / "broken.md"
    broken.write_bytes(b"not utf-8: \xff\xfe")

    result = runner.invoke(app, ["ingest", str(broken)])

    assert result.exit_code == 1
    assert "failed" in result.output


def test_ingest_without_a_store_says_to_run_init(tmp_path: Path) -> None:
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nA decision was made.\n", encoding="utf-8")

    result = runner.invoke(app, ["ingest", str(source)])

    assert result.exit_code == 1
    assert "praxis init" in result.output


def test_bare_corpus_shows_its_subcommands() -> None:
    result = runner.invoke(app, ["corpus"])

    assert "generate" in result.output


# --- Phase 4: extraction and the eval harness -----------------------------


def a_corpus(tmp_path: Path, documents: int = 3) -> Path:
    """A generated corpus, through the command a user would type."""
    root = tmp_path / "corpus"
    runner.invoke(
        app, ["corpus", "generate", str(root), "--documents", str(documents), "--revisions", "0"]
    )
    return root


def an_ingested_store(tmp_path: Path, documents: int = 3) -> Path:
    """A store holding a corpus's spans, ready to extract from."""
    corpus = a_corpus(tmp_path, documents)
    runner.invoke(app, ["init"])
    runner.invoke(app, ["ingest", str(corpus / "documents")])
    return corpus


def a_document() -> Document:
    """One document, for a run built by hand rather than by the pipeline."""
    return Document(
        id=DocumentId("DOC-0001"),
        source_uri="adr.md",
        source_kind=SourceKind.MARKDOWN,
        content="# An ADR",
        ingested_at=datetime(2026, 8, 21, 3, 0, tzinfo=UTC),
        created_by="test",
        created_at=datetime(2026, 8, 21, 3, 0, tzinfo=UTC),
    )


def test_extract_runs_all_three_agents_over_the_store(tmp_path: Path) -> None:
    an_ingested_store(tmp_path)

    result = runner.invoke(app, ["extract"])

    assert result.exit_code == 0, result.output
    assert "3 read" in result.output
    assert "candidates" in result.output
    assert "calls via mock" in result.output


def test_extract_exits_zero_although_the_gate_refused_citations(tmp_path: Path) -> None:
    """The difference from `ingest`, and it is deliberate.

    A refused span in ingestion is a bug -- the segmenter answers in block
    numbers. A refused quotation in extraction is the gate working, and offline
    it is almost every claim (ADR 0016). Exiting non-zero would mean this
    command fails on every run without a key, which is every run.
    """
    an_ingested_store(tmp_path)

    result = runner.invoke(app, ["extract"])

    assert result.exit_code == 0, result.output
    assert "refused" in result.output


def test_extract_without_a_store_says_to_run_init(tmp_path: Path) -> None:
    result = runner.invoke(app, ["extract"])

    assert result.exit_code == 1
    assert "praxis init" in result.output


def test_eval_grades_a_corpus_and_prints_every_section(tmp_path: Path) -> None:
    corpus = a_corpus(tmp_path)

    result = runner.invoke(app, ["eval", str(corpus)])

    assert result.exit_code == 0, result.output
    assert "## Extraction quality" in result.output
    assert "## Citation integrity" in result.output
    assert "## Fusion" in result.output
    assert "Documents graded: 3" in result.output


def test_eval_names_the_provider_the_seed_and_the_prompt_versions(tmp_path: Path) -> None:
    corpus = a_corpus(tmp_path)

    result = runner.invoke(app, ["eval", str(corpus)])

    assert "**provider**: mock" in result.output
    assert f"**corpus_seed**: {get_settings().seed}" in result.output
    assert "**scan_for_decisions**: v1" in result.output


def test_eval_prints_the_offline_caveat_beside_the_table(tmp_path: Path) -> None:
    """Because a metrics table outlives the conversation that produced it."""
    result = runner.invoke(app, ["eval", str(a_corpus(tmp_path))])

    assert "measure the pipeline, not a model" in result.output


def test_eval_leaves_the_configured_store_alone(tmp_path: Path) -> None:
    """The corpus is synthetic, and its decisions were never made by anyone."""
    corpus = a_corpus(tmp_path)
    runner.invoke(app, ["init"])

    runner.invoke(app, ["eval", str(corpus)])
    result = runner.invoke(app, ["store", "stats"])

    assert "0 versions written" in result.output


def test_eval_writes_the_numbers_as_data_when_asked(tmp_path: Path) -> None:
    corpus = a_corpus(tmp_path)
    written = tmp_path / "out" / "metrics.json"

    result = runner.invoke(app, ["eval", str(corpus), "--json", str(written)])

    assert result.exit_code == 0, result.output
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["documents"] == 3
    assert payload["provenance"]["provider"] == "mock"
    assert set(payload["kinds"]) == {"decision", "assumption", "estimate"}


def test_eval_writes_the_same_table_it_printed(tmp_path: Path) -> None:
    corpus = a_corpus(tmp_path)
    written = tmp_path / "table.md"

    result = runner.invoke(app, ["eval", str(corpus), "--markdown", str(written)])

    assert written.read_text(encoding="utf-8").strip() in result.output


def test_eval_can_keep_its_scratch_store_for_digging_into(tmp_path: Path) -> None:
    corpus = a_corpus(tmp_path)
    kept = tmp_path / "scratch" / "eval.db"

    result = runner.invoke(app, ["eval", str(corpus), "--keep", str(kept)])

    assert result.exit_code == 0, result.output
    assert kept.is_file()


def test_eval_says_so_when_a_directory_holds_no_answer_key(tmp_path: Path) -> None:
    """And says it on a line short enough to survive a narrow terminal.

    The first version put the remedy on the same line as a temp-directory path,
    and rich folded `praxis corpus generate` in half on CI's 80 columns. A
    suggested command that arrives with a line break through it is not a
    suggestion.
    """
    empty = tmp_path / "not-a-corpus"
    empty.mkdir()

    result = runner.invoke(app, ["eval", str(empty)])

    assert result.exit_code == 1
    assert "praxis corpus generate" in result.output


def test_two_evals_of_one_corpus_report_the_same_numbers(tmp_path: Path) -> None:
    """The property the Phase 10 ablation table rests on, through the CLI."""
    corpus = a_corpus(tmp_path)
    first, second = tmp_path / "a.json", tmp_path / "b.json"

    runner.invoke(app, ["eval", str(corpus), "--json", str(first)])
    runner.invoke(app, ["eval", str(corpus), "--json", str(second)])

    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


def test_extract_reports_a_provider_failure_as_a_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure about the run, not about one document, stops the command.

    The agents already degrade rather than raise on a bad answer about a single
    document. What reaches here is the other kind -- a budget exhausted, a
    provider that cannot answer at all -- and it is a sentence rather than a
    traceback for the same reason every store failure is.
    """
    an_ingested_store(tmp_path, documents=1)
    monkeypatch.setattr(
        ExtractionPipeline,
        "extract_store",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ProviderError("the budget is spent")),
    )

    result = runner.invoke(app, ["extract"])

    assert result.exit_code == 1
    assert "the budget is spent" in result.output


def test_extract_reports_windows_nothing_was_ever_learned_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blind window is not a negative answer, and the report says which it is."""
    an_ingested_store(tmp_path, documents=1)
    blind = DocumentExtraction(document=a_document(), blind_windows=2)
    monkeypatch.setattr(
        ExtractionPipeline,
        "extract_store",
        lambda *_args, **_kwargs: ExtractionRun(documents=(blind,)),
    )

    result = runner.invoke(app, ["extract"])

    assert result.exit_code == 0, result.output
    assert "2 windows never answered about" in result.output


def test_eval_reports_a_failed_run_as_a_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = a_corpus(tmp_path, documents=1)
    monkeypatch.setattr(
        cli_eval,
        "evaluate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ProviderError("no answer to be had")),
    )

    result = runner.invoke(app, ["eval", str(corpus)])

    assert result.exit_code == 1
    assert "no answer to be had" in result.output


def test_eval_closes_its_scratch_store_when_the_migration_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-opened store is closed rather than left to a garbage collector.

    Worth its own test because the connection is opened before the thing that
    can fail, so the cleanup is a `BaseException` handler rather than a
    context manager, and an untested one of those is a leak nobody sees.
    """
    corpus = a_corpus(tmp_path, documents=1)
    monkeypatch.setattr(
        cli_eval,
        "migrate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(StoreError("the schema will not apply")),
    )

    result = runner.invoke(app, ["eval", str(corpus)])

    assert result.exit_code == 1
    assert "the schema will not apply" in result.output


# --- Phase 5: the memory half ---------------------------------------------


def an_extracted_store(tmp_path: Path, documents: int = 3) -> Path:
    """A store with Half A's records in it, ready for the memory commands."""
    corpus = an_ingested_store(tmp_path, documents)
    runner.invoke(app, ["extract"])
    return corpus


def test_formalize_compiles_the_store_s_assumptions(tmp_path: Path) -> None:
    an_extracted_store(tmp_path)

    result = runner.invoke(app, ["formalize"])

    assert result.exit_code == 0, result.output
    assert "compiled" in result.output
    assert "calls via mock" in result.output


def test_formalize_reports_what_it_did_not_pay_for_twice(tmp_path: Path) -> None:
    # The claim the audit-trail check exists for. A second pass over the same
    # store spends nothing, and saying so is how a person knows it did not.
    an_extracted_store(tmp_path)
    runner.invoke(app, ["formalize"])

    result = runner.invoke(app, ["formalize"])

    assert result.exit_code == 0, result.output
    assert "skipped" in result.output


def test_formalize_without_a_store_says_to_run_init(tmp_path: Path) -> None:
    result = runner.invoke(app, ["formalize"])

    assert result.exit_code == 1
    assert "praxis init" in result.output


def test_monitor_reaches_a_verdict_about_every_assumption(tmp_path: Path) -> None:
    an_extracted_store(tmp_path)
    runner.invoke(app, ["formalize"])

    result = runner.invoke(app, ["monitor"])

    assert result.exit_code == 0, result.output
    assert "assumptions" in result.output
    assert "unchecked" in result.output


def test_monitor_names_expired_apart_from_breached(tmp_path: Path) -> None:
    # The distinction this phase is graded on. A monitor that folded the two
    # into one line would be reporting a number nobody can act on.
    an_extracted_store(tmp_path)

    result = runner.invoke(app, ["monitor"])

    assert result.exit_code == 0, result.output
    assert "expired" in result.output
    assert "breached" in result.output


def test_monitor_reads_the_facts_it_is_given(tmp_path: Path) -> None:
    an_extracted_store(tmp_path)
    facts = tmp_path / "facts.json"
    facts.write_text('{"facts": {"index_size_gb": "80"}}', encoding="utf-8")

    result = runner.invoke(app, ["monitor", "--facts", str(facts)])

    assert result.exit_code == 0, result.output


def test_monitor_reports_an_unreadable_facts_file_as_a_sentence(tmp_path: Path) -> None:
    an_extracted_store(tmp_path)

    result = runner.invoke(app, ["monitor", "--facts", str(tmp_path / "absent.json")])

    assert result.exit_code == 1
    assert "praxis monitor" in result.output


def test_contradictions_reports_each_stage_apart(tmp_path: Path) -> None:
    # A pair nobody proposed and a pair a model judged not to conflict are
    # different results, so the output has to be able to say which happened.
    an_extracted_store(tmp_path)
    runner.invoke(app, ["formalize"])

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0, result.output
    assert "proposed" in result.output
    assert "by arithmetic" in result.output


def test_contradictions_writes_no_duplicates_on_a_second_run(tmp_path: Path) -> None:
    an_extracted_store(tmp_path)
    runner.invoke(app, ["contradictions"])

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0, result.output
    assert "written    0 new edges" in result.output


def test_why_refuses_a_question_the_record_does_not_answer(tmp_path: Path) -> None:
    # A refusal is the cheap outcome and a wrong match is the expensive one, so
    # this exits non-zero rather than printing a guess.
    an_extracted_store(tmp_path)

    result = runner.invoke(app, ["why", "why not zeppelins"])

    assert result.exit_code == 1
    assert "praxis why" in result.output


def test_why_without_a_store_says_to_run_init(tmp_path: Path) -> None:
    result = runner.invoke(app, ["why", "why not Postgres"])

    assert result.exit_code == 1
    assert "praxis init" in result.output
