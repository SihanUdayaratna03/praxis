-- 002_search: full-text search, kept in step by the database rather than by
-- whoever remembered to call the indexer.
--
-- ADR 0003 puts search inside the store instead of beside it. That only pays
-- off if the index cannot drift from the rows, so the index is populated by
-- AFTER INSERT triggers: a record that exists is a record that is searchable,
-- and there is no code path that writes one without the other.
--
-- One FTS5 table over every searchable field of every kind, rather than one per
-- table. A reporter's question is "where was this said", not "where in a
-- decision was this said", and a single index answers it with a single MATCH.
--
-- Nothing is ever removed from the index, because nothing is ever removed from
-- the store. Superseded versions stay in it and are filtered out by joining
-- `record_head`, which keeps the index append-only like everything else.

CREATE VIRTUAL TABLE search USING fts5(
    body,
    record_id UNINDEXED,
    kind      UNINDEXED,
    version   UNINDEXED,
    field     UNINDEXED,
    -- remove_diacritics 2 is the Unicode-correct setting; the default (1) is
    -- kept only for backwards compatibility and mishandles some codepoints.
    -- https://www.sqlite.org/fts5.html#unicode61_tokenizer
    tokenize = 'unicode61 remove_diacritics 2'
);

-- Each trigger projects one record into (field, body) pairs and drops the ones
-- that are absent or blank. Written as a SELECT over a UNION rather than as a
-- run of INSERT statements so an optional field costs a row in a list instead
-- of a second trigger with its own WHEN clause.

CREATE TRIGGER document_is_searchable AFTER INSERT ON document
BEGIN
    INSERT INTO search (body, record_id, kind, version, field)
    SELECT body, NEW.id, 'document', NEW.version, field
    FROM (SELECT NEW.title AS body, 'title' AS field
          UNION ALL SELECT NEW.content, 'content')
    WHERE body IS NOT NULL AND trim(body) <> '';
END;

CREATE TRIGGER span_is_searchable AFTER INSERT ON span
BEGIN
    INSERT INTO search (body, record_id, kind, version, field)
    SELECT NEW.text, NEW.id, 'span', NEW.version, 'text'
    WHERE trim(NEW.text) <> '';
END;

CREATE TRIGGER decision_is_searchable AFTER INSERT ON decision
BEGIN
    INSERT INTO search (body, record_id, kind, version, field)
    SELECT body, NEW.id, 'decision', NEW.version, field
    FROM (SELECT NEW.title AS body, 'title' AS field
          UNION ALL SELECT NEW.chosen, 'chosen'
          -- The rejected options are searchable too: "why not X" is the
          -- question ArchaeologistAgent exists to answer, and the answer is
          -- in the reasons, not in the chosen option.
          UNION ALL SELECT (SELECT group_concat(json_extract(value, '$.option') || ' ' ||
                                                json_extract(value, '$.reason'), ' ')
                            FROM json_each(NEW.rejected)), 'rejected')
    WHERE body IS NOT NULL AND trim(body) <> '';
END;

CREATE TRIGGER assumption_is_searchable AFTER INSERT ON assumption
BEGIN
    INSERT INTO search (body, record_id, kind, version, field)
    SELECT body, NEW.id, 'assumption', NEW.version, field
    FROM (SELECT NEW.statement AS body, 'statement' AS field
          UNION ALL SELECT NEW.predicate, 'predicate'
          UNION ALL SELECT NEW.expiry_condition, 'expiry_condition')
    WHERE body IS NOT NULL AND trim(body) <> '';
END;

CREATE TRIGGER estimate_is_searchable AFTER INSERT ON estimate
BEGIN
    INSERT INTO search (body, record_id, kind, version, field)
    SELECT body, NEW.id, 'estimate', NEW.version, field
    FROM (SELECT NEW.subject AS body, 'subject' AS field
          UNION ALL SELECT (SELECT group_concat(value, ' ') FROM json_each(NEW.conditions)),
                    'conditions')
    WHERE body IS NOT NULL AND trim(body) <> '';
END;

CREATE TRIGGER outcome_is_searchable AFTER INSERT ON outcome
BEGIN
    INSERT INTO search (body, record_id, kind, version, field)
    SELECT NEW.notes, NEW.id, 'outcome', NEW.version, 'notes'
    WHERE trim(NEW.notes) <> '';
END;

CREATE TRIGGER finding_is_searchable AFTER INSERT ON finding
BEGIN
    INSERT INTO search (body, record_id, kind, version, field)
    SELECT body, NEW.id, 'finding', NEW.version, field
    FROM (SELECT NEW.prosecution AS body, 'prosecution' AS field
          UNION ALL SELECT NEW.challenge, 'challenge')
    WHERE body IS NOT NULL AND trim(body) <> '';
END;
