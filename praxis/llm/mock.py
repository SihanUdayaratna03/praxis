"""The default provider: an answer with no network and no credentials.

This is the implementation invariant 1 is about. `PRAXIS_LLM_PROVIDER=mock` is
the default, CI has no secret, and every test, eval run, CLI command and
dashboard render goes through here unless something explicitly asks otherwise.

The interesting content is not in this module. `praxis.llm.synthesis` builds the
answer out of the schema and the prompt; what is left here is the small set of
decisions about *how a call behaves* rather than what it says:

- **The response is a function of the request.** Everything is keyed off the
  replay key, so the same question gets the same answer regardless of how many
  calls preceded it. A provider whose output depended on call order would make
  a parallel run and a serial run disagree, and Phase 4's determinism
  requirement is that they must not.
- **Token counts are reported, and cost is not charged.** Offline runs still
  populate the token columns so the eval harness can report what a run *would*
  have cost, which is the cheapest place in the system to produce that figure.
- **Latency is whatever it really was.** A mock that invented plausible
  latencies would put fiction in a column an eval table reads as measurement.
- **It can be asked to misbehave.** ADR 0005's uncomfortable assumption is that
  mock output is realistic enough to write agents against, and the failure
  modes are the part hardest to imitate. `malformed_share` and `refusal_share`
  make truncation and refusal reproducible on demand, so the repair loop and
  the abstention path are exercised by the real provider rather than by a stub
  that only exists in a test file. Both default to zero.
"""

from __future__ import annotations

from praxis.config.models import MOCK_MODEL_ID, ModelSpec
from praxis.config.settings import ProviderName, Settings
from praxis.llm.accounting import CostLedger
from praxis.llm.hashing import digest_of, prompt_hash
from praxis.llm.provider import LLMProvider
from praxis.llm.synthesis import synthesise_answer
from praxis.llm.trace import TraceSink
from praxis.llm.types import LLMRequest, LLMResponse, StopReason, TokenUsage

CHARS_PER_TOKEN_ESTIMATE = 4
"""Characters per token when reporting what a mock call consumed.

Not an API fact and not a tokenizer -- counting exactly needs a round trip, and
the offline provider is the one thing that may not make one. Deliberately not
`accounting.CHARS_PER_TOKEN_FLOOR`: that one guards a spending ceiling and must
over-count to be safe, while this one feeds a reported figure and should sit
near the truth for ordinary English prose.
"""

_TRUNCATION_SHARE = 0.6
"""How much of a truncated answer survives.

Enough that the text is recognisably the right answer and little enough that
the JSON cannot close, which is exactly what a real `max_tokens` cut produces
and the most common cause of malformed structured output.
"""


class MockProvider(LLMProvider):
    """Answers every request from the prompt, deterministically and for free."""

    name = ProviderName.MOCK
    bills = False

    def __init__(  # noqa: PLR0913 -- four inherited wires plus three behaviour knobs
        self,
        *,
        sink: TraceSink | None = None,
        ledger: CostLedger | None = None,
        run_id: str | None = None,
        settings: Settings | None = None,
        malformed_share: float = 0.0,
        refusal_share: float = 0.0,
        cite_coherently: bool = False,
    ) -> None:
        """Wire the provider and choose how often it misbehaves.

        Args:
            sink: Where traces go. Defaults to an in-memory sink.
            ledger: The run's budget. Never charged, but still counted.
            run_id: Groups this run's traces.
            settings: Configuration, read once if not supplied.
            malformed_share: The share of structured calls answered with a
                truncated body. Not random: which calls fail is decided by the
                replay key, so a failing request fails every time it is made
                and a bug found offline is reproducible from the trace row.
            refusal_share: The share of calls the model declines, decided the
                same way.
            cite_coherently: Quote from the passage a claim cites rather than
                from the prompt as a whole. Off by default -- the default draw
                is what Phases 2 to 9 measured, and it is the only offline
                exercise the citation gate's refusal branches get. `praxis
                eval` turns it on. ADR 0034.

        Raises:
            ValueError: if either share is outside 0 to 1, which would silently
                mean "never" and hide a misconfigured test.
        """
        super().__init__(sink=sink, ledger=ledger, run_id=run_id, settings=settings)
        for label, share in (
            ("malformed_share", malformed_share),
            ("refusal_share", refusal_share),
        ):
            if not 0.0 <= share <= 1.0:
                message = f"{label} is a share of calls, so it must be 0 to 1, got {share}"
                raise ValueError(message)
        self.malformed_share = malformed_share
        self.refusal_share = refusal_share
        self.cite_coherently = cite_coherently

    def _invoke(self, request: LLMRequest, spec: ModelSpec) -> LLMResponse:
        """Synthesise one answer, or one of the two failures worth imitating.

        Args:
            request: What to ask.
            spec: The model routing chose. Used only to cap the reported output
                at what that model could really have produced -- a mock run
                reports the shape of the live run it stands in for.

        Returns:
            A response carrying `MOCK_MODEL_ID`, never a real model id, so that
            no metric can be computed over a mixture of mock and live rows
            without the mixture being visible.
        """
        key = prompt_hash(request)
        if _drawn(key, "refusal") < self.refusal_share:
            return self._respond(request, spec, text="", stop_reason=StopReason.REFUSAL)

        text = synthesise_answer(
            request.schema, request.source_text, key, cite_coherently=self.cite_coherently
        )
        if request.schema is not None and _drawn(key, "malformed") < self.malformed_share:
            cut = max(1, int(len(text) * _TRUNCATION_SHARE))
            return self._respond(request, spec, text=text[:cut], stop_reason=StopReason.MAX_TOKENS)
        return self._respond(request, spec, text=text, stop_reason=StopReason.END_TURN)

    def _respond(
        self,
        request: LLMRequest,
        spec: ModelSpec,
        *,
        text: str,
        stop_reason: StopReason,
    ) -> LLMResponse:
        """Wrap synthesised text in a response with plausible token counts."""
        return LLMResponse(
            text=text,
            model_id=MOCK_MODEL_ID,
            usage=TokenUsage(
                input_tokens=_tokens_in(request.prompt_text),
                output_tokens=min(_tokens_in(text), request.max_tokens, spec.max_output_tokens),
            ),
            stop_reason=stop_reason,
        )


def _tokens_in(text: str) -> int:
    """Estimate a token count from a character count, rounding up."""
    return -(-len(text) // CHARS_PER_TOKEN_ESTIMATE)


def _drawn(key: str, purpose: str) -> float:
    """Draw a stable number in [0, 1) for one request and one kind of failure.

    Derived from the replay key rather than from a generator, so the answer
    does not depend on how many calls came before it -- and salted by purpose,
    so a request that is refused is not thereby also the one that truncates.
    """
    return int(digest_of([key, purpose])[:8], 16) / 0x1_0000_0000
