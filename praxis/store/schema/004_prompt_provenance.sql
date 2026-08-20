-- 004_prompt_provenance: which prompt, at which version, produced this row.
--
-- `request_json` already holds the system prompt text in full, so in a narrow
-- sense the prompt was always recoverable. Two things it could not do, and both
-- of them are what Phase 4 needs before it reports a precision figure:
--
--   * group a run by prompt version without parsing a JSON blob per row, and
--   * tell two versions whose text differs by a character apart from two runs
--     of one version.
--
-- A column and a digest cost one migration and answer both as a query. See
-- ADR 0014.
--
-- Nullable, and deliberately so. Every row written before this migration has no
-- prompt id and never will, and a NOT NULL column would have had to invent one
-- -- which is exactly the kind of fabricated provenance the trace table exists
-- to make impossible. A NULL here means "written before prompts were versioned"
-- and says so honestly.

ALTER TABLE llm_trace ADD COLUMN prompt_id TEXT;
ALTER TABLE llm_trace ADD COLUMN prompt_sha TEXT;

-- "Which prompt version produced these numbers?" -- the query the eval report
-- runs once per table it renders, and the A/B query if a version is ever
-- compared against its predecessor.
CREATE INDEX llm_trace_prompt_id ON llm_trace (prompt_id, seq);
