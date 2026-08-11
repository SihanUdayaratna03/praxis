-- 001_core: the node registry, the version ledger, the nine records, the one
-- typed edge table, and the triggers that make append-only a property of the
-- database rather than a habit of the code that writes to it.
--
-- Three structural choices, each of which the rest of the store depends on.
--
-- `node` holds identity and nothing else: one row per entity, ever. Edges point
-- at a node, not at a version, which is what lets D-0042 reach version 3
-- without every edge into it having to be rewritten. Its `UNIQUE (id, kind)` is
-- there so an edge's *declared* endpoint kind can be a composite foreign key --
-- SQLite then enforces not only that the endpoint exists but that it is the
-- kind the edge claims it is.
--
-- `record_version` holds the version metadata every versioned record shares, so
-- "the current version of X" is one MAX over one table rather than a UNION over
-- nine, and an `audit_event` can name the exact version it describes with a real
-- foreign key rather than a convention.
--
-- Everything else is a payload table keyed `(id, version)`. Nothing in this file
-- is ever updated or deleted; a change is a new row plus an audit row, and the
-- triggers at the bottom abort any statement that says otherwise.

-- ---------------------------------------------------------------------------
-- Identity
-- ---------------------------------------------------------------------------

CREATE TABLE node (
    id      TEXT NOT NULL PRIMARY KEY,
    kind    TEXT NOT NULL,
    ordinal INTEGER,

    -- Mirrors praxis.domain.enums.GRAPH_KINDS. `audit_event` is absent on
    -- purpose: the log describing changes to the graph is not a member of it,
    -- and letting an audit row be an edge endpoint would make the trail part of
    -- the thing it audits.
    CONSTRAINT node_kind_known CHECK (
        kind IN ('document', 'span', 'decision', 'assumption',
                 'estimate', 'outcome', 'link', 'finding')
    ),

    -- Sequential kinds carry the counter their id was rendered from; the two
    -- content-addressed kinds have none. Storing it beats parsing 'D-0042'
    -- back apart every time the allocator needs the next one.
    CONSTRAINT node_ordinal_matches_kind CHECK (
        (kind IN ('span', 'link') AND ordinal IS NULL)
        OR (kind NOT IN ('span', 'link') AND ordinal >= 1)
    ),

    -- The composite foreign key target that makes a declared endpoint kind
    -- truthful rather than merely asserted.
    CONSTRAINT node_id_kind_unique UNIQUE (id, kind)
) STRICT;

CREATE UNIQUE INDEX node_kind_ordinal ON node (kind, ordinal);

-- ---------------------------------------------------------------------------
-- Versions
-- ---------------------------------------------------------------------------

CREATE TABLE record_version (
    id         TEXT    NOT NULL REFERENCES node (id),
    version    INTEGER NOT NULL,
    retracted  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    created_by TEXT    NOT NULL,

    PRIMARY KEY (id, version),

    CONSTRAINT version_starts_at_one CHECK (version >= 1),
    CONSTRAINT retracted_is_boolean CHECK (retracted IN (0, 1)),
    CONSTRAINT created_by_says_something CHECK (length(trim(created_by)) > 0),

    -- Timestamps are ISO-8601 and carry an offset without exception. An
    -- assumption's expiry is a comparison against wall-clock time, and a naive
    -- timestamp makes that comparison depend on which machine ran it.
    CONSTRAINT created_at_is_offset_aware CHECK (
        created_at GLOB '????-??-??T??:??:??*'
        AND (created_at LIKE '%+__:__' OR created_at LIKE '%-__:__'
             OR created_at LIKE '%Z')
    )
) STRICT;

-- ---------------------------------------------------------------------------
-- Records
-- ---------------------------------------------------------------------------

CREATE TABLE document (
    id           TEXT    NOT NULL,
    version      INTEGER NOT NULL,
    source_uri   TEXT    NOT NULL,
    source_kind  TEXT    NOT NULL,
    title        TEXT,
    content      TEXT    NOT NULL,
    content_hash TEXT    NOT NULL,
    byte_length  INTEGER NOT NULL,
    ingested_at  TEXT    NOT NULL,

    PRIMARY KEY (id, version),
    FOREIGN KEY (id, version) REFERENCES record_version (id, version),

    CONSTRAINT document_id_prefix CHECK (id GLOB 'DOC-*'),
    CONSTRAINT document_source_kind_known CHECK (source_kind IN ('markdown', 'text', 'json')),
    CONSTRAINT document_byte_length_is_a_length CHECK (byte_length >= 0)
) STRICT;

