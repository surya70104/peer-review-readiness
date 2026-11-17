"""One-click evidence pack: the firm's Peer Review Readiness dossier as a ZIP.

Everything is read from the live records at the moment of the request and
nothing is written. Each file is listed in README_manifest.txt with its row
count, size and SHA-256, so a recipient can check the pack is intact.
"""
import csv
import hashlib
import io
import json
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal

from app.services import applicability, profile_service
from app.services import readiness_service, registers_service, review_service, staff_service

CSV_BOM = "﻿"   # so Excel reads the UTF-8 correctly


# ================================================================== encoding helpers
def _json_default(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (set, tuple)):
        return list(v)
    raise TypeError(f"cannot serialise {type(v).__name__}")


def _json(obj) -> bytes:
    return (json.dumps(obj, indent=2, default=_json_default, ensure_ascii=False) + "\n").encode("utf-8")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v      # a recipient opening the CSV in a spreadsheet must not run a formula
    return v


def _csv(columns: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        w.writerow([_cell(r.get(c)) for c in columns])
    return (CSV_BOM + buf.getvalue()).encode("utf-8")


# ================================================================== the pack
def build_pack(cur, now: datetime | None = None) -> tuple[list[tuple[str, bytes]], dict]:
    """Return ([(name, bytes), ...] including the manifest, info)."""
    now = now or datetime.now(timezone.utc)
    as_of = date.today()
    files: list[tuple[str, bytes, str]] = []   # name, data, what the row count counts

    def add(name, data, rows, unit):
        files.append((name, data, rows, unit))

    # 01, 02: standing
    profile = profile_service.get_profile(cur)
    history = []
    if profile:
        view = profile_service.build_view(cur, profile, as_of)
        history = view.pop("history")
        add("01_standing_profile.json", _json({"generated_at": now, "as_of": as_of, **view}), 1, "profile")
    else:
        add("01_standing_profile.json", _json({"generated_at": now, "as_of": as_of, "profile": None,
                                               "note": "No firm profile has been recorded."}), 0, "profile")
    add("02_applicability_history.csv",
        _csv(["changed_at", "old_phase", "new_phase", "old_basis", "basis", "partner_count"], history),
        len(history), "history rows")

    # 03, 04: assurance register
    assurance = registers_service.list_assurance(cur)
    eng_cols = ["engagement", "client", "service", "entity_category", "partner", "period_from", "period_to",
                "fee", "report_date", "udin"]
    add("03_assurance_register.csv", _csv(eng_cols, assurance["engagements"]), len(assurance["engagements"]), "engagements")
    members = assurance["summary"]["audits_per_member"]
    add("04_audits_per_member.csv", _csv(["partner", "engagements", "total_fees"], members), len(members), "partners")

    # 05: SQC 1
    sqc = registers_service.list_sqc(cur)["policies"]
    add("05_sqc1_policies.csv",
        _csv(["area", "policy_version", "effective_from", "acknowledged_by", "acknowledged_at", "evidence_of_operation"], sqc),
        len(sqc), "policies")

    # 06: staff
    staff = staff_service.list_staff(cur)["records"]
    staff_rows = [{**r, "month": r["month"].strftime("%Y-%m"), "compliant": r["compliant"],
                   "open_flags": "; ".join(f["label"] for f in r["flags"])} for r in staff]
    add("06_staff_compliance.csv",
        _csv(["person", "role", "registration_no", "month", "working_days", "attendance_days", "diary_submitted",
              "stipend_amount", "stipend_paid_at", "stipend_mode", "training_hours", "training_required",
              "compliant", "open_flags", "notes"], staff_rows), len(staff_rows), "records")

    # 07, 08: readiness
    sc = readiness_service.scorecard(cur, None, as_of)
    add("07_readiness_scorecard.json", _json({"generated_at": now, **sc}), len(sc["engagements"]), "engagements scored")
    sample = readiness_service.likely_sample(cur, None, None, as_of)["sample"]
    sample_rows = [{**s, "open_gaps": "; ".join(g["message"] for g in s["gaps"]),
                    "reasons": " | ".join(s["reasons"])} for s in sample]
    add("08_likely_sample.csv",
        _csv(["rank", "engagement", "client", "service", "partner", "entity_category", "fee", "file_date",
              "risk_score", "readiness_score", "status", "open_gaps", "later_addition_count", "reasons"], sample_rows),
        len(sample_rows), "ranked engagements")

    # 09: review cycles and findings
    cycles = review_service.list_cycles(cur, as_of)
    add("09_review_cycles_and_findings.json", _json({"generated_at": now, **cycles}),
        len(cycles["cycles"]), "cycles")

    # manifest
    firm = profile["firm"] if profile else "(no firm profile recorded)"
    ev = applicability.evaluate(profile, as_of) if profile else None
    lines = [
        "OAK Audit - Peer Review Readiness evidence pack",
        "=" * 47,
        f"Generated:        {now:%Y-%m-%d %H:%M} UTC",
        f"Figures as at:    {as_of.isoformat()}",
        f"Firm:             {firm}",
        f"Applicable phase: {ev['basis'] if ev else 'not evaluated'}",
    ]
    if profile:
        lines += [f"Certificate:      {profile['certificate_no'] or '-'} (expires {profile['expires_on'] or '-'})",
                  f"Review must start by: {profile['start_by'] or '-'}"]
    w = sc["window"]
    lines += [f"Review window:    {w['from']} to {w['to']} (selection year {w['selection_year']}; {w['source']})", "",
              "Files", "-----"]
    entries = []
    for name, data, rows, unit in files:
        entries.append({"name": name, "rows": rows, "unit": unit, "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
        lines.append(f"{name}")
        lines.append(f"    {rows} {unit}, {len(data)} bytes")
        lines.append(f"    sha256 {entries[-1]['sha256']}")
    lines += ["", "Notes", "-----",
              "- Generated from the live records at the moment of export. Nothing was edited to produce it.",
              "- Scores cover these engagement dimensions only: " + ", ".join(readiness_service.ASSESSED) + ".",
              "  Not assessed (no evidence captured yet): " + ", ".join(readiness_service.NOT_ASSESSED) + ".",
              "- Documented later additions are listed beside each engagement in file 07, with their date and reason.",
              "  They never change a contemporaneous score.",
              "- Rank is not a prediction of the reviewer's selection. The same records give the same ranking.",
              "- README_manifest.txt itself is not hashed above."]
    manifest = ("\n".join(lines) + "\n").encode("utf-8")
    out = [(n, d) for n, d, _r, _u in files] + [("README_manifest.txt", manifest)]
    return out, {"firm": firm, "files": entries, "generated_at": now, "as_of": as_of}


# ================================================================== streaming
class _Sink(io.RawIOBase):
    """An unseekable file that just collects what the ZIP writer produces."""

    def __init__(self):
        self.chunks: list[bytes] = []

    def writable(self):
        return True

    def write(self, b):
        self.chunks.append(bytes(b))
        return len(b)

    def drain(self):
        out, self.chunks = self.chunks, []
        return b"".join(out)


def stream_zip(files: list[tuple[str, bytes]], now: datetime):
    """Yield the ZIP archive in chunks as each file is added."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            info = zipfile.ZipInfo(name, date_time=now.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
            chunk = sink.drain()
            if chunk:
                yield chunk
    tail = sink.drain()     # the central directory is written when the archive closes
    if tail:
        yield tail


def pack_filename(now: datetime) -> str:
    return f"peer-review-evidence-pack-{now:%Y%m%d-%H%M}.zip"
