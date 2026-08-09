"""Praxis: organizational memory and calibration engine.

Two halves that need each other:

* **Provenance** captures every decision with the assumptions it rests on, and
  turns those assumptions into machine-checkable predicates that expire.
* **Calibration** logs every estimate as a prediction, matches outcomes back to
  it, and learns each estimator's directional bias per class of work.

The fusion between them is the product: most decision assumptions are estimates
in disguise, so calibration data can invalidate a decision, and a missed
estimate can be traced forward to every decision that leaned on it.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
