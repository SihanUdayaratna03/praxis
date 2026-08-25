"""The predicate language an assumption is compiled into, and its evaluator.

`Assumption.predicate` has been a string since Phase 1 and `Assumption.expiry_condition`
beside it, both documented as "held as text until the Phase 5 DSL parses it". This is
that DSL. Nothing here calls a model: invariant 3 names the predicate evaluator
explicitly, and the argument is in `praxis.predicates.evaluator`.
"""
