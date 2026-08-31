"""One function that turns configuration into a provider.

This is the file [ADR 0005](../../docs/adr/0005-offline-first-llm-provider.md)'s
second assumption is about: *switching to live models requires editing `.env`
and nothing else*, written as the predicate
`files_changed_to_enable_live_models == 1`. That is only true if exactly one
place reads `PRAXIS_LLM_PROVIDER`, so every caller from Phase 3 onward asks
here and no agent ever names an implementation.

Two decisions worth stating:

- **The live provider is imported inside the branch that needs it.** Invariant
  1 is about credentials, but an offline run that still required the vendor
  package to be installed would keep that promise only halfway. `anthropic` is
  an optional extra, and a mock run never touches the import.
- **Recording is chosen here rather than inside the provider.** Whether a run
  writes fixtures is configuration, and the class that can write them is the
  live one only -- so the branch belongs where the configuration is read.
"""

from __future__ import annotations

from praxis.config.settings import ProviderName, Settings, get_settings
from praxis.llm.accounting import CostLedger
from praxis.llm.errors import ProviderUnavailableError
from praxis.llm.mock import MockProvider
from praxis.llm.provider import LLMProvider
from praxis.llm.replay import ReplayProvider
from praxis.llm.trace import TraceSink


def provider_for(
    settings: Settings | None = None,
    *,
    sink: TraceSink | None = None,
    ledger: CostLedger | None = None,
    run_id: str | None = None,
    cite_coherently: bool = False,
) -> LLMProvider:
    """Return the provider this configuration selects.

    Args:
        settings: Configuration, read once if not supplied.
        sink: Where traces go. Defaults to an in-memory sink, so a provider
            works before any store exists.
        ledger: The run's budget. Defaults to the configured ceiling.
        run_id: Groups this run's traces. Defaults to a fresh one.
        cite_coherently: Asks the mock to quote from the passage it cites.
            Ignored by every other provider, which have no say in the matter.
            ADR 0034.

    Returns:
        `MockProvider` unless something says otherwise -- the default is
        offline, which is what makes a fresh clone run the whole pipeline.

    Raises:
        ProviderUnavailableError: if the live provider is selected and either
            its optional dependency or its key is missing. Both are the same
            failure to the person who hit it: this run cannot go live.
    """
    resolved = settings or get_settings()
    if resolved.llm_provider is ProviderName.REPLAY:
        return ReplayProvider(sink=sink, ledger=ledger, run_id=run_id, settings=resolved)
    if resolved.llm_provider is ProviderName.ANTHROPIC:
        live = _live_class(resolved)
        return live(sink=sink, ledger=ledger, run_id=run_id, settings=resolved)
    # The mock is last because it is the default, not because anything unknown
    # should quietly become it: a provider name with no branch would answer
    # with synthesised text and hide itself inside metrics that look like a
    # working run. `test_factory` walks every member of `ProviderName` and
    # asserts the provider it gets back reports that same name, so a member
    # falling through to here fails a test rather than a demo.
    return MockProvider(
        sink=sink,
        ledger=ledger,
        run_id=run_id,
        settings=resolved,
        cite_coherently=cite_coherently,
    )


def _live_class(settings: Settings) -> type[LLMProvider]:
    """Return the live provider class, importing the SDK only now.

    Deferred rather than top-level because `praxis.llm.anthropic` imports the
    vendor package at module scope: importing it here would make the offline
    default depend on an optional extra being installed, which keeps invariant
    1's promise only halfway.
    """
    try:
        from praxis.llm.anthropic import (  # noqa: PLC0415 -- the whole point of the branch
            AnthropicProvider,
            RecordingAnthropicProvider,
        )
    except ImportError as exc:  # pragma: no cover -- needs the extra uninstalled
        message = (
            "PRAXIS_LLM_PROVIDER=anthropic needs the live extra: `uv sync --extra live`. "
            "The default mock provider needs neither the package nor a key."
        )
        raise ProviderUnavailableError(message) from exc

    return RecordingAnthropicProvider if settings.record_replay else AnthropicProvider
