"""What `praxis serve` does before it binds anything.

`uvicorn.run` blocks until interrupted, so it is patched out. What is worth
asserting is everything up to that call: a missing store is a sentence rather
than a traceback, the store is opened read-only and cross-thread, and binding
something other than loopback says so out loud.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
from praxis.cli import app
from praxis.config.settings import Settings, get_settings
from praxis.store.errors import StoreError
from praxis.store.repository import Repository, open_repository
from typer.testing import CliRunner

from tests.store.conftest import build_world

runner = CliRunner()


@pytest.fixture
def settings(tmp_path) -> Settings:
    """A store of this test's own, so nothing reads the developer's real one."""
    get_settings.cache_clear()
    return Settings(data_dir=tmp_path)


@pytest.fixture
def seeded(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[Repository]:
    """A store holding the fixture graph."""
    monkeypatch.setenv("PRAXIS_DATA_DIR", str(settings.data_dir))
    get_settings.cache_clear()
    repository = open_repository(get_settings())
    build_world(repository)
    repository.close()
    yield repository
    get_settings.cache_clear()


@pytest.fixture
def bound(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture what would have been served instead of serving it.

    The snapshots are taken inside the fake because `serve` closes the store
    when serving returns -- which is right, and means a test that looked
    afterwards would be reading a closed handle.
    """
    calls: list[dict[str, object]] = []

    def fake_run(app_object, **kwargs):
        repository = app_object.state.repository
        answers: list[int] = []
        worker = threading.Thread(target=lambda: answers.append(repository.stats().versions))
        worker.start()
        worker.join()
        calls.append(
            {
                "app": app_object,
                "decisions": repository.stats().records["decision"],
                "versions_from_another_thread": answers,
                **kwargs,
            }
        )

    monkeypatch.setattr("praxis.web.server.uvicorn.run", fake_run)
    return calls


class TestServing:
    def test_it_reports_the_url_the_store_and_the_provider(self, seeded, bound):
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 0
        assert "http://127.0.0.1:8000" in result.stdout
        assert "mock" in result.stdout

    def test_it_binds_loopback_by_default(self, seeded, bound):
        runner.invoke(app, ["serve"])
        assert bound[0]["host"] == "127.0.0.1"
        assert bound[0]["port"] == 8000

    def test_a_host_and_port_are_passed_through(self, seeded, bound):
        runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "9001"])  # noqa: S104
        assert bound[0]["host"] == "0.0.0.0"  # noqa: S104
        assert bound[0]["port"] == 9001

    def test_binding_beyond_loopback_warns(self, seeded, bound):
        result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])  # noqa: S104
        assert "warning" in result.stdout
        assert "serves this store to the network" in result.stdout

    def test_loopback_does_not_warn(self, seeded, bound):
        assert "serves this store to the network" not in runner.invoke(app, ["serve"]).stdout


class TestAMissingStore:
    def test_it_is_a_sentence_rather_than_a_traceback(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch, bound
    ):
        monkeypatch.setenv("PRAXIS_DATA_DIR", str(settings.data_dir / "nothing-here"))
        get_settings.cache_clear()
        result = runner.invoke(app, ["serve"])
        assert result.exit_code == 1
        assert "praxis:" in result.stdout
        assert bound == [], "nothing should be served over a store that is not there"
        get_settings.cache_clear()

    def test_it_does_not_create_the_store_it_could_not_find(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch, bound
    ):
        missing = settings.data_dir / "nothing-here"
        monkeypatch.setenv("PRAXIS_DATA_DIR", str(missing))
        get_settings.cache_clear()
        runner.invoke(app, ["serve"])
        assert not (missing / "praxis.db").exists()
        get_settings.cache_clear()


class TestTheStoreItServes:
    def test_the_app_reads_the_store_the_settings_name(self, seeded, bound):
        runner.invoke(app, ["serve"])
        assert bound[0]["decisions"] == 2

    def test_the_handle_is_usable_from_another_thread(self, seeded, bound):
        """Otherwise every request would fail on the threadpool boundary."""
        runner.invoke(app, ["serve"])
        answers = bound[0]["versions_from_another_thread"]
        assert answers, "the worker thread raised instead of answering"
        assert answers[0] > 0

    def test_the_store_is_closed_once_serving_returns(self, seeded, bound):
        """A handle left open would hold a lock on the file after Ctrl+C."""
        runner.invoke(app, ["serve"])
        with pytest.raises(StoreError):
            bound[0]["app"].state.repository.stats()

    def test_the_app_carries_a_lock_for_the_routes_to_hold(self, seeded, bound):
        runner.invoke(app, ["serve"])
        assert bound[0]["app"].state.lock is not None
