"""The shared shape of every record: strict, frozen, versioned, tz-aware.

Two base classes rather than one. `VersionedRecord` carries the append-only
machinery -- a version number and a retraction flag -- and every graph node
extends it. `AuditEvent` extends the plainer `Record`, because the log
describing changes to records is not itself a record that gets revised. Giving
it a `version` column would have invited exactly that.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, ClassVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from pydantic import field_validator as _field_validator

from praxis.domain import ids
from praxis.domain.enums import RecordKind

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
"""Prose that must actually say something. Stripped, so two spellings of the
same value that differ only in trailing whitespace cannot both exist."""

Confidence = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
"""A probability. Bounded rather than merely typed, because these feed the Brier
score directly and a confidence outside [0, 1] would silently poison it."""

Quantity = Annotated[Decimal, Field(ge=0, allow_inf_nan=False, strict=True)]
"""An estimated or observed amount.

`Decimal`, never `float`: quantities are compared against each other and summed
across a whole calibration history, and binary floating point makes those sums
depend on their order.

Two details that each close a hole the obvious annotation leaves open.
`strict=True` is what makes `active_quantity=2.0` an error rather than a
silently converted float, which is invariant 4 with teeth instead of a
convention. And `allow_inf_nan=False` matters more than it looks:
`Decimal('Infinity') >= 0` is true, so the bound alone would let it through.
"""

WorkClass = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
]
"""The kind of work an estimate covers, as kebab-case, e.g. `data-modelling`.

Calibration is per estimator *per work class*, so this string is a grouping key
rather than a label. Constraining the spelling is what stops `data-modelling`
and `Data Modelling` becoming two classes with half the sample size each.
"""


class Record(BaseModel):
    """Base for everything Praxis stores.

    The config is deliberately unforgiving:

    - `frozen` -- records are values. An append-only store whose records could
      be mutated in memory would have two truths, one of them unwritten.
    - `extra="forbid"` -- a misspelled field is an error, not a silently
      discarded one. This is the "no loose dicts" rule with teeth.

    Strictness is applied per field rather than to the whole model, and that is
    a considered choice. Whole-model strict mode also rejects `"markdown"` for a
    `SourceKind` and a list for a tuple, which is parsing rather than looseness
    -- it would buy nothing and push conversions into every caller. The place
    coercion genuinely causes harm is a `float` becoming a `Decimal` quantity,
    so `Quantity` carries `strict=True` and the model does not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_kind: ClassVar[RecordKind]
    """Which table and node kind this class maps to. Set by every subclass."""

    id: str

    @_field_validator("id")
    @classmethod
    def _id_matches_kind(cls, value: str) -> str:
        """Reject an id whose prefix belongs to a different record kind."""
        return ids.validate(value, cls.record_kind)


class VersionedRecord(Record):
    """A record that changes by gaining a version, never by being edited.

    `version` is assigned by the store, not by the caller: it is the store that
    knows what the current version is, and letting a caller nominate one would
    make two concurrent writers able to agree on a number that is wrong.
    """

    version: int = Field(default=1, ge=1)
    retracted: bool = False
    """Set on a *new* version to withdraw a record. The withdrawn versions stay
    readable, so a retraction is a statement about a record rather than the
    disappearance of one."""

    created_at: AwareDatetime
    """When this version was written. Timezone-aware without exception: an
    assumption's expiry is a comparison against wall-clock time, and a naive
    datetime makes that comparison depend on which machine ran it."""

    created_by: NonEmptyStr
    """The agent or human that wrote this version."""
