"""The record types Praxis reasons over, and nothing that knows about storage.

This package is pure: Pydantic models, typed identifiers, the link vocabulary
and the span verification function. It imports no SQL and no provider, so the
records can be constructed and validated in a test with no database and no
credentials.
"""
