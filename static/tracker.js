// Screen 5 · Review tracker: start a cycle, record milestones, close it, add and resolve findings.
(function () {
  const { $, api, showError, toast, flashThenReload, blankToNull } = window.OAK;

  // Remember what a dialog was opened with, so only real changes are sent.
  const initial = new WeakMap();
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-open][data-fill]");
    if (!b) return;
    const v = JSON.parse(b.dataset.fill);
    const dlg = $(b.dataset.open);
    if (!dlg) return;
    initial.set(dlg, v);
    if (b.dataset.open === "#milestones") $("#ms-label").textContent = v.label;
    if (b.dataset.open === "#close-cycle") { $("#cc-label").textContent = v.label; $("#cc-note").textContent = v.note; }
    if (b.dataset.open === "#add-finding") $("#af-label").textContent = v.label;
    if (b.dataset.open === "#finding-response") {
      $("#fr-label").textContent = v.label;
      $("#fr-desc").textContent = v.description;
      const f = $("#finding-response-form");
      f.resolved.checked = false;
      f.resolved_on.value = "";
    }
  });

  // Wire a dialog form: build(form, initial) returns {method, url, body, message} or a string error.
  function wire(id, build) {
    const form = $(id);
    if (!form) return;
    const dialog = form.closest("dialog");
    const errBox = $(".form-error", form);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      errBox.hidden = true;
      const req = build(form, initial.get(dialog) || {});
      if (typeof req === "string") return showError(errBox, req);
      const btn = form.querySelector("button.primary");
      btn.disabled = true;
      const r = await api(req.method, req.url, req.body);
      btn.disabled = false;
      if (!r.ok) return showError(errBox, r.data);
      dialog.close();
      form.reset();
      flashThenReload(typeof req.message === "function" ? req.message(r.data) : req.message);
    });
  }

  // Only fields the user changed, and never a blank over a recorded value.
  function changed(form, was, fields) {
    const body = {};
    for (const k of fields) {
      const v = blankToNull(form[k].value);
      if (v !== null && v !== (was[k] || "")) body[k] = v;
    }
    return body;
  }

  // ---- start a cycle
  wire("#new-cycle-form", (f) => {
    const year = Number(f.selection_year.value);
    if (!year) return "Enter the selection year.";
    const body = { selection_year: year };
    for (const k of ["reviewer", "intimation_at", "consent_at"]) {
      const v = blankToNull(f[k].value);
      if (v !== null) body[k] = v;
    }
    return { method: "POST", url: "/api/reviews/cycles", body, message: "Review cycle started." };
  });

  // ---- milestones
  wire("#milestones-form", (f, was) => {
    const body = changed(f, was, ["reviewer", "intimation_at", "consent_at", "questionnaire_due", "questionnaire_sent",
      "visit_at", "findings_at", "response_due", "response_sent", "preliminary_report_at"]);
    if (!Object.keys(body).length) return "Nothing has changed.";
    return { method: "PATCH", url: "/api/reviews/cycles/" + f.id.value, body, message: "Milestones saved." };
  });

  // ---- close the cycle
  const outcome = $("#close-cycle-form") && $("#close-cycle-form").outcome;
  if (outcome) outcome.addEventListener("change", () => {
    $("#fo-wrap").style.display = outcome.value === "modified" ? "" : "none";
  });
  wire("#close-cycle-form", (f) => {
    if (!f.final_report_at.value || !f.outcome.value) return "Enter the final report date and its outcome.";
    const body = { final_report_at: f.final_report_at.value, outcome: f.outcome.value };
    if (f.outcome.value === "modified") body.follow_on_months = Number(f.follow_on_months.value);
    return {
      method: "PATCH", url: "/api/reviews/cycles/" + f.id.value, body,
      message: (d) => "Cycle closed. " + (d.profile_note || ""),
    };
  });

  // ---- findings
  wire("#add-finding-form", (f) => {
    const body = { control_area: f.control_area.value.trim(), description: f.description.value.trim() };
    if (!body.control_area || !body.description) return "Enter the control area and what the reviewer found.";
    for (const k of ["engagement", "severity", "due_at", "owner", "ref"]) {
      const v = blankToNull(f[k].value);
      if (v !== null) body[k] = v;
    }
    return { method: "POST", url: "/api/reviews/cycles/" + f.id.value + "/findings", body, message: "Finding added." };
  });

  wire("#finding-response-form", (f, was) => {
    const body = changed(f, was, ["pu_response", "responded_at", "corrective_action", "resolution", "evidence_ref", "owner", "due_at"]);
    if (f.resolved.checked) {
      body.resolved = true;
      const on = blankToNull(f.resolved_on.value);
      if (on) body.resolved_on = on;
    }
    if (!Object.keys(body).length) return "Nothing has changed.";
    return {
      method: "PATCH", url: "/api/reviews/findings/" + f.id.value, body,
      message: f.resolved.checked ? "Finding resolved." : "Finding updated.",
    };
  });

  // ---- evidence pack: say what is happening while the browser downloads it
  const pack = $("#pack-btn");
  if (pack) pack.addEventListener("click", () => toast("Preparing the evidence pack. It will download as a ZIP."));
})();
