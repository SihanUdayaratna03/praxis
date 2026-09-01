"""A client over the same fixture graph the store tests use.

The store is in memory and the app is built around it directly, so nothing here
binds a socket. `create_app` takes an open repository for exactly this reason.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from praxis.store.connection import MEMORY, connect
from praxis.store.migrations import migrate
from praxis.store.repository import Repository
from praxis.web.server import create_app

from tests.store.conftest import World, build_world


@pytest.fixture
def repository() -> Iterator[Repository]:
    """An empty, migrated store."""
    # cross_thread because TestClient runs the app in a worker thread, the
    # same reason `praxis serve` opens the real store that way.
    connection = connect(MEMORY, cross_thread=True)
    migrate(connection)
    store = Repository(connection)
    yield store
    store.close()


@pytest.fixture
def graph(repository: Repository) -> World:
    """The fixture graph, written into the store the app will read."""
    return build_world(repository)


@pytest.fixture
def client(repository: Repository) -> Iterator[TestClient]:
    """A client over an empty store."""
    with TestClient(create_app(repository, provider="mock")) as test_client:
        yield test_client


@pytest.fixture
def populated(repository: Repository, graph: World) -> Iterator[TestClient]:
    """A client over the fixture graph."""
    with TestClient(create_app(repository, provider="mock")) as test_client:
        yield test_client
