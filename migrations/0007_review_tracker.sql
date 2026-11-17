-- OAK Audit · Peer Review Readiness
-- Migration 0007 · Stage 7: review tracker (spec sections 6 and 9, Screen 5).
-- Adds what the live cycle needs on top of the Stage 5 tables:
--   pr_cycle    intimation_at; an outcome always comes with a final report date;
--               a cycle cannot be closed while any finding is still open
--   pr_finding  severity, due_at, evidence_ref; a resolved finding is complete

-- ---------------------------------------------------------------
-- pr_cycle
-- ---------------------------------------------------------------
ALTER TABLE pr_cycle ADD COLUMN intimation_at DATE;   -- date the Board's selection letter arrived

ALTER TABLE pr_cycle ADD CONSTRAINT pr_cycle_final_has_outcome
    CHECK ((final_report_at IS NULL) = (outcome IS NULL));
ALTER TABLE pr_cycle ADD CONSTRAINT pr_cycle_consent_after_intimation
    CHECK (intimation_at IS NULL OR consent_at IS NULL OR consent_at >= intimation_at);
ALTER TABLE pr_cycle ADD CONSTRAINT pr_cycle_questionnaire_after_consent
    CHECK (questionnaire_sent IS NULL OR consent_at IS NULL OR questionnaire_sent >= consent_at);
ALTER TABLE pr_cycle ADD CONSTRAINT pr_cycle_response_after_findings
    CHECK (response_sent IS NULL OR findings_at IS NULL OR response_sent >= findings_at);

-- Closing a cycle (a final report date) is refused while any finding is open,
-- whatever application path attempts it.
CREATE FUNCTION pr_cycle_close_guard() RETURNS trigger AS $$
BEGIN
    IF NEW.final_report_at IS NOT NULL AND EXISTS (
        SELECT 1 FROM pr_finding WHERE cycle_id = NEW.id AND closed_at IS NULL) THEN
        RAISE EXCEPTION 'cycle % cannot be closed while findings are still open', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER pr_cycle_close_guard
    BEFORE UPDATE ON pr_cycle
    FOR EACH ROW EXECUTE FUNCTION pr_cycle_close_guard();

-- ---------------------------------------------------------------
-- pr_finding
-- ---------------------------------------------------------------
ALTER TABLE pr_finding
    ADD COLUMN severity     TEXT CHECK (severity IN ('high', 'medium', 'low')),
    ADD COLUMN due_at       DATE,                 -- firm's response due; defaults to the cycle's response clock
    ADD COLUMN evidence_ref TEXT;                 -- where the evidence of correction is kept

-- Resolved means the firm responded, said what it did, and recorded the corrective action.
ALTER TABLE pr_finding ADD CONSTRAINT pr_finding_resolved_complete CHECK (
    closed_at IS NULL OR (pu_response IS NOT NULL AND resolution IS NOT NULL AND corrective_action IS NOT NULL));
ALTER TABLE pr_finding ADD CONSTRAINT pr_finding_closed_after_response CHECK (
    closed_at IS NULL OR responded_at IS NULL OR closed_at >= responded_at);

CREATE INDEX pr_finding_open_idx ON pr_finding (cycle_id) WHERE closed_at IS NULL;
