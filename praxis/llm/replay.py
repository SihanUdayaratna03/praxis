"""Recorded answers, played back exactly, or a loud failure.

The point of this provider is what it refuses to do. It never calls a network,
and on a miss it raises rather than falling back to one: a replay run that
quietly went live where a fixture was absent would make an eval result depend on
which fixtures happened to be present, which is the failure replay exists to
rule out. `ReplayCacheMissError` is therefore the whole design, not an edge
case.

A fixture is trusted only as far as it can be checked. Three things are verified
before a recording is played back, and each answers a way a fixture corpus rots:

- **The recorded request must still hash to the key it is filed under.** A
  fixture edited by hand -- to fix a typo in the prompt, say -- is answering a
  different question than the one it will now be asked, and nothing else in the
  system would notice.
- **The model must be the one routing chose today.** A recording made before a
  model deprecation is evidence about the old model. `praxis.config.models` is
  the only place that changes, and this is where that change surfaces.
- **The file must parse and carry every field.** A truncated write is not a
  recording.

All three raise `ReplayCacheMissError` with a `detail`, because the response to
each is the same: record it again.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, Self

from praxis.config.models import ModelSpec
from praxis.config.settings import ProviderName, Settings
from praxis.llm.accounting import CostLedger
from praxis.llm.errors import ReplayCacheMissError
from praxis.llm.hashing import digest_of, prompt_hash
from praxis.llm.provider import LLMProvider
from praxis.llm.trace import TraceSink
from praxis.llm.types import LLMRequest, LLMResponse, StopReason, TokenUsage

FIXTURE_SUFFIX = ".json"


def fixture_path(root: Path, request: LLMRequest) -> Path:
    """Return the one path a request's recording can live at.

    Filed under the agent because a corpus of bare digests is unreadable, and a
    person recording fixtures needs to see which agent is missing them. The
    agent is part of the replay key, so this stays a function of the request:
    one request, one path, on any machine.
    """
    return root / request.agent / f"{prompt_hash(request)}{FIXTURE_SUFFIX}"


@dataclass(frozen=True, slots=True)
class Recording:
    """One live response, saved so it can be replayed without the network.

    Attributes:
        prompt_hash: The replay key, repeated inside the file so that a
            recording moved to the wrong name is detectable.
        request: The request's canonical rendering. Kept in full so a fixture
            is readable on its own and so its integrity can be re-derived
            rather than trusted.
        response: The text the model returned, unparsed.
        model_id: Which model answered.
        stop_reason: Why it stopped.
        usage: What it consumed. Replayed calls cost nothing, but the token
            counts are what a report of "what this run would have cost" reads.
        recorded_at: When, timezone-aware (invariant 5).
    """

    prompt_hash: str
    request: dict[str, Any]
    response: str
    model_id: str
    stop_reason: StopReason
    usage: TokenUsage
    recorded_at: datetime

    def __post_init__(self) -> None:
        """Reject a naive timestamp, wherever the recording came from."""
        if self.recorded_at.tzinfo is None:
            message = "recorded_at must be timezone-aware"
            raise ValueError(message)

    @classmethod
    def of(cls, request: LLMRequest, response: LLMResponse) -> Recording:
        """Capture a live exchange."""
        return cls(
            prompt_hash=prompt_hash(request),
            request=request.canonical(),
            response=response.text,
            model_id=response.model_id,
            stop_reason=response.stop_reason,
            usage=response.usage,
            recorded_at=datetime.now(UTC),
        )

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> Self:
        """Rebuild a recording from a fixture file's contents.

        Raises:
            KeyError: if a required field is absent. Caught by the loader and
                re-raised as a miss, since a fixture missing half its fields is
                something to record again rather than to reason about.
        """
        usage = payload.get("usage") or {}
        return cls(
            prompt_hash=payload["prompt_hash"],
            request=payload["request"],
            response=payload["response"],
            model_id=payload["model_id"],
            stop_reason=StopReason.from_api(payload["stop_reason"]),
            usage=TokenUsage(**usage),
            recorded_at=datetime.fromisoformat(payload["recorded_at"]),
        )

    def to_json(self) -> str:
        """Render the recording as the text of its fixture file.

        Indented rather than compact: fixtures are read and diffed by people,
        and the replay key is computed from `request` rather than from these
        bytes, so the formatting is free.
        """
        return json.dumps(
            {
                "prompt_hash": self.prompt_hash,
                "recorded_at": self.recorded_at.isoformat(),
                "model_id": self.model_id,
                "stop_reason": self.stop_reason.value,
                "usage": {
                    "input_tokens": self.usage.input_tokens,
                    "output_tokens": self.usage.output_tokens,
                    "cache_read_input_tokens": self.usage.cache_read_input_tokens,
                    "cache_creation_input_tokens": self.usage.cache_creation_input_tokens,
                },
                "request": self.request,
                "response": self.response,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )

    def as_response(self) -> LLMResponse:
        """Return the recording as the response it was made from."""
        return LLMResponse(
            text=self.response,
            model_id=self.model_id,
            usage=self.usage,
            stop_reason=self.stop_reason,
        )


def write_recording(root: Path, request: LLMRequest, response: LLMResponse) -> Path:
    """Save one live exchange where `ReplayProvider` will find it.

    Args:
        root: The fixture directory, usually `Settings.replay_dir`.
        request: What was asked.
        response: What came back.

    Returns:
        The path written.
    """
    path = fixture_path(root, request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(Recording.of(request, response).to_json(), encoding="utf-8")
    return path


class ReplayProvider(LLMProvider):
    """Answers from a recorded fixture, or not at all."""

    name = ProviderName.REPLAY
    bills = False

    def __init__(
        self,
        *,
        root: Path | None = None,
        sink: TraceSink | None = None,
        ledger: CostLedger | None = None,
        run_id: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Point the provider at a fixture directory.

        Args:
            root: Where recordings live. Defaults to `Settings.replay_dir`.
            sink: Where traces go.
            ledger: The run's budget. Never charged.
            run_id: Groups this run's traces.
            settings: Configuration, read once if not supplied.
        """
        super().__init__(sink=sink, ledger=ledger, run_id=run_id, settings=settings)
        self.root = root if root is not None else self._settings.replay_dir

    def _invoke(self, request: LLMRequest, spec: ModelSpec) -> LLMResponse:
        """Load the recording for this request, or raise.

        Raises:
            ReplayCacheMissError: if there is no usable recording. Never a
                fallback to the network -- an eval run that silently went live
                on a miss would not be reproducible, which is the one property
                replay is for.
        """
        recording = self._load(request)
        if recording.model_id != spec.model_id:
            self._miss(
                request,
                f"a recording made by {recording.model_id} rather than {spec.model_id}",
            )
        return recording.as_response()

    def _load(self, request: LLMRequest) -> Recording:
        """Read and verify the fixture filed under this request's key."""
        path = fixture_path(self.root, request)
        if not path.is_file():
            self._miss(request, None)
        try:
            recording = Recording.from_json(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self._miss(request, f"an unreadable recording ({type(exc).__name__})")
        if digest_of(recording.request) != prompt_hash(request):
            self._miss(request, "a recording of a different question")
        return recording

    def _miss(self, request: LLMRequest, detail: str | None) -> NoReturn:
        """Raise the one error this provider has, saying what went wrong."""
        raise ReplayCacheMissError(
            prompt_hash(request),
            request.agent,
            fixture_path(self.root, request),
            detail,
        )
