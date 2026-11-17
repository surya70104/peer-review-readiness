// Screen 2 · Scorecard: recompute, and log a documented later addition.
(function () {
  const { $, api, showError, errorText, toast, flashThenReload, blankToNull } = window.OAK;

  // ---- recompute: re-evaluate every engagement and save the scores
  const btn = $("#recompute-btn");
  if (btn) btn.addEventListener("click", async () => {
    btn.disabled = true;
    const y = btn.dataset.year;
    const r = await api("POST", "/api/readiness/recompute" + (y ? "?selection_year=" + encodeURIComponent(y) : ""));
    btn.disabled = false;
    if (!r.ok) return toast(errorText(r.data));
    const d = r.data;
    flashThenReload("Recomputed " + d.engagements_scored + " engagements; " + d.rows_written + " scores saved."
      + (d.later_additions_preserved ? " " + d.later_additions_preserved + " later addition(s) preserved." : ""));
  });

  // ---- later addition dialog
  document.addEventListener("click", (e) => {
    const b = e.target.closest('[data-open="#later-addition"]');
    if (!b) return;
    const v = JSON.parse(b.dataset.fill);
    $("#la-label").textContent = v.label;
    $("#la-note").textContent = v.note;
  });

  const form = $("#later-addition-form");
  if (!form) return;
  const dialog = form.closest("dialog");
  const errBox = $(".form-error", form);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errBox.hidden = true;
    const body = { description: form.description.value.trim(), reason: form.reason.value.trim() };
    if (!body.description || !body.reason) return showError(errBox, "Say what was added and why it was added late.");
    const dim = blankToNull(form.dimension.value);
    const by = blankToNull(form.added_by.value);
    if (dim) body.dimension = dim;
    if (by) body.added_by = by;
    const submit = form.querySelector("button.primary");
    submit.disabled = true;
    const r = await api("POST", "/api/readiness/engagements/" + form.id.value + "/later-addition", body);
    submit.disabled = false;
    // Inside the 60-day window the server refuses and says when it closes; show that as is.
    if (!r.ok) return showError(errBox, r.status === 409 ? r.data : r.data);
    dialog.close();
    form.reset();
    flashThenReload("Later addition recorded with today's date. The score is unchanged.");
  });
})();
