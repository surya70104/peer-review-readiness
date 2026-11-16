-- Migration 0002 · Stage 2 support
--  * last_report_date: follow-on review (6-12 months) is measured from the report date
--  * pr_change_ts(): lets the application record WHEN an applicability change took
--    effect (e.g. the date a partner was admitted) instead of the time of data entry.
--    The app sets the transaction-local setting 'app.change_at'; otherwise now() is used.

ALTER TABLE pr_profile ADD COLUMN last_report_date DATE;

ALTER TABLE pr_profile ADD CONSTRAINT pr_profile_report_needs_date
    CHECK (last_report_type IS NULL OR last_report_date IS NOT NULL);

CREATE FUNCTION pr_change_ts() RETURNS timestamptz AS $$
    SELECT COALESCE(NULLIF(current_setting('app.change_at', true), '')::timestamptz, now());
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION pr_profile_before_update() RETURNS trigger AS $$
BEGIN
    IF NEW.phase_applicable IS DISTINCT FROM OLD.phase_applicable
       OR NEW.phase_basis   IS DISTINCT FROM OLD.phase_basis THEN
        NEW.basis_changed_at := pr_change_ts();
        INSERT INTO pr_applicability_history
            (profile_id, old_phase, new_phase, old_basis, basis, partner_count, changed_at)
        VALUES (OLD.id, OLD.phase_applicable, NEW.phase_applicable, OLD.phase_basis,
                NEW.phase_basis, NEW.partner_count, NEW.basis_changed_at);
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION pr_profile_before_insert() RETURNS trigger AS $$
BEGIN
    NEW.basis_changed_at := pr_change_ts();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
