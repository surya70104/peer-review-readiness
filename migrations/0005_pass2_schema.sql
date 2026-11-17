-- OAK Audit · Peer Review Readiness
-- Migration 0005 · Pass 2 (Readiness) tables, spec Section 8:
--   staff_compliance  (Stage 5, live now)
--   pr_cycle, pr_finding  (review tracker, Stage 7)
--   eng_score  (engagement scoring and sample rank, Stage 6)
-- Nothing in Pass 1 is altered.

-- Keeps updated_at honest whatever path writes the row.
CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------
-- staff_compliance: one row per person per month (spec: person · type ·
-- month · attendance · diary_submitted · stipend_amount · stipend_paid_at
-- · mode · training_hours). Council directions on articled and audit
-- assistants: attendance register, work diary, stipend and its mode of
-- payment, plus training records by person and period.
-- ---------------------------------------------------------------
CREATE TABLE staff_compliance (
    id                BIGSERIAL PRIMARY KEY,
    person            TEXT NOT NULL CHECK (btrim(person) <> ''),
    role              TEXT NOT NULL
                      CHECK (role IN ('articled_assistant', 'audit_assistant', 'audit_staff')),
    registration_no   TEXT,                       -- articleship registration / employee ID
    month             DATE NOT NULL CHECK (month = date_trunc('month', month)::date),
    working_days      SMALLINT NOT NULL CHECK (working_days BETWEEN 0 AND 31),
    attendance_days   SMALLINT NOT NULL CHECK (attendance_days >= 0),
    diary_submitted   BOOLEAN NOT NULL DEFAULT FALSE,
    stipend_amount    NUMERIC(12, 2) CHECK (stipend_amount IS NULL OR stipend_amount >= 0),
    stipend_paid_at   DATE,                       -- NULL means not paid
    stipend_mode      TEXT CHECK (stipend_mode IN ('bank_transfer', 'cheque', 'cash', 'upi', 'other')),
    training_hours    NUMERIC(6, 2) NOT NULL DEFAULT 0 CHECK (training_hours >= 0),
    training_required NUMERIC(6, 2) NOT NULL DEFAULT 0 CHECK (training_required >= 0),
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT staff_attendance_within_working CHECK (attendance_days <= working_days),
    -- A payment record names an amount and how it was paid.
    CONSTRAINT staff_paid_has_amount CHECK (stipend_paid_at IS NULL OR stipend_amount IS NOT NULL),
    CONSTRAINT staff_paid_has_mode   CHECK (stipend_paid_at IS NULL OR stipend_mode IS NOT NULL)
);
-- One record per person per month, ignoring case and stray spaces.
CREATE UNIQUE INDEX staff_compliance_person_month_uq
    ON staff_compliance (lower(btrim(person)), month);
