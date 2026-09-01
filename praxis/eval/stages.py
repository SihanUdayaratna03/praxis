"""Which stages a graded run turns on.

Its own module so `harness` and `ablation` can share it without importing
each other. See ADR 0035 for what the rungs mean.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class Stages:
    """The stages `evaluate` runs. All on is the ordinary graded run.

    Attributes:
        floor_only: Segment on the paragraph floor, skipping the model.
        estimation: Run Half B.
        memory: Run formalization, monitoring and detection.
        calibration: Run the calibration pass.
        fusion: Run the fusion pass.
        governance: Run the adversarial and governance pass.
    """

    floor_only: bool = False
    estimation: bool = True
    memory: bool = True
    calibration: bool = True
    fusion: bool = True
    governance: bool = True

    @classmethod
    def floor(cls) -> Stages:
        """The baseline rung: the floor segmenter and extraction, nothing after."""
        return cls(
            floor_only=True,
            estimation=False,
            memory=False,
            calibration=False,
            fusion=False,
            governance=False,
        )

    def with_(self, **stages: bool) -> Stages:
        """A copy with some stages changed. How a rung is built from the one below."""
        return replace(self, **stages)
