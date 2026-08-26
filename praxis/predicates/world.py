"""The facts, the events and the clock a predicate is read against.

Three sources of truth, deliberately separate, because a predicate and an expiry
condition ask different questions of them. `index_size_gb <= 50` needs a
*measurement*; `on_event("the work ships")` needs to know whether something
*happened*; `after("2026-08-31")` needs the time. Merging them into one mapping
would mean encoding an event as a boolean fact named after its own description,
and nothing could then tell "the event has not happened" from "nobody recorded
whether it did" -- which is the same three-valued distinction one level up.

**A world state is a value, not a service.** It answers from what it was built
with and never reaches for anything, so evaluating a predicate is a pure
function of a tree and this object. That is what makes invariant 3 checkable:
two runs over one store and one facts file produce the same verdicts, and a
property test can generate a world rather than mock one.

**Event names are compared after normalisation, and only after it.** Case and
run-length of whitespace are spelling; anything else is a different event. An
`on_event` condition whose description differs from the observation by more than
that is a judgement call, and `AssumptionMonitor` is where that judgement is made
-- with a model, on a path that can only age an assumption and never breach one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

type Value = Decimal | bool | str
"""What an identifier may be bound to. The same three kinds a `Constant` holds,
so a comparison between a fact and a literal is a comparison between like things."""


def normalise_event(description: str) -> str:
    """Reduce an event description to what two spellings of it share.

    Args:
        description: The event as written, in a condition or in an observation.

    Returns:
        Case-folded, with every run of whitespace collapsed to one space and the
        ends trimmed.
    """
    return " ".join(description.split()).casefold()


@dataclass(frozen=True, slots=True)
class WorldState:
    """Everything a predicate or an expiry condition may be evaluated against.

    Attributes:
        now: When the evaluation is happening. Timezone-aware, invariant 5 --
            an expiry is a comparison against wall-clock time, and a naive one
            would fire at a different moment on two machines.
        facts: Identifier to value. An identifier absent here is *unmeasured*,
            which is `UNKNOWN` and not `false`.
        events: Descriptions of things that have happened, as observed. Compared
            through `normalise_event`, so the set is stored as written and
            matched after normalising.
    """

    now: datetime
    facts: Mapping[str, Value] = field(default_factory=dict)
    events: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        """Freeze the mapping and refuse a naive clock.

        Raises:
            ValueError: if `now` carries no timezone.
        """
        if self.now.tzinfo is None:
            message = "a world state's clock must be timezone-aware"
            raise ValueError(message)
        # Wrapped rather than copied so a caller cannot mutate the facts out
        # from under an evaluation that has already read some of them. The
        # dataclass is frozen; without this the mapping inside it would not be.
        object.__setattr__(self, "facts", MappingProxyType(dict(self.facts)))

    def value_of(self, name: str) -> Value | None:
        """What an identifier is bound to, or `None` if nothing bound it."""
        return self.facts.get(name)

    def observed(self, description: str) -> bool:
        """Whether an event matching this description has been recorded.

        Args:
            description: The event named by an `on_event(...)` condition.

        Returns:
            True if any observation normalises to the same text.
        """
        wanted = normalise_event(description)
        return any(normalise_event(seen) == wanted for seen in self.events)

    def with_events(self, *descriptions: str) -> WorldState:
        """A copy of this world with more observations in it.

        Used by `AssumptionMonitor` when a model has recognised an observation
        as the event a condition names: the recognition becomes an ordinary
        observation and the deterministic evaluator does the rest, so nothing
        downstream has to know a model was involved.

        Args:
            descriptions: Events to add.

        Returns:
            A new world state. This one is unchanged.
        """
        return WorldState(now=self.now, facts=self.facts, events=self.events.union(descriptions))

    def with_facts(self, facts: Mapping[str, Value]) -> WorldState:
        """A copy of this world with more bindings in it.

        Later bindings win, so a caller layering a facts file over what the
        store derived gets the file's answer -- which is the order
        `praxis.monitor.facts` documents and relies on.

        Args:
            facts: Bindings to add.

        Returns:
            A new world state. This one is unchanged.
        """
        return WorldState(now=self.now, facts={**self.facts, **facts}, events=self.events)
