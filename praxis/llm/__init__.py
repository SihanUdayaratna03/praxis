"""The only part of Praxis that knows a network exists.

`praxis.store` states the same boundary about `sqlite3`, and for the same
reason: a seam is worth exactly as much as the rule that nothing crosses it.
No module outside this package may import an HTTP client or a model vendor's
SDK, and `tests/test_boundaries.py` asserts it over the source rather than
trusting review.

Inside the package the boundary is narrower still. `praxis.llm.anthropic` is
the single module that imports `anthropic`; `MockProvider` and `ReplayProvider`
reach nothing but the filesystem, which is what lets the whole pipeline, the
test suite, the eval harness and the CLI run with no credentials and no
network. See [ADR 0005](../../docs/adr/0005-offline-first-llm-provider.md).
"""
