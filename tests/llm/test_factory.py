"""Configuration in, provider out -- the one place that reads the setting.

ADR 0005's second assumption is written as a predicate:
`files_changed_to_enable_live_models == 1`. It is true only while exactly one
function reads `PRAXIS_LLM_PROVIDER`, so the tests here are about that function
being complete and honest: every configured name is served by the
implementation it names, the offline path needs neither a key nor the optional
package, and the wiring a caller passes is not quietly dropped.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from praxis.config.settings import ProviderName, Settings
from praxis.llm.accounting import CostLedger
from praxis.llm.anthropic import AnthropicProvider, RecordingAnthropicProvider
from praxis.llm.factory import provider_for
from praxis.llm.mock import MockProvider
from praxis.llm.replay import ReplayProvider
from praxis.llm.trace import MemoryTraceSink
from praxis.llm.types import LLMRequest, Message, MessageRole

NOT_A_KEY = "xxxxxxxxxxxxxxxx"
"""A value shaped like a credential and obviously not one. The live provider
builds an SDK client from it and never makes a call, so nothing authenticates."""


def configured(tmp_path, **overrides) -> Settings:
    """Settings pointed at a throwaway directory, one field at a time."""
    fields = {"data_dir": tmp_path / "praxis", "replay_dir": tmp_path / "fixtures"}
    fields.update(overrides)
    return Settings(**fields)


class TestEveryNameIsServed:
    @pytest.mark.parametrize("name", list(ProviderName))
    def test_the_provider_reports_the_name_that_selected_it(self, name, tmp_path):
        # The exhaustiveness check. A new member of ProviderName that nobody
        # implemented would fall through to the mock, and this is what notices:
        # the mock reports 'mock', not the name that was asked for.
        settings = configured(
            tmp_path,
            llm_provider=name,
            anthropic_api_key=NOT_A_KEY if name is ProviderName.ANTHROPIC else None,
        )
        assert provider_for(settings).name is name

    def test_the_default_is_offline(self, tmp_path):
        # Invariant 1 as the shipped default, not as a documented option.
        provider = provider_for(configured(tmp_path))
        assert isinstance(provider, MockProvider)
        assert not provider.bills

    def test_replay_is_pointed_at_the_configured_fixtures(self, tmp_path):
        settings = configured(tmp_path, llm_provider=ProviderName.REPLAY)
        provider = provider_for(settings)
        assert isinstance(provider, ReplayProvider)
        assert provider.root == settings.replay_dir


class TestTheLiveBranch:
    def test_a_key_selects_the_live_provider(self, tmp_path):
        settings = configured(
            tmp_path, llm_provider=ProviderName.ANTHROPIC, anthropic_api_key=NOT_A_KEY
        )
        provider = provider_for(settings)
        assert isinstance(provider, AnthropicProvider)
        assert provider.bills

    def test_recording_is_chosen_here_not_inside_the_provider(self, tmp_path):
        # Whether a run writes fixtures is configuration, and the class that
        # can write them is the live one only.
        settings = configured(
            tmp_path,
            llm_provider=ProviderName.ANTHROPIC,
            anthropic_api_key=NOT_A_KEY,
            record_replay=True,
        )
        assert isinstance(provider_for(settings), RecordingAnthropicProvider)

    def test_an_offline_run_never_records(self, tmp_path):
        # Recording a mock's answer would seed the fixture corpus with
        # synthesised text, and every replayed eval after that would be
        # measuring the mock.
        settings = configured(tmp_path, record_replay=True)
        assert isinstance(provider_for(settings), MockProvider)

    def test_settings_refuses_the_live_provider_without_a_key(self, tmp_path):
        # The check sits in Settings, so it fires when configuration is read
        # rather than on the first call -- a run that cannot go live should
        # not get as far as making one.
        with pytest.raises(ValueError, match="PRAXIS_ANTHROPIC_API_KEY"):
            configured(tmp_path, llm_provider=ProviderName.ANTHROPIC)


class TestWiring:
    def test_the_sink_is_forwarded(self, tmp_path):
        sink = MemoryTraceSink()
        assert provider_for(configured(tmp_path), sink=sink).sink is sink

    def test_the_ledger_is_forwarded(self, tmp_path):
        ledger = CostLedger(ceiling_usd=Decimal("1.23"))
        assert provider_for(configured(tmp_path), ledger=ledger).ledger is ledger

    def test_the_run_id_is_forwarded(self, tmp_path):
        # Two providers built for one run must write traces that group.
        assert provider_for(configured(tmp_path), run_id="RUN-abc").run_id == "RUN-abc"

    def test_the_ceiling_comes_from_configuration_by_default(self, tmp_path):
        settings = configured(tmp_path, cost_ceiling_usd=2.5)
        assert provider_for(settings).ledger.ceiling_usd == Decimal("2.5")

    def test_two_calls_do_not_share_a_ledger(self, tmp_path):
        # Per-run, not global: a ceiling reached in one place must not leak
        # into another and make a suite order-dependent.
        first = provider_for(configured(tmp_path))
        second = provider_for(configured(tmp_path))
        assert first.ledger is not second.ledger


class TestItActuallyAnswers:
    def test_the_offline_default_completes_a_call_end_to_end(self, tmp_path):
        # The claim the whole phase rests on, exercised through the same
        # entry point Phase 3's agents will use.

        provider = provider_for(configured(tmp_path), sink=MemoryTraceSink())
        response = provider.complete(
            LLMRequest(
                agent="DecisionScout",
                task="scan_for_decisions",
                system="You find decisions.",
                messages=(
                    Message(
                        role=MessageRole.USER,
                        content="We chose SQLite over Postgres for the graph store.",
                    ),
                ),
            )
        )
        assert response.text
        assert provider.ledger.spent_usd == Decimal("0")
