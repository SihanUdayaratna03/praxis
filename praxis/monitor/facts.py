"""Where a predicate's quantities come from: a file, the store, and nothing else.

Two sources, layered in a fixed order, and the order is the decision.

1. **The store**, for the one binding the graph itself already asserts. An
   assumption with an `estimated_as` edge to an `Estimate` that an `Outcome`
   resolves is an assumption whose subject has been *measured* -- and that is
   the product's central claim running in miniature: a missed estimate reaching
   forward to breach the assumption a decision rests on. One hop, through edges
   the store holds, and no inference beyond it.
2. **A facts file**, for everything else. A person supplies measurements and
   observations as JSON; later bindings win, so a person can always correct what
   the store derived.

**Nothing here reaches the network and nothing here guesses.** An identifier no
source binds stays unbound, which the evaluator answers `UNKNOWN` to, which the
monitor reads as "not checked" rather than "violated". Filling a gap with a
plausible zero would turn every unmeasured assumption into a breach.

The store-derived half is built and tested here, and in a Phase 5 corpus run it
binds nothing: no agent writes an `Outcome` until Phase 6, so there is no
measurement to reach back through. That zero is the mechanism waiting rather
than the mechanism failing, and `docs/reports/phase-5.md` says so beside the
number.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

from praxis.domain.links import LinkType
from praxis.domain.records import Assumption, Estimate, Outcome
from praxis.obs.logging import get_logger
from praxis.predicates.errors import PredicateSyntaxError
from praxis.predicates.intervals import constraints_of
from praxis.predicates.parser import parse
from praxis.predicates.world import Value, WorldState
from praxis.store.repository import Repository

_log = get_logger(__name__)

FACTS_FILENAME: Final = "facts.json"
"""What `praxis monitor` looks for when handed a directory."""


class FactsFile(BaseModel):
    """Measurements and observations a person supplies to a monitoring run.

    Attributes:
        facts: Identifier to value. Numbers arrive as JSON numbers or strings
            and become `Decimal` either way -- invariant 4 has no exception for
            a file, and a rate read through binary floating point would move a
            verdict that sits on a threshold.
        events: Things that have happened, described in whatever words the
            person used. `AssumptionMonitor` is what reconciles a description
            with the event an `on_event(...)` condition names.
        as_of: When these were true. Defaults to the run's own clock; supplied
            when replaying a check against a past state, which is the only way
            to ask "was this breached last quarter".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    facts: Mapping[str, Value] = {}
    events: tuple[str, ...] = ()
    as_of: AwareDatetime | None = None

    @field_validator("facts", mode="before")
    @classmethod
    def _numbers_are_decimal(cls, given: object) -> object:
        """Read every numeric-looking value as `Decimal`, never as float.

        JSON has one number type and Python reads it as a float, which is
        exactly the representation invariant 4 keeps out of the arithmetic. A
        string that is not a number is left alone: `stage == "closed"` is a
        legitimate predicate and its value is a string.
        """
        if not isinstance(given, Mapping):
            return given
        return {str(name): _as_value(value) for name, value in given.items()}


def load_facts(path: Path) -> FactsFile:
    """Read a facts file, or the `facts.json` inside a directory.

    Args:
        path: The file, or a directory holding `FACTS_FILENAME`.

    Returns:
        What it says.

    Raises:
        OSError: if there is nothing there.
        ValueError: if it is not a well-formed facts file.
    """
    target = path / FACTS_FILENAME if path.is_dir() else path
    return FactsFile.model_validate_json(target.read_text(encoding="utf-8"))


def world_for(
    repository: Repository, *, now: datetime, supplied: FactsFile | None = None
) -> WorldState:
    """Build the world a monitoring run evaluates against.

    Args:
        repository: The store, for the bindings the graph itself asserts.
        now: The run's clock. Timezone-aware, invariant 5.
        supplied: A person's measurements and observations, layered on top.

    Returns:
        The world state. Identifiers no source binds stay unbound.
    """
    at = supplied.as_of if supplied is not None and supplied.as_of is not None else now
    world = WorldState(now=at, facts=measured_in(repository))
    if supplied is None:
        return world
    return world.with_facts(supplied.facts).with_events(*supplied.events)


