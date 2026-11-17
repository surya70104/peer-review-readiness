-- OAK Audit · Peer Review Readiness
-- Migration 0006 · Stage 6: honesty by construction for later additions,
-- a 'register' scoring dimension, and the entity category the sample
-- selector needs (spec sections 5 and 7).
--
-- Later additions (spec section 7): a documented later addition to a file whose
-- assembly window has closed is shown as what it is, with its date and reason,
-- and is never merged into the contemporaneous record. To make that hold
-- whatever application path writes to the database:
--   * eng_later_addition is the append-only source of truth. Rows cannot be
--     updated or deleted, cannot be back-dated (added_at is stamped by the
--     database), and can only be added once the file's SA 230 assembly window
--     (60 days after the report date) has closed.
--   * eng_score.later_additions is a read-only mirror of that ledger, rebuilt
--     by trigger on every insert and update. Recomputing scores can neither
--     wipe nor overwrite it, because nothing the application writes to that
--     column is kept.

-- ---------------------------------------------------------------
-- 1. Entity category on the assurance register (sample-selection driver).
--    NULL means "not recorded", which the selector reports rather than guesses.
-- ---------------------------------------------------------------
ALTER TABLE assurance_register
    ADD COLUMN entity_category TEXT
    CHECK (entity_category IN ('listed', 'level1_pie', 'large_unlisted_public',
                               'raised_50cr', 'psb_branch', 'other'));

-- ---------------------------------------------------------------
-- 2. 'register' dimension: fee and engagement-metadata completeness.
-- ---------------------------------------------------------------
ALTER TABLE eng_score DROP CONSTRAINT eng_score_dimension_check;
ALTER TABLE eng_score ADD CONSTRAINT eng_score_dimension_check CHECK (dimension IN (
    'SA 210', 'SA 230', 'SA 300', 'SA 580', 'SA 700',
    'independence', 'UDIN', 'register', 'overall'));

-- ---------------------------------------------------------------
-- 3. The append-only ledger of documented later additions.
-- ---------------------------------------------------------------
CREATE TABLE eng_later_addition (
    id           BIGSERIAL PRIMARY KEY,
    engagement   TEXT NOT NULL REFERENCES assurance_register(engagement)
                 ON UPDATE CASCADE ON DELETE RESTRICT,
    dimension    TEXT CHECK (dimension IS NULL OR dimension IN (
                     'SA 210', 'SA 230', 'SA 300', 'SA 580', 'SA 700',
                     'independence', 'UDIN', 'register')),
    description  TEXT NOT NULL CHECK (btrim(description) <> ''),
    reason       TEXT NOT NULL CHECK (btrim(reason) <> ''),
    added_by     TEXT,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX eng_later_addition_engagement_idx ON eng_later_addition (engagement, id);

-- Stamp the time and refuse additions to a file that is not yet closed.
CREATE FUNCTION eng_later_addition_before_insert() RETURNS trigger AS $$
DECLARE
    reported DATE;
BEGIN
    NEW.added_at := clock_timestamp();      -- never taken from the caller
    SELECT report_date INTO reported FROM assurance_register WHERE engagement = NEW.engagement;
    IF reported IS NULL OR current_date <= reported + 60 THEN
        RAISE EXCEPTION 'the assembly window for % has not closed; record this in the file itself, not as a later addition',
            NEW.engagement USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER eng_later_addition_before_insert
    BEFORE INSERT ON eng_later_addition
    FOR EACH ROW EXECUTE FUNCTION eng_later_addition_before_insert();

-- No edits, no deletes, no truncate. The one exception is the foreign key
-- cascading an engagement code rename (trigger depth 2), which changes only the
-- engagement column.
CREATE FUNCTION eng_later_addition_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND pg_trigger_depth() >= 2
       AND (to_jsonb(NEW) - 'engagement') = (to_jsonb(OLD) - 'engagement') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'eng_later_addition is append-only (% refused)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER eng_later_addition_no_update
    BEFORE UPDATE ON eng_later_addition
    FOR EACH ROW EXECUTE FUNCTION eng_later_addition_guard();
CREATE TRIGGER eng_later_addition_no_delete
    BEFORE DELETE ON eng_later_addition
    FOR EACH ROW EXECUTE FUNCTION eng_later_addition_guard();
CREATE TRIGGER eng_later_addition_no_truncate
    BEFORE TRUNCATE ON eng_later_addition
    FOR EACH STATEMENT EXECUTE FUNCTION eng_later_addition_guard();

-- ---------------------------------------------------------------
-- 4. eng_score.later_additions is a mirror of the ledger.
-- ---------------------------------------------------------------
CREATE FUNCTION eng_later_additions_json(names TEXT[]) RETURNS jsonb AS $$
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'id', id, 'dimension', dimension, 'description', description,
               'reason', reason, 'added_by', added_by, 'added_at', added_at) ORDER BY id),
           '[]'::jsonb)
    FROM eng_later_addition WHERE engagement = ANY(names);
$$ LANGUAGE sql STABLE;

-- Only the 'overall' row carries them. On an engagement code rename the ledger
-- may or may not have been cascaded yet, so both names are consulted.
CREATE FUNCTION eng_score_sync_later_additions() RETURNS trigger AS $$
BEGIN
    IF NEW.dimension <> 'overall' THEN
        NEW.later_additions := '[]'::jsonb;
    ELSIF TG_OP = 'INSERT' THEN
        NEW.later_additions := eng_later_additions_json(ARRAY[NEW.engagement]);
    ELSE
        NEW.later_additions := eng_later_additions_json(ARRAY[NEW.engagement, OLD.engagement]);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER eng_score_sync_later_additions
    BEFORE INSERT OR UPDATE ON eng_score
    FOR EACH ROW EXECUTE FUNCTION eng_score_sync_later_additions();

-- A new ledger entry refreshes the mirror on every window's overall row.
CREATE FUNCTION eng_later_addition_refresh_mirror() RETURNS trigger AS $$
BEGIN
    UPDATE eng_score SET updated_at = now()
     WHERE engagement = NEW.engagement AND dimension = 'overall';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER eng_later_addition_refresh_mirror
    AFTER INSERT ON eng_later_addition
    FOR EACH ROW EXECUTE FUNCTION eng_later_addition_refresh_mirror();

-- Rebuild any rows that already exist (none yet in practice).
UPDATE eng_score SET updated_at = now() WHERE dimension = 'overall';
