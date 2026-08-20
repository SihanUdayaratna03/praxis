"""Grading an extraction run against a corpus whose answers are known.

Four modules, and the split is the one `EST-0005` priced as four line items
because they fail in four different ways.

- `matching` pairs what was extracted with what should have been, by span
  overlap. Everything about *which* record answers *which* question is here.
- `metrics` counts the pairs. Deterministic arithmetic and nothing else --
  invariant 3, property-tested rather than trusted.
- `harness` runs a corpus end to end and produces a graded result.
- `report` renders it. No arithmetic, so a formatting change can never move a
  number.
"""
