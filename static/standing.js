// Screen 1 · Standing: partner-change widget and profile editor.
(function () {
  const { $, api, showError, toast, flashThenReload, blankToNull } = window.OAK;
  const fmt = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  };
  const phaseName = (n) => (n ? "Phase " + ["", "I", "II", "III", "IV"][n] : "Not applicable");

  // ---------------------------------------------------------------- partner change
  const pform = $("#partner-form");
  if (pform) {
    const errBox = $(".form-error", pform);
    const result = $(".result", pform);
    const maxHistory = Number(pform.dataset.maxHistory || 0);

    pform.addEventListener("submit", async (e) => {
      e.preventDefault();
      const apply = e.submitter && e.submitter.dataset.apply === "true";
      errBox.hidden = true;
      result.hidden = true;
      const buttons = pform.querySelectorAll("button");
      buttons.forEach((b) => (b.disabled = true));
      const r = await api("POST", "/api/profile/simulate-partner-change", {
        partner_count: Number(pform.partner_count.value),
        effective_on: pform.effective_on.value,
        apply,
      });
      buttons.forEach((b) => (b.disabled = false));
      if (!r.ok) return showError(errBox, r.data);

      const sim = r.data.simulation;
      if (apply) {
        const msg = sim.phase_changed
          ? "Recorded: " + phaseName(sim.phase.before) + " → " + phaseName(sim.phase.after) + " on " + fmt(sim.effective_on)
          : "Recorded partner count " + sim.partner_count.after + " (phase unchanged)";
        return flashThenReload(msg);
      }

      // Dry run: describe what the real trigger would have written, then it was rolled back.
      const newRows = r.data.history.filter((h) => h.id > maxHistory);
      result.replaceChildren();
      const add = (text, cls) => {
        const d = document.createElement("div");
        if (cls) d.className = cls;
        d.textContent = text;
        result.appendChild(d);
      };
      add("Dry run · nothing was saved", "headline");
      add("Partners " + sim.partner_count.before + " → " + sim.partner_count.after + " on " + fmt(sim.effective_on));
      add(sim.phase_changed
        ? "Phase flips: " + phaseName(sim.phase.before) + " → " + phaseName(sim.phase.after)
        : "Phase stays " + phaseName(sim.phase.after));
      add("Basis: " + sim.basis.after);
      add(newRows.length
        ? "History would gain " + newRows.length + " row" + (newRows.length > 1 ? "s" : "") + ", dated " + newRows.map((h) => fmt(h.changed_at)).join(", ") + "."
        : "No history row would be written (phase and basis are unchanged).", "muted");
      result.hidden = false;
    });
  }

  // ---------------------------------------------------------------- profile editor
  const form = $("#profile-form");
  if (!form) return;
  const errBox = $(".form-error", form);
  const creating = form.dataset.create === "true";
  const initial = creating ? {} : JSON.parse(form.dataset.initial);
  const dialog = form.closest("dialog");

  const BOOLS = ["renders_attestation", "audits_listed_entities", "audits_large_unlisted_public",
                 "audits_entities_raised_50cr", "audits_psb_branches"];
  const TEXTS = ["firm", "certificate_no"];
  const DATES = ["effective_from", "expires_on", "last_report_date"];

  const foWrap = $("#fo-wrap");
  const syncFollowOn = () => { foWrap.style.display = form.last_report_type.value === "modified" ? "" : "none"; };
  form.last_report_type.addEventListener("change", syncFollowOn);
  syncFollowOn();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errBox.hidden = true;
    const body = {};
    const put = (k, v) => { if (creating || v !== initial[k]) body[k] = v; };

    TEXTS.forEach((k) => put(k, k === "firm" ? form[k].value.trim() : blankToNull(form[k].value) || ""));
    DATES.forEach((k) => put(k, form[k].value || ""));
    BOOLS.forEach((k) => put(k, form[k].checked));
    put("level", Number(form.level.value));
    put("last_report_type", form.last_report_type.value);

    // Blank means "none" for nullable fields; the API wants null.
    for (const k of ["certificate_no", "effective_from", "expires_on", "last_report_date", "last_report_type"]) {
      if (k in body && body[k] === "") body[k] = null;
    }
    if (creating) {
      if (!body.firm) return showError(errBox, "Firm name is required.");
      body.partner_count = Number(form.partner_count.value || 0);
      for (const k of Object.keys(body)) if (body[k] === null) delete body[k];
    }

    // Follow-on period only travels with a modified report, and only if it matters.
    const type = form.last_report_type.value;
    const months = Number(form.follow_on_months.value);
    if (type === "modified" && (creating || "last_report_type" in body || "last_report_date" in body || months !== initial.follow_on_months)) {
      body.follow_on_months = months;
    }
    if (type !== "modified" && "last_report_type" in body && body.last_report_type === null) body.last_report_date = null;

    if (form.effective_on.value) body.effective_on = form.effective_on.value;

    if (!creating && Object.keys(body).filter((k) => k !== "effective_on").length === 0) {
      dialog.close();
      return toast("No changes to save.");
    }

    const btn = form.querySelector("button.primary");
    btn.disabled = true;
    const r = await api("POST", "/api/profile", body);
    btn.disabled = false;
    if (!r.ok) return showError(errBox, r.data);
    dialog.close();
    flashThenReload(creating ? "Profile created." : "Profile saved.");
  });
})();