-- Recognising a re-ingested source is a hash lookup, not a content comparison.
CREATE INDEX document_content_hash ON document (content_hash);

CREATE TABLE span (
    id         TEXT    NOT NULL,
    version    INTEGER NOT NULL,
    doc_id     TEXT    NOT NULL REFERENCES node (id),
    start_byte INTEGER NOT NULL,
    end_byte   INTEGER NOT NULL,
    text       TEXT    NOT NULL,

    PRIMARY KEY (id, version),
    FOREIGN KEY (id, version) REFERENCES record_version (id, version),

    CONSTRAINT span_id_prefix CHECK (id GLOB 'SPAN-*'),
    -- GLOB rather than LIKE: LIKE is case-insensitive for ASCII, and would
    -- accept 'doc-...' as a document id.
    CONSTRAINT span_doc_is_a_document CHECK (doc_id GLOB 'DOC-*'),
    CONSTRAINT span_range_is_forward CHECK (start_byte >= 0 AND end_byte > start_byte)
) STRICT;

CREATE INDEX span_document ON span (doc_id, start_byte);

CREATE TABLE decision (
    id             TEXT    NOT NULL,
    version        INTEGER NOT NULL,
    title          TEXT    NOT NULL,
    chosen         TEXT    NOT NULL,
    rejected       TEXT    NOT NULL,
    decision_maker TEXT    NOT NULL,
    decided_at     TEXT    NOT NULL,
    scope          TEXT    NOT NULL,
    impact         TEXT    NOT NULL,
    status         TEXT    NOT NULL,
    span_id        TEXT    NOT NULL REFERENCES node (id),
    confidence     REAL    NOT NULL,

    PRIMARY KEY (id, version),
    FOREIGN KEY (id, version) REFERENCES record_version (id, version),

    CONSTRAINT decision_id_prefix CHECK (id GLOB 'D-*'),
    CONSTRAINT decision_cites_a_span CHECK (span_id GLOB 'SPAN-*'),
    CONSTRAINT decision_scope_known CHECK (
        scope IN ('personal', 'team', 'project', 'organisation')
    ),
    CONSTRAINT decision_impact_known CHECK (impact IN ('low', 'medium', 'high')),
    CONSTRAINT decision_status_known CHECK (
        status IN ('proposed', 'accepted', 'superseded', 'deprecated')
    ),
    CONSTRAINT decision_confidence_is_a_probability CHECK (confidence BETWEEN 0.0 AND 1.0),
    -- A decision with no rejected options is a note. The model enforces the
    -- same rule; this is what makes it true of hand-written SQL as well.
    CONSTRAINT decision_rejected_is_a_nonempty_array CHECK (
        json_valid(rejected) AND json_type(rejected) = 'array' AND json_array_length(rejected) >= 1
    )
) STRICT;

CREATE TABLE assumption (
    id                TEXT    NOT NULL,
    version           INTEGER NOT NULL,
    statement         TEXT    NOT NULL,
    predicate         TEXT    NOT NULL,
    expiry_condition  TEXT    NOT NULL,
    status            TEXT    NOT NULL,
    last_evaluated_at TEXT,
    span_id           TEXT    NOT NULL REFERENCES node (id),
    confidence        REAL    NOT NULL,

    PRIMARY KEY (id, version),
    FOREIGN KEY (id, version) REFERENCES record_version (id, version),

    CONSTRAINT assumption_id_prefix CHECK (id GLOB 'A-*'),
    CONSTRAINT assumption_cites_a_span CHECK (span_id GLOB 'SPAN-*'),
    CONSTRAINT assumption_status_known CHECK (
        status IN ('unverified', 'holding', 'breached', 'expired')
    ),
    CONSTRAINT assumption_confidence_is_a_probability CHECK (confidence BETWEEN 0.0 AND 1.0),
    -- A verdict with no evaluation time is indistinguishable from a monitor
    -- that never ran, and the difference is the whole value of the field.
    CONSTRAINT assumption_verdict_has_a_time CHECK (
        (status = 'unverified') = (last_evaluated_at IS NULL)
    )
) STRICT;

-- AssumptionMonitor's work queue is "everything not yet holding".
CREATE INDEX assumption_status ON assumption (status);

