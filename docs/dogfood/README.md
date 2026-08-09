# Dogfood corpus

Praxis's predictions about its own construction, and what actually happened.

Two JSONL files, append-only, one record per line:

- `estimates.jsonl` — written **before** a phase starts. An estimate logged
  after the fact is not a prediction, and including one would quietly inflate
  the calibration numbers in the Phase 12 demo.
- `outcomes.jsonl` — written when the phase closes, referencing the estimate by
  `id`.

Both files are shaped to load directly into the Phase 1 `Estimate` and
`Outcome` records, so the Phase 12 self-analysis reads them with the normal
ingestion path rather than a bespoke importer.

## Estimate fields

| Field | Meaning |
| ----- | ------- |
| `id` | `EST-NNNN`, never reused |
| `logged_at` | When the prediction was made, UTC, ISO-8601 |
| `owner` | Who made it |
| `subject` | What is being estimated |
| `work_class` | The taxonomy tag bias is segmented by |
| `quantity` / `unit` | The predicted value |
| `confidence` | Stated, 0–1, before any calibration |
| `measure` | Optional. `active` or `wall_clock`. Absent means wall clock. |
| `conditions` | What the estimate assumes |
| `calibration_applied` | Optional. Whether a learned factor was applied. |
| `calibration_note` | Optional. Why it was or was not. |
| `source` | Where it was stated |

`measure` exists because `EST-0001` was a wall-clock prediction and
`OUT-0001` showed that wall clock hides external blocks. Later estimates say
which they mean.

`calibration_applied` records whether the estimator corrected itself, so the
Phase 12 analysis can separate raw predictions from adjusted ones. Recording a
refusal to adjust is as useful as recording an adjustment — most of the time
early on, `n` is too small to correct with, and the honest note is why.

## Outcome fields

| Field | Meaning |
| ----- | ------- |
| `id` | `OUT-NNNN` |
| `estimate_id` | The `EST-NNNN` it resolves |
| `resolved_at` | UTC, ISO-8601 |
| `quantity` / `unit` | What actually happened, wall clock |
| `active_quantity` | Optional. Time actually spent working. |
| `blocked_quantity` | Optional. Time waiting on something external. |
| `match_quality` | `exact`, `partial`, or `unresolved` |
| `notes` | What made the difference, in one line |

`active_quantity` and `blocked_quantity` were added after the first outcome
proved they were needed. Phase 0's wall clock matched its estimate almost
exactly, and that was a coincidence: the engineering was over-estimated by
2.3x and an external block absorbed the difference. Without the split, the
calibrator would learn that this estimator is well calibrated on scaffolding,
which is the opposite of what happened. When they are present they sum to
`quantity`; when they are absent, all of `quantity` was active.

## The rule that makes this worth anything

The estimate is logged before the work and is never edited afterwards. The
whole demo rests on the predictions being real, and a corpus that was tidied up
in hindsight would show a suspiciously well-calibrated author — which is both
dishonest and a much less interesting result.