CREATE INDEX staff_compliance_month_idx ON staff_compliance (month DESC);
CREATE INDEX staff_compliance_role_idx  ON staff_compliance (role);
CREATE TRIGGER staff_compliance_updated BEFORE UPDATE ON staff_compliance
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------
-- pr_cycle: a live review cycle (spec section 6: the two fifteen-day
-- clocks run from consent_at and findings_at). Dates are calendar dates.
-- ---------------------------------------------------------------
CREATE TABLE pr_cycle (
    id                    SERIAL PRIMARY KEY,
    profile_id            INTEGER NOT NULL REFERENCES pr_profile(id) ON DELETE RESTRICT,
    selection_year        INTEGER NOT NULL CHECK (selection_year BETWEEN 2020 AND 2100),
    period_from           DATE NOT NULL,          -- the three years before the selection year
    period_to             DATE NOT NULL,
    reviewer              TEXT,
    consent_at            DATE,                   -- clock 1 starts here
    questionnaire_due     DATE,
    questionnaire_sent    DATE,
    visit_at              DATE,
    findings_at           DATE,                   -- clock 2 starts here
    response_due          DATE,
    response_sent         DATE,
    preliminary_report_at DATE,
    final_report_at       DATE,
    outcome               TEXT CHECK (outcome IN ('clean', 'modified')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pr_cycle_unique_year UNIQUE (profile_id, selection_year),
    CONSTRAINT pr_cycle_period_order CHECK (period_to >= period_from),
    CONSTRAINT pr_cycle_findings_after_consent CHECK (
        findings_at IS NULL OR consent_at IS NULL OR findings_at >= consent_at),
    CONSTRAINT pr_cycle_prelim_after_findings CHECK (
        preliminary_report_at IS NULL OR findings_at IS NULL OR preliminary_report_at >= findings_at),
    CONSTRAINT pr_cycle_final_after_prelim CHECK (
        final_report_at IS NULL OR preliminary_report_at IS NULL OR final_report_at >= preliminary_report_at)
);
CREATE TRIGGER pr_cycle_updated BEFORE UPDATE ON pr_cycle
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------
-- pr_finding: findings of non-compliance, the firm's response, and the
-- corrective action with its owner.
-- ---------------------------------------------------------------
CREATE TABLE pr_finding (
    id                BIGSERIAL PRIMARY KEY,
    cycle_id          INTEGER NOT NULL REFERENCES pr_cycle(id) ON DELETE RESTRICT,
    ref               TEXT NOT NULL CHECK (btrim(ref) <> ''),
    control_area      TEXT NOT NULL,
    description       TEXT NOT NULL,
    engagement        TEXT REFERENCES assurance_register(engagement)
                      ON UPDATE CASCADE ON DELETE RESTRICT,
    pu_response       TEXT,                       -- the practice unit's written response
    responded_at      DATE,
    resolution        TEXT,
    corrective_action TEXT,
    owner             TEXT,
    closed_at         DATE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pr_finding_unique_ref UNIQUE (cycle_id, ref),
    CONSTRAINT pr_finding_response_pair CHECK ((pu_response IS NULL) = (responded_at IS NULL))
);
CREATE INDEX pr_finding_cycle_idx ON pr_finding (cycle_id);
CREATE TRIGGER pr_finding_updated BEFORE UPDATE ON pr_finding
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------
-- eng_score: rule-based score per engagement, per rolling window, per
-- dimension, with named gaps. later_additions holds documented later
-- additions (each with its date and reason); they are never merged into
-- the contemporaneous record. sample_rank / rank_drivers describe the
-- engagement as a whole, so by convention they live on the 'overall' row.
-- ---------------------------------------------------------------
CREATE TABLE eng_score (
    id                BIGSERIAL PRIMARY KEY,
    engagement        TEXT NOT NULL REFERENCES assurance_register(engagement)
                      ON UPDATE CASCADE ON DELETE RESTRICT,
    window_from       DATE NOT NULL,
    window_to         DATE NOT NULL,
    dimension         TEXT NOT NULL CHECK (dimension IN (
                          'SA 210', 'SA 230', 'SA 300', 'SA 580', 'SA 700',
                          'independence', 'UDIN', 'overall')),
    score             NUMERIC(5, 2) NOT NULL CHECK (score BETWEEN 0 AND 100),
    gaps              JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(gaps) = 'array'),
    later_additions   JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(later_additions) = 'array'),
    sample_rank       INTEGER CHECK (sample_rank IS NULL OR sample_rank >= 1),
    rank_drivers      JSONB CHECK (rank_drivers IS NULL OR jsonb_typeof(rank_drivers) IN ('array', 'object')),
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT eng_score_window_order CHECK (window_to >= window_from),
    CONSTRAINT eng_score_unique UNIQUE (engagement, window_from, window_to, dimension)
);
CREATE INDEX eng_score_rank_idx ON eng_score (window_from, window_to, sample_rank)
    WHERE sample_rank IS NOT NULL;
CREATE TRIGGER eng_score_updated BEFORE UPDATE ON eng_score
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