CREATE TABLE estimate (
    id               TEXT    NOT NULL,
    version          INTEGER NOT NULL,
    subject          TEXT    NOT NULL,
    owner            TEXT    NOT NULL,
    work_class       TEXT    NOT NULL,
    -- Quantities are the exact decimal text, never REAL. They are summed across
    -- a whole calibration history and read off a table by a judge; binary
    -- floating point would make those sums depend on their order. Invariant 4.
    active_quantity  TEXT    NOT NULL,
    blocked_quantity TEXT    NOT NULL,
    unit             TEXT    NOT NULL,
    confidence       REAL    NOT NULL,
    conditions       TEXT    NOT NULL,
    estimated_at     TEXT    NOT NULL,
    span_id          TEXT    NOT NULL REFERENCES node (id),

    PRIMARY KEY (id, version),
    FOREIGN KEY (id, version) REFERENCES record_version (id, version),

    CONSTRAINT estimate_id_prefix CHECK (id GLOB 'EST-*'),
    CONSTRAINT estimate_cites_a_span CHECK (span_id GLOB 'SPAN-*'),
    CONSTRAINT estimate_unit_known CHECK (
        unit IN ('hours', 'days', 'weeks', 'points', 'count', 'usd')
    ),
    CONSTRAINT estimate_confidence_is_a_probability CHECK (confidence BETWEEN 0.0 AND 1.0),
    CONSTRAINT estimate_work_class_is_kebab_case CHECK (
        work_class GLOB '[a-z0-9]*' AND work_class NOT GLOB '*[^a-z0-9-]*'
    ),
    CONSTRAINT estimate_conditions_is_an_array CHECK (
        json_valid(conditions) AND json_type(conditions) = 'array'
    )
) STRICT;

-- Calibration is per estimator per work class, so this pair is the grouping key
-- BiasDetective will read, not a reporting convenience.
CREATE INDEX estimate_owner_work_class ON estimate (owner, work_class);

CREATE TABLE outcome (
    id               TEXT    NOT NULL,
    version          INTEGER NOT NULL,
    estimate_id      TEXT    NOT NULL REFERENCES node (id),
    active_quantity  TEXT,
    blocked_quantity TEXT,
    unit             TEXT    NOT NULL,
    match_quality    TEXT    NOT NULL,
    resolved_at      TEXT,
    notes            TEXT    NOT NULL DEFAULT '',
    span_id          TEXT REFERENCES node (id),

    PRIMARY KEY (id, version),
    FOREIGN KEY (id, version) REFERENCES record_version (id, version),

    CONSTRAINT outcome_id_prefix CHECK (id GLOB 'OUT-*'),
    CONSTRAINT outcome_resolves_an_estimate CHECK (estimate_id GLOB 'EST-*'),
    CONSTRAINT outcome_cites_a_span CHECK (span_id IS NULL OR span_id GLOB 'SPAN-*'),
    CONSTRAINT outcome_unit_known CHECK (
        unit IN ('hours', 'days', 'weeks', 'points', 'count', 'usd')
    ),
    CONSTRAINT outcome_match_quality_known CHECK (
        match_quality IN ('exact', 'close', 'partial', 'miss', 'unresolved')
    ),
    -- Unresolved outcomes exist so estimates that never resolved stay visible
    -- in the calibration data instead of being dropped, which is how a curve
    -- ends up flattering its estimator. That only works if `unresolved` cannot
    -- also carry a number.
    CONSTRAINT outcome_unresolved_carries_no_numbers CHECK (
        (match_quality = 'unresolved') = (
            active_quantity IS NULL AND blocked_quantity IS NULL AND resolved_at IS NULL
        )
    )
) STRICT;

CREATE INDEX outcome_estimate ON outcome (estimate_id);

CREATE TABLE link (
    id          TEXT    NOT NULL,
    version     INTEGER NOT NULL,
    link_type   TEXT    NOT NULL,
    source_id   TEXT    NOT NULL,
    source_kind TEXT    NOT NULL,
    target_id   TEXT    NOT NULL,
    target_kind TEXT    NOT NULL,
    confidence  REAL    NOT NULL,
    rationale   TEXT    NOT NULL,
    span_id     TEXT REFERENCES node (id),

    PRIMARY KEY (id, version),
    FOREIGN KEY (id, version) REFERENCES record_version (id, version),

    -- The composite form, not two single-column keys. This is what makes a
    -- declared endpoint kind enforceable: an edge cannot claim its target is a
    -- decision while pointing at an estimate.
    FOREIGN KEY (source_id, source_kind) REFERENCES node (id, kind),
    FOREIGN KEY (target_id, target_kind) REFERENCES node (id, kind),

    CONSTRAINT link_id_prefix CHECK (id GLOB 'L-*'),
    CONSTRAINT link_cites_a_span CHECK (span_id IS NULL OR span_id GLOB 'SPAN-*'),
    CONSTRAINT link_type_known CHECK (
        link_type IN ('assumes', 'justified_by', 'contradicts',
                      'supersedes', 'estimated_as', 'collateral_of')
    ),
    CONSTRAINT link_confidence_is_a_probability CHECK (confidence BETWEEN 0.0 AND 1.0),
    CONSTRAINT link_is_not_a_self_loop CHECK (source_id <> target_id)
) STRICT;

