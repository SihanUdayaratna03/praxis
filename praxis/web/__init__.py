"""The read-only dashboard: a JSON API over the store, and the page that draws it.

Only `praxis.web.server` binds a socket, and nothing here writes. See ADR 0036
for the framework choice and ADR 0037 for the seam.
"""
