"""Structured logging emits machine-readable events, not prose."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from praxis.config.settings import LogFormat, Settings
from praxis.obs.logging import bind_run, clear_run, configure_logging, get_logger


@pytest.fixture(autouse=True)
def _reset_context() -> None:
    clear_run()


def _emit(settings: Settings, capsys: pytest.CaptureFixture[str], **fields: object) -> str:
    configure_logging(settings)
    get_logger("test").info("something_happened", **fields)
    return capsys.readouterr().err.strip()


def test_events_are_valid_json_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    line = _emit(Settings(data_dir=tmp_path), capsys, span_id="S-1", confidence=0.42)

    payload = json.loads(line)

    assert payload["event"] == "something_happened"
    assert payload["span_id"] == "S-1"
    assert payload["confidence"] == 0.42


def test_every_event_carries_level_logger_and_timestamp(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = json.loads(_emit(Settings(data_dir=tmp_path), capsys))

    assert payload["level"] == "info"
    assert payload["logger"] == "test"
    assert payload["timestamp"].endswith("Z")


def test_timestamps_are_utc_not_local(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Naive or local timestamps are a correctness bug in this system."""
    payload = json.loads(_emit(Settings(data_dir=tmp_path), capsys))

    assert payload["timestamp"].endswith("Z")


def test_bound_run_context_appears_on_every_subsequent_event(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(data_dir=tmp_path)
    configure_logging(settings)
    bind_run("R-0001", corpus="synthetic-v1")

    logger = get_logger("test")
    logger.info("first")
    logger.info("second")

    lines = [json.loads(line) for line in capsys.readouterr().err.strip().splitlines()]

    assert [entry["run_id"] for entry in lines] == ["R-0001", "R-0001"]
    assert all(entry["corpus"] == "synthetic-v1" for entry in lines)


def test_clear_run_drops_the_context(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(data_dir=tmp_path)
    configure_logging(settings)
    bind_run("R-0002")
    clear_run()

    get_logger("test").info("after_clear")
    payload = json.loads(capsys.readouterr().err.strip())

    assert "run_id" not in payload


def test_console_format_is_human_readable_not_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    line = _emit(Settings(data_dir=tmp_path, log_format=LogFormat.CONSOLE), capsys)

    assert "something_happened" in line
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)


def test_configuring_twice_does_not_duplicate_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(data_dir=tmp_path)
    configure_logging(settings)
    configure_logging(settings)

    get_logger("test").info("once")

    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_log_level_is_honoured(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(data_dir=tmp_path, log_level="WARNING"))

    logger = get_logger("test")
    logger.info("suppressed")
    logger.warning("emitted")

    lines = capsys.readouterr().err.strip().splitlines()

    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "emitted"