-- The two traversal directions. Reverse first, because it is the one the
-- fusion query walks: "this estimate missed -- what rests on it?"
CREATE INDEX link_reverse ON link (target_id, link_type);
CREATE INDEX link_forward ON link (source_id, link_type);

CREATE TABLE finding (
    id           TEXT    NOT NULL,
    version      INTEGER NOT NULL,
    kind         TEXT    NOT NULL,
    subject_id   TEXT    NOT NULL,
    subject_kind TEXT    NOT NULL,
    prosecution  TEXT    NOT NULL,
    challenge    TEXT,
    verdict      TEXT    NOT NULL,
    severity     TEXT    NOT NULL,
    confidence   REAL    NOT NULL,
    detected_at  TEXT    NOT NULL,

    PRIMARY KEY (id, version),
    FOREIGN KEY (id, version) REFERENCES record_version (id, version),
    FOREIGN KEY (subject_id, subject_kind) REFERENCES node (id, kind),

    CONSTRAINT finding_id_prefix CHECK (id GLOB 'F-*'),
    CONSTRAINT finding_kind_known CHECK (
        kind IN ('assumption_breach', 'contradiction', 'calibration_bias',
                 'collateral_impact', 'stale_decision')
    ),
    CONSTRAINT finding_verdict_known CHECK (verdict IN ('undecided', 'upheld', 'overturned')),
    CONSTRAINT finding_severity_known CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT finding_confidence_is_a_probability CHECK (confidence BETWEEN 0.0 AND 1.0),
    -- Nothing high-severity reaches a human without surviving the challenger,
    -- so a verdict with no recorded challenge is a claim that a review happened
    -- when there is no evidence it did.
    CONSTRAINT finding_verdict_rests_on_a_challenge CHECK (
        verdict = 'undecided' OR challenge IS NOT NULL
    )
) STRICT;

CREATE INDEX finding_subject ON finding (subject_id);

-- A child table rather than a JSON array, because these are ids of rows that
-- have to exist. A JSON array is the one place in this schema where a
-- reference could dangle without SQLite noticing, and the evidence behind a
-- finding is not where to put that hole.
CREATE TABLE finding_evidence (
    finding_id      TEXT    NOT NULL,
    finding_version INTEGER NOT NULL,
    position        INTEGER NOT NULL,
    span_id         TEXT    NOT NULL REFERENCES node (id),

    PRIMARY KEY (finding_id, finding_version, position),
    FOREIGN KEY (finding_id, finding_version) REFERENCES finding (id, version),

    CONSTRAINT finding_evidence_is_a_span CHECK (span_id GLOB 'SPAN-*'),
    CONSTRAINT finding_evidence_position_is_ordered CHECK (position >= 0)
) STRICT;

-- ---------------------------------------------------------------------------
-- Audit
-- ---------------------------------------------------------------------------

-- Not versioned and never retracted: this is the log, and a log that can be
-- revised is a log nobody can rely on. Written in the same transaction as the
-- change it describes -- an audit row that can be lost independently of its
-- change is worse than no audit row, because it looks trustworthy.
CREATE TABLE audit_event (
    id             TEXT    NOT NULL PRIMARY KEY,
    -- Audit events are not graph nodes, so they cannot draw their counter from
    -- `node.ordinal` like every other sequential kind. It is a column here for
    -- the same reason it is one there: this is the fastest-growing table in the
    -- store, and deriving the next ordinal by parsing 'AUD-0042' back apart
    -- would be an unindexable scan on the path taken by every single write.
    ordinal        INTEGER NOT NULL,
    occurred_at    TEXT    NOT NULL,
    actor          TEXT    NOT NULL,
    action         TEXT    NOT NULL,
    entity_id      TEXT    NOT NULL,
    entity_kind    TEXT    NOT NULL,
    entity_version INTEGER NOT NULL,
    reason         TEXT    NOT NULL,
    run_id         TEXT,

    FOREIGN KEY (entity_id, entity_version) REFERENCES record_version (id, version),
    FOREIGN KEY (entity_id, entity_kind) REFERENCES node (id, kind),

    CONSTRAINT audit_id_prefix CHECK (id GLOB 'AUD-*'),
    -- There is no 'deleted'. Retraction is a new version carrying a flag, so
    -- the thing that was retracted stays readable and this row stays true.
    CONSTRAINT audit_action_known CHECK (action IN ('created', 'revised', 'retracted')),
    CONSTRAINT audit_reason_says_something CHECK (length(trim(reason)) > 0),
    CONSTRAINT audit_ordinal_starts_at_one CHECK (ordinal >= 1)
) STRICT;

