// Screen 4 · Registers: add / edit engagements, add / record SQC policies, CSV export.
(function () {
  const { $, api, showError, flashThenReload, blankToNull, toast } = window.OAK;

  // Wire a dialog form: build(form) returns {method, url, body, message} or a string error.
  function wire(id, build) {
    const form = $(id);
    const dialog = form.closest("dialog");
    const errBox = $(".form-error", form);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      errBox.hidden = true;
      const req = build(form);
      if (typeof req === "string") return showError(errBox, req);
      const btn = form.querySelector("button.primary");
      btn.disabled = true;
      const r = await api(req.method, req.url, req.body);
      btn.disabled = false;
      if (!r.ok) return showError(errBox, r.data);
      dialog.close();
      form.reset();
      flashThenReload(req.message);
    });
  }

  // ---- assurance: add
  wire("#add-engagement-form", (f) => {
    const body = {};
    for (const k of ["engagement", "client", "service", "partner"]) {
      body[k] = f[k].value.trim();
      if (!body[k]) return "Engagement code, client, service and partner are required.";
    }
    for (const k of ["period_from", "period_to", "report_date", "udin"]) {
      const v = blankToNull(f[k].value);
      if (v !== null) body[k] = v;
    }
    if (f.fee.value !== "") body.fee = Number(f.fee.value);
    return { method: "POST", url: "/api/registers/assurance", body, message: "Engagement added." };
  });

  // ---- assurance: update report date / UDIN / fee
  document.addEventListener("click", (e) => {
    const b = e.target.closest('[data-open="#edit-engagement"]');
    if (b) $("#ee-label").textContent = JSON.parse(b.dataset.fill).label;
    const c = e.target.closest('[data-open="#record-sqc"]');
    if (c) $("#rs-label").textContent = JSON.parse(c.dataset.fill).label;
  });
  wire("#edit-engagement-form", (f) => ({
    method: "PATCH",
    url: "/api/registers/assurance/" + f.id.value,
    body: {
      report_date: blankToNull(f.report_date.value),
      udin: blankToNull(f.udin.value),
      fee: f.fee.value === "" ? null : Number(f.fee.value),
    },
    message: "Engagement updated.",
  }));

  // ---- SQC: add
  wire("#add-sqc-form", (f) => {
    const body = { area: f.area.value.trim(), policy_version: f.policy_version.value.trim(), effective_from: f.effective_from.value };
    if (!body.area || !body.policy_version || !body.effective_from) return "Area, version and effective date are required.";
    const by = blankToNull(f.acknowledged_by.value);
    const ev = blankToNull(f.evidence_of_operation.value);
    if (by) body.acknowledged_by = by;
    if (ev) body.evidence_of_operation = ev;
    return { method: "POST", url: "/api/registers/sqc", body, message: "Policy added." };
  });

  // ---- SQC: record acknowledgement / evidence (blank fields are left as they are)
  wire("#record-sqc-form", (f) => {
    const body = {};
    const by = blankToNull(f.acknowledged_by.value);
    const ev = blankToNull(f.evidence_of_operation.value);
    if (by) body.acknowledged_by = by;
    if (ev) body.evidence_of_operation = ev;
    if (!Object.keys(body).length) return "Enter who acknowledged it, or the evidence of operation.";
    return { method: "PATCH", url: "/api/registers/sqc/" + f.id.value, body, message: "Policy updated." };
  });

  // ---- CSV export of the list currently shown (same data the questionnaire needs)
  const csvBtn = $("#csv-btn");
  if (csvBtn) csvBtn.addEventListener("click", () => {
    const table = $("#assurance-table");
    const head = [...table.querySelectorAll("thead th")].slice(0, -1).map((th) => th.textContent.trim());
    const rows = [...table.querySelectorAll("tbody tr")]
      .filter((tr) => tr.querySelector("td[data-raw]"))
      .map((tr) => [...tr.querySelectorAll("td[data-raw]")].map((td) => td.dataset.raw));
    if (!rows.length) return toast("Nothing to export.");
    const esc = (v) => '"' + String(v).replace(/"/g, '""') + '"';
    const csv = [head, ...rows].map((r) => r.map(esc).join(",")).join("\r\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
    a.download = "assurance-register-" + new Date().toISOString().slice(0, 10) + ".csv";
    a.click();
    URL.revokeObjectURL(a.href);
  });
})();
