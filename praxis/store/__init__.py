"""The only part of Praxis that speaks SQL.

ADR 0003 claims the storage backend is replaceable without touching an agent.
That claim is only true while every statement lives inside this package, so it
is a boundary rather than a preference: no module outside `praxis.store` may
import `sqlite3` or contain a query.
"""