CREATE UNIQUE INDEX audit_ordinal ON audit_event (ordinal);
CREATE INDEX audit_entity ON audit_event (entity_id, entity_version);
CREATE INDEX audit_occurred_at ON audit_event (occurred_at);
CREATE INDEX audit_run ON audit_event (run_id);

-- ---------------------------------------------------------------------------
-- Views: what "current" means, and the edge set the impact walk follows
-- ---------------------------------------------------------------------------

-- The head version of every entity. Answered from the (id, version) primary
-- key index, so it costs a scan of the index rather than of the rows.
CREATE VIEW record_head AS
SELECT id, MAX(version) AS version
FROM record_version
GROUP BY id;

CREATE VIEW current_record AS
SELECT n.id, n.kind, n.ordinal, rv.version, rv.retracted, rv.created_at, rv.created_by
FROM record_version AS rv
JOIN record_head AS h ON h.id = rv.id AND h.version = rv.version
JOIN node AS n ON n.id = rv.id;

CREATE VIEW current_link AS
SELECT l.id, l.version, l.link_type, l.source_id, l.source_kind,
       l.target_id, l.target_kind, l.confidence, l.rationale, l.span_id,
       rv.retracted, rv.created_at, rv.created_by
FROM link AS l
JOIN record_head AS h ON h.id = l.id AND h.version = l.version
JOIN record_version AS rv ON rv.id = l.id AND rv.version = l.version;

-- The impact DAG: the current, unretracted edges that mean "depends on".
--
-- Every one of these points from the dependent to the depended-upon -- a rule
-- praxis.domain.links enforces rather than hopes for -- so the whole of Phase
-- 8's question is reverse reachability over this one view:
--
--     WITH RECURSIVE impacted(id, kind, depth) AS (
--         SELECT :estimate_id, 'estimate', 0
--         UNION
--         SELECT e.source_id, e.source_kind, i.depth + 1
--         FROM dependency_edge AS e JOIN impacted AS i ON e.target_id = i.id
--         WHERE i.depth < :max_depth
--     )
--     SELECT id, kind, MIN(depth) FROM impacted WHERE depth > 0 GROUP BY id, kind;
--
-- `contradicts` and `supersedes` are excluded because neither expresses
-- dependency: following `supersedes` during a blast-radius walk would drag
-- every retired version of a record into the answer.
CREATE VIEW dependency_edge AS
SELECT source_id, source_kind, target_id, target_kind, link_type
FROM current_link
WHERE retracted = 0
  AND link_type IN ('assumes', 'justified_by', 'estimated_as');

-- ---------------------------------------------------------------------------
-- Append-only, enforced (CLAUDE.md invariant 7)
-- ---------------------------------------------------------------------------
--
-- A change is a new version plus an AuditEvent. These triggers are what make
-- that true of anything holding a connection, not only of the repository's own
-- methods -- including a person with the sqlite3 shell and a good reason.
-- RAISE(ABORT) surfaces as SQLITE_CONSTRAINT_TRIGGER, which
-- praxis.store.errors translates to AppendOnlyViolationError.

CREATE TRIGGER node_is_append_only_update BEFORE UPDATE ON node
BEGIN SELECT RAISE(ABORT, 'append-only: node rows are never updated'); END;
CREATE TRIGGER node_is_append_only_delete BEFORE DELETE ON node
BEGIN SELECT RAISE(ABORT, 'append-only: node rows are never deleted'); END;

CREATE TRIGGER record_version_is_append_only_update BEFORE UPDATE ON record_version
BEGIN SELECT RAISE(ABORT, 'append-only: a change is a new version, never an update'); END;
CREATE TRIGGER record_version_is_append_only_delete BEFORE DELETE ON record_version
BEGIN SELECT RAISE(ABORT, 'append-only: a version is retracted, never deleted'); END;

