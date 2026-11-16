-- Migration 0004 · make pr_applicability_history genuinely append-only.
-- 0001 blocked UPDATE only. Block direct DELETE and TRUNCATE too.
-- A DELETE that arrives through the ON DELETE CASCADE from pr_profile runs
-- inside the foreign-key trigger (pg_trigger_depth() >= 2) and is still allowed,
-- so removing a profile removes its own history; nothing else can.

CREATE FUNCTION pr_applicability_history_no_delete() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' OR pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'pr_applicability_history is append-only';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER pr_applicability_history_no_delete
    BEFORE DELETE ON pr_applicability_history
    FOR EACH ROW EXECUTE FUNCTION pr_applicability_history_no_delete();

CREATE TRIGGER pr_applicability_history_no_truncate
    BEFORE TRUNCATE ON pr_applicability_history
    FOR EACH STATEMENT EXECUTE FUNCTION pr_applicability_history_no_delete();
