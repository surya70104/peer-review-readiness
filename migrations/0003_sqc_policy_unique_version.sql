-- Migration 0003 · one row per (area, policy_version); a re-issued policy gets a new version.
CREATE UNIQUE INDEX sqc_policy_area_version_uq ON sqc_policy (area, policy_version);
