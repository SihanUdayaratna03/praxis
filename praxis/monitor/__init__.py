"""Evaluating formalized assumptions, and raising a breach when one is violated.

The half of Phase 5 that turns a compiled predicate into something that acts.
`praxis.predicates` decides *what a predicate says*; this decides *what to do
about it*, which is a different question with a different failure mode -- and
the one safety property everything here is built around is that **no model
output can produce a breach.** See `praxis.monitor.monitor`.
"""
