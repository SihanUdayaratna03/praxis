-- 003_traces: one row per attempt at one model call.
--
-- Deliberately not part of the graph and deliberately not the audit trail.
-- `audit_event` records what an agent *changed*, so every row of it names a
-- record that exists. A trace records what an agent was *asked* and what came
-- back, which is a different thing with a different lifetime: it exists for
-- calls that wrote nothing, and the rows most worth keeping are the ones where
-- the model refused, was truncated, or answered something that would not
-- parse. Those have no entity to point at, so folding the two tables together
-- would mean either dropping the failures or inventing a record for them.
--
-- No `node` row, no `record_version`, no foreign key into either: a trace is
-- evidence about the pipeline, not a member of the graph the pipeline builds.
-- Append-only all the same -- a trace that can be rewritten is not evidence.

CREATE TABLE llm_trace (
    -- Arrival order, and the only total order this table has. Two calls in one
    -- run can share a millisecond; SQLite's rowid cannot.
    seq            INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT    NOT NULL,
    occurred_at    TEXT    NOT NULL,
    agent          TEXT    NOT NULL,
    task           TEXT    NOT NULL,
    role           TEXT    NOT NULL,
    provider       TEXT    NOT NULL,
    model_id       TEXT    NOT NULL,
    prompt_hash    TEXT    NOT NULL,
    attempt        INTEGER NOT NULL,
    outcome        TEXT    NOT NULL,
    stop_reason    TEXT,

    input_tokens                INTEGER NOT NULL,
    output_tokens               INTEGER NOT NULL,
    cache_read_input_tokens     INTEGER NOT NULL,
    cache_creation_input_tokens INTEGER NOT NULL,

    -- The exact decimal text, never REAL. Invariant 4: this column is summed
    -- across a whole eval run and read off a table by a judge, and binary
    -- floating point would make that sum depend on the order it was taken in.
    cost_usd       TEXT    NOT NULL,
    latency_ms     INTEGER NOT NULL,

    -- Every word the model read, and everything it said. Kept in full because
    -- a trace that records only a hash can prove two calls were the same and
    -- can never show what either of them asked.
    request_json   TEXT    NOT NULL,
    response_text  TEXT    NOT NULL,
    error          TEXT,

    CONSTRAINT trace_role_known CHECK (role IN ('scan', 'extract', 'reason')),
    CONSTRAINT trace_provider_known CHECK (provider IN ('mock', 'anthropic', 'replay')),
    CONSTRAINT trace_outcome_known CHECK (outcome IN ('ok', 'malformed', 'refused', 'error')),
    CONSTRAINT trace_attempt_starts_at_one CHECK (attempt >= 1),
    CONSTRAINT trace_counts_are_counts CHECK (
        input_tokens >= 0 AND output_tokens >= 0
        AND cache_read_input_tokens >= 0 AND cache_creation_input_tokens >= 0
    ),
    CONSTRAINT trace_latency_is_a_duration CHECK (latency_ms >= 0),
    CONSTRAINT trace_run_says_something CHECK (length(trim(run_id)) > 0),

    -- The same rule the dataclass enforces, held here as well so it is true of
    -- a row written by hand: a failure explains itself and a success does not
    -- pretend to have failed.
    CONSTRAINT trace_error_matches_outcome CHECK ((outcome = 'error') = (error IS NOT NULL)),

    -- Timestamps are ISO-8601 and carry an offset, as everywhere else in this
    -- schema. Invariant 5.
    CONSTRAINT trace_occurred_at_is_offset_aware CHECK (
        occurred_at GLOB '????-??-??T??:??:??*'
        AND (occurred_at LIKE '%+__:__' OR occurred_at LIKE '%-__:__'
             OR occurred_at LIKE '%Z')
    )
) STRICT;

-- "What did that run cost, and what did it ask?" -- the report query.
CREATE INDEX llm_trace_run ON llm_trace (run_id, seq);

-- "How many times have we asked this exact question?" The replay key is what
-- makes that a lookup rather than a comparison of prompts.
CREATE INDEX llm_trace_prompt_hash ON llm_trace (prompt_hash);

-- The ablation table groups by agent; the eval harness groups by model.
CREATE INDEX llm_trace_agent ON llm_trace (agent, task);
CREATE INDEX llm_trace_model ON llm_trace (model_id);

CREATE TRIGGER llm_trace_is_append_only_update BEFORE UPDATE ON llm_trace
BEGIN SELECT RAISE(ABORT, 'append-only: a trace is evidence and is never rewritten'); END;
CREATE TRIGGER llm_trace_is_append_only_delete BEFORE DELETE ON llm_trace
BEGIN SELECT RAISE(ABORT, 'append-only: a trace is evidence and is never erased'); END;
