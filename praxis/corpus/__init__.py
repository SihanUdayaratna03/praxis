"""A corpus whose answers are known, and the format that says what they are.

Phase 10 grades extractions automatically, which is only possible against a
corpus where the right answer is written down in a shape a program can compare
against. `groundtruth` is that shape, argued for in ADR 0012, and `generator`
produces documents together with it.

The generator emits ground truth **by construction**: it assembles a document
from fragments and records the byte range of each fragment as it appends it.
Nothing is ever located by searching the finished text, because that shortcut
mislabels silently every time a phrase repeats, and a generator drawing from
fixed phrase menus repeats phrases constantly.

This lands in Phase 3 rather than Phase 10 because it is also Phase 3's own
test data: the pipeline needs documents with decisions, assumptions, estimates
and outcomes actually in them, and inventing those twice would produce two
corpora that disagree.
"""