def measured_in(repository: Repository) -> dict[str, Value]:
    """The bindings the store can assert without inferring anything.

    One hop and one hop only: an assumption whose predicate names exactly one
    quantity, carrying an `estimated_as` edge to an estimate that an outcome
    resolves. The outcome's active quantity binds that name.

    **Exactly one quantity is the condition, and it is not fussiness.** A
    predicate naming two is a predicate where nothing says which of them the
    estimate is about, and binding the wrong one would produce a breach with a
    number attached -- the most convincing kind of wrong answer this system
    could give.

    Args:
        repository: The store.

    Returns:
        Identifier to measured value. Empty until something writes an `Outcome`.
    """
    # Keyed by plain `str` rather than by the typed id, because a `Link`'s
    # endpoint is a `NodeId` and the two do not unify through an invariant
    # mapping. The lookup is a string lookup either way.
    resolved = {
        str(outcome.estimate_id): outcome
        for outcome in repository.list_all(Outcome)
        if outcome.active_quantity is not None
    }
    if not resolved:
        return {}
    estimates = {str(estimate.id): estimate for estimate in repository.list_all(Estimate)}
    facts: dict[str, Value] = {}
    for assumption in repository.list_all(Assumption):
        binding = _binding_for(repository, assumption, resolved, estimates)
        if binding is not None:
            facts[binding[0]] = binding[1]
    return facts


def _binding_for(
    repository: Repository,
    assumption: Assumption,
    resolved: Mapping[str, Outcome],
    estimates: Mapping[str, Estimate],
) -> tuple[str, Value] | None:
    """The one name an assumption's own measured outcome binds, if there is one."""
    name = subject_of(assumption)
    if name is None:
        return None
    for link in repository.links_from(assumption.id, types=(LinkType.ESTIMATED_AS,)):
        outcome = resolved.get(link.target_id)
        if outcome is None or link.target_id not in estimates:
            continue
        if outcome.unit is not estimates[link.target_id].unit:
            # Comparing across units is how a four-week overrun reads as four
            # hours. `OutcomeMatcher` owns unit reconciliation in Half B; until
            # then a mismatch is left unbound rather than converted here.
            _log.warning(
                "outcome_unit_mismatch",
                assumption_id=assumption.id,
                estimate_id=link.target_id,
                outcome_unit=outcome.unit.value,
                estimate_unit=estimates[link.target_id].unit.value,
            )
            continue
        if outcome.active_quantity is not None:
            return name, outcome.active_quantity
    return None


def subject_of(assumption: Assumption) -> str | None:
    """The single quantity an assumption's predicate is about, or `None`.

    Read off the parsed predicate rather than off a field the formalizer wrote,
    so the answer cannot disagree with the expression it describes. Also the
    blocking key `ContradictionDetector` buckets on, which is why it lives here
    once rather than in both callers.

    Args:
        assumption: The record.

    Returns:
        The name, or `None` when the predicate does not parse, names nothing, or
        names more than one thing.
    """
    try:
        formula = parse(assumption.predicate)
    except PredicateSyntaxError:
        return None
    names = {constraint.name for constraint in constraints_of(formula)}
    return next(iter(names)) if len(names) == 1 else None


def _as_value(given: object) -> object:
    """One JSON value as the three kinds a predicate compares.

    Booleans are tested before numbers because `True` is an `int` in Python, and
    a boolean silently becoming `Decimal(1)` would make `valid == true` compare
    a number with a boolean and answer `UNKNOWN` forever.
    """
    if isinstance(given, bool):
        return given
    if isinstance(given, int | float):
        return Decimal(str(given))
    if isinstance(given, str):
        try:
            return Decimal(given)
        except InvalidOperation:
            return given
    return given
