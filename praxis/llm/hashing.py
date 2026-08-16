"""The one identity a request has, across processes and machines.

Three things key off this hash, and they must agree or none of them works: the
fixture a `ReplayProvider` loads, the answer a `MockProvider` synthesises, and
the `prompt_hash` column that makes "how many times did we ask this exact
question" a query rather than a diff.

Stability is the whole requirement, so the two ways a hash usually stops being
stable are closed here rather than left to the caller:

- **Key order.** `json.dumps` follows insertion order by default, so two
  dictionaries built by different code paths with the same contents serialise
  differently. `sort_keys=True` makes the rendering a function of the value.
- **Unicode escaping.** The default escapes non-ASCII, so the same prompt
  hashes differently depending on nothing at all. `ensure_ascii=False` keeps
  the bytes the prompt actually has, and the encode is pinned to UTF-8.

The digest deliberately does not cover the model id. A recording is keyed by
what was *asked*, so a mock trace and a replay trace of the same prompt share a
hash and can be compared; the model that answered is recorded alongside the
fixture, and `ReplayProvider` refuses a recording made by a different one.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final

from praxis.llm.types import LLMRequest

DIGEST_CHARS: Final = 32
"""128 bits of lower-case hex.

Twice the width of a content-addressed record id, because the failure differs.
A span id collision would merge two citations, which a person would notice; a
replay key collision would silently answer one question with another
question's recorded response, which nobody would.
"""


def canonical_json(payload: Any) -> str:
    """Render a value as the one JSON string that identifies it.

    Args:
        payload: Anything `json.dumps` accepts. In practice a request's
            `canonical()`.

    Returns:
        Compact JSON with sorted keys and unescaped non-ASCII.
    """
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def prompt_hash(request: LLMRequest) -> str:
    """Return the stable key for a request.

    Deterministic across processes, machines and runs: two callers that build
    the same request arrive at the same key without coordinating, which is
    what makes a recorded corpus shareable and an eval run reproducible.
    """
    return digest_of(request.canonical())


def digest_of(payload: Any) -> str:
    """Hash any JSON-serialisable value with the same rules a request uses."""
    encoded = canonical_json(payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:DIGEST_CHARS]