CREATE TRIGGER document_is_append_only_update BEFORE UPDATE ON document
BEGIN SELECT RAISE(ABORT, 'append-only: a change is a new version, never an update'); END;
CREATE TRIGGER document_is_append_only_delete BEFORE DELETE ON document
BEGIN SELECT RAISE(ABORT, 'append-only: a version is retracted, never deleted'); END;

CREATE TRIGGER span_is_append_only_update BEFORE UPDATE ON span
BEGIN SELECT RAISE(ABORT, 'append-only: a change is a new version, never an update'); END;
CREATE TRIGGER span_is_append_only_delete BEFORE DELETE ON span
BEGIN SELECT RAISE(ABORT, 'append-only: a version is retracted, never deleted'); END;

CREATE TRIGGER decision_is_append_only_update BEFORE UPDATE ON decision
BEGIN SELECT RAISE(ABORT, 'append-only: a change is a new version, never an update'); END;
CREATE TRIGGER decision_is_append_only_delete BEFORE DELETE ON decision
BEGIN SELECT RAISE(ABORT, 'append-only: a version is retracted, never deleted'); END;

CREATE TRIGGER assumption_is_append_only_update BEFORE UPDATE ON assumption
BEGIN SELECT RAISE(ABORT, 'append-only: a change is a new version, never an update'); END;
CREATE TRIGGER assumption_is_append_only_delete BEFORE DELETE ON assumption
BEGIN SELECT RAISE(ABORT, 'append-only: a version is retracted, never deleted'); END;

CREATE TRIGGER estimate_is_append_only_update BEFORE UPDATE ON estimate
BEGIN SELECT RAISE(ABORT, 'append-only: a change is a new version, never an update'); END;
CREATE TRIGGER estimate_is_append_only_delete BEFORE DELETE ON estimate
BEGIN SELECT RAISE(ABORT, 'append-only: a version is retracted, never deleted'); END;

CREATE TRIGGER outcome_is_append_only_update BEFORE UPDATE ON outcome
BEGIN SELECT RAISE(ABORT, 'append-only: a change is a new version, never an update'); END;
CREATE TRIGGER outcome_is_append_only_delete BEFORE DELETE ON outcome
BEGIN SELECT RAISE(ABORT, 'append-only: a version is retracted, never deleted'); END;

CREATE TRIGGER link_is_append_only_update BEFORE UPDATE ON link
BEGIN SELECT RAISE(ABORT, 'append-only: a change is a new version, never an update'); END;
CREATE TRIGGER link_is_append_only_delete BEFORE DELETE ON link
BEGIN SELECT RAISE(ABORT, 'append-only: a version is retracted, never deleted'); END;

CREATE TRIGGER finding_is_append_only_update BEFORE UPDATE ON finding
BEGIN SELECT RAISE(ABORT, 'append-only: a change is a new version, never an update'); END;
CREATE TRIGGER finding_is_append_only_delete BEFORE DELETE ON finding
BEGIN SELECT RAISE(ABORT, 'append-only: a version is retracted, never deleted'); END;

CREATE TRIGGER finding_evidence_is_append_only_update BEFORE UPDATE ON finding_evidence
BEGIN SELECT RAISE(ABORT, 'append-only: a change is a new version, never an update'); END;
CREATE TRIGGER finding_evidence_is_append_only_delete BEFORE DELETE ON finding_evidence
BEGIN SELECT RAISE(ABORT, 'append-only: a version is retracted, never deleted'); END;

CREATE TRIGGER audit_event_is_append_only_update BEFORE UPDATE ON audit_event
BEGIN SELECT RAISE(ABORT, 'append-only: the audit trail is never rewritten'); END;
CREATE TRIGGER audit_event_is_append_only_delete BEFORE DELETE ON audit_event
BEGIN SELECT RAISE(ABORT, 'append-only: the audit trail is never erased'); END;

-- Versions arrive one at a time, in order, starting at 1. Without this a
-- caller could insert version 7 of a record that has only ever had version 1,
-- and every "current version" read would silently skip the five in between.
CREATE TRIGGER record_version_is_sequential BEFORE INSERT ON record_version
WHEN NEW.version <> 1 + COALESCE(
    (SELECT MAX(version) FROM record_version WHERE id = NEW.id), 0
)
BEGIN
    SELECT RAISE(ABORT, 'append-only: versions are consecutive and start at 1');
END;
