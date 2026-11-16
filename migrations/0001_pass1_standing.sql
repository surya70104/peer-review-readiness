-- OAK Audit · Peer Review Readiness
-- Migration 0001 · Pass 1 (Standing) tables, spec Section 8

-- ---------------------------------------------------------------
-- pr_profile: one row per practice unit (firm)
-- ---------------------------------------------------------------
CREATE TABLE pr_profile (
    id                  SERIAL PRIMARY KEY,
    firm                TEXT NOT NULL UNIQUE,
    -- Level I: central statutory audits of banks/insurers/listed/large PIEs (3-year cycle)
    -- Level II: all other practice units (4-year cycle)
    level               SMALLINT NOT NULL DEFAULT 2 CHECK (level IN (1, 2)),
    partner_count       INTEGER  NOT NULL DEFAULT 0 CHECK (partner_count >= 0),

    -- Applicability (0 = none applicable yet, 1..4 = Phase I..IV)
    phase_applicable    SMALLINT NOT NULL DEFAULT 0 CHECK (phase_applicable BETWEEN 0 AND 4),
    phase_basis         TEXT,
    basis_changed_at    TIMESTAMPTZ,

    -- Phase trigger inputs (the applicability engine reads these in a later stage)
    audits_listed_entities      BOOLEAN NOT NULL DEFAULT FALSE,  -- Phase I
    audits_large_unlisted_public BOOLEAN NOT NULL DEFAULT FALSE, -- Phase II (paid-up >= 500cr / turnover >= 1000cr / loans >= 500cr)
    audits_entities_raised_50cr BOOLEAN NOT NULL DEFAULT FALSE,  -- Phase III (raised > 50cr from public/banks/FIs)
    audits_psb_branches         BOOLEAN NOT NULL DEFAULT FALSE,  -- Phase IV (Public Sector Bank branches)
    renders_attestation         BOOLEAN NOT NULL DEFAULT TRUE,   -- partner ladder 5 / 4 / 3 applies to attestation units

    -- Certificate state: read from the certificate itself, never derived
    certificate_no      TEXT,
    effective_from      DATE,
    expires_on          DATE,

    -- Last review outcome and follow-on
    last_report_type    TEXT CHECK (last_report_type IN ('clean', 'modified')),
    follow_on_due       DATE,

    -- Computed backward from expires_on (spec section 2)
    start_by            DATE,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pr_profile_cert_dates CHECK (
        effective_from IS NULL OR expires_on IS NULL OR expires_on > effective_from
    )
);

-- ---------------------------------------------------------------
-- pr_applicability_history: append-only log of every change to
-- phase_applicable or phase_basis (spec section 1, acceptance test 1)
-- ---------------------------------------------------------------
CREATE TABLE pr_applicability_history (
    id              BIGSERIAL PRIMARY KEY,
    profile_id      INTEGER NOT NULL REFERENCES pr_profile(id) ON DELETE CASCADE,
    old_phase       SMALLINT,
    new_phase       SMALLINT NOT NULL,
    old_basis       TEXT,
    basis           TEXT,
    partner_count   INTEGER,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX pr_applicability_history_profile_idx
    ON pr_applicability_history (profile_id, changed_at DESC);

-- History is append-only: no edits, no deletes of individual rows.
CREATE FUNCTION pr_applicability_history_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'pr_applicability_history is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER pr_applicability_history_no_update
    BEFORE UPDATE ON pr_applicability_history
    FOR EACH ROW EXECUTE FUNCTION pr_applicability_history_immutable();

-- UPDATE: log any change to phase_applicable / phase_basis and stamp
-- basis_changed_at, whatever application path made the change.
CREATE FUNCTION pr_profile_before_update() RETURNS trigger AS $$
BEGIN
    IF NEW.phase_applicable IS DISTINCT FROM OLD.phase_applicable
       OR NEW.phase_basis   IS DISTINCT FROM OLD.phase_basis THEN
        NEW.basis_changed_at := now();
        INSERT INTO pr_applicability_history
            (profile_id, old_phase, new_phase, old_basis, basis, partner_count, changed_at)
        VALUES (OLD.id, OLD.phase_applicable, NEW.phase_applicable, OLD.phase_basis,
                NEW.phase_basis, NEW.partner_count, NEW.basis_changed_at);
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER pr_profile_before_update
    BEFORE UPDATE ON pr_profile
    FOR EACH ROW EXECUTE FUNCTION pr_profile_before_update();

-- INSERT: stamp basis_changed_at (BEFORE), then log the initial state (AFTER,
-- so the FK to the new row is valid).
CREATE FUNCTION pr_profile_before_insert() RETURNS trigger AS $$
BEGIN
    NEW.basis_changed_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION pr_profile_after_insert() RETURNS trigger AS $$
BEGIN
    INSERT INTO pr_applicability_history
        (profile_id, old_phase, new_phase, old_basis, basis, partner_count, changed_at)
    VALUES (NEW.id, NULL, NEW.phase_applicable, NULL, NEW.phase_basis,
            NEW.partner_count, NEW.basis_changed_at);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER pr_profile_before_insert
    BEFORE INSERT ON pr_profile
    FOR EACH ROW EXECUTE FUNCTION pr_profile_before_insert();

CREATE TRIGGER pr_profile_after_insert
    AFTER INSERT ON pr_profile
    FOR EACH ROW EXECUTE FUNCTION pr_profile_after_insert();

-- ---------------------------------------------------------------
-- assurance_register: the standing list the questionnaire needs
-- ---------------------------------------------------------------
CREATE TABLE assurance_register (
    id            BIGSERIAL PRIMARY KEY,
    engagement    TEXT NOT NULL UNIQUE,
    client        TEXT NOT NULL,
    service       TEXT NOT NULL,
    period_from   DATE,
    period_to     DATE,
    partner       TEXT NOT NULL,
    fee           NUMERIC(14, 2) CHECK (fee IS NULL OR fee >= 0),
    report_date   DATE,
    udin          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT assurance_period_order CHECK (
        period_from IS NULL OR period_to IS NULL OR period_to >= period_from
    )
);
CREATE INDEX assurance_register_partner_idx ON assurance_register (partner);
CREATE INDEX assurance_register_report_date_idx ON assurance_register (report_date);

-- ---------------------------------------------------------------
-- sqc_policy: SQC 1 policies and evidence they operated
-- ---------------------------------------------------------------
CREATE TABLE sqc_policy (
    id                    BIGSERIAL PRIMARY KEY,
    area                  TEXT NOT NULL,
    policy_version        TEXT NOT NULL,
    effective_from        DATE NOT NULL,
    acknowledged_by       TEXT,
    acknowledged_at       TIMESTAMPTZ,
    evidence_of_operation TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT sqc_policy_ack_pair CHECK (
        (acknowledged_by IS NULL) = (acknowledged_at IS NULL)
    )
);
CREATE INDEX sqc_policy_area_idx ON sqc_policy (area, effective_from DESC);
