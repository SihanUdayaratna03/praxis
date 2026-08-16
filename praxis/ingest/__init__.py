"""Turning a file into `Document` and `Span` records nothing can lie about.

Ingestion is where span integrity is either established or lost. Every claim any
later agent makes carries a `span_id`, and that span is only worth something if
its byte offsets really address the text it quotes. If this package produces a
sloppy span, every downstream agent inherits the sloppiness and no amount of
careful prompting recovers it.

Three modules and one rule between them:

- `adapters` normalises bytes into the exact text every offset is an offset
  into. Deterministic parsing, no model.
- `blocks` cuts that text into the addressable units a segmenter is allowed to
  group. Deterministic, and the floor the segmenter degrades to.
- `segmenter` asks a model which runs of blocks belong together -- and nothing
  else. **A model never supplies a byte offset**, so a hallucinated citation is
  not a thing this package can emit.
- `verifier` re-reads every span against its document anyway, because
  "impossible by construction" and "checked regardless" are the only pairing
  worth making a promise out of.
"""
