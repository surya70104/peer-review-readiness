// Shared helpers. No framework, no build step.
(function () {
  const $ = (sel, root) => (root || document).querySelector(sel);

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    return { ok: res.ok, status: res.status, data };
  }

  function errorText(data) {
    if (!data) return "Request failed.";
    const d = data.detail !== undefined ? data.detail : data;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d.map((e) => (e.loc ? e.loc.filter((x) => x !== "body").join(".") + ": " : "") + (e.msg || "invalid")).join("; ");
    }
    return JSON.stringify(d);
  }

  function showError(box, data) {
    if (!box) return;
    box.textContent = errorText(data);
    box.hidden = false;
  }

  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, 4500);
  }

  // Show a message after the next reload (used after a successful save).
  function flashThenReload(msg) {
    try { sessionStorage.setItem("flash", msg); } catch (_) { /* ignore */ }
    location.reload();
  }

  function init() {
    let msg = null;
    try { msg = sessionStorage.getItem("flash"); sessionStorage.removeItem("flash"); } catch (_) { /* ignore */ }
    if (msg) toast(msg);

    // Generic dialog openers/closers: data-open="#id", data-close
    document.addEventListener("click", (e) => {
      const opener = e.target.closest("[data-open]");
      if (opener) {
        const dlg = $(opener.dataset.open);
        if (dlg) {
          if (opener.dataset.fill) {
            const values = JSON.parse(opener.dataset.fill);
            for (const [k, v] of Object.entries(values)) {
              const f = dlg.querySelector('[name="' + k + '"]');
              if (f) f.value = v == null ? "" : v;
            }
          }
          const err = dlg.querySelector(".form-error");
          if (err) err.hidden = true;
          dlg.showModal();
        }
      }
      if (e.target.closest("[data-close]")) e.target.closest("dialog").close();
    });
  }

  // Blank string -> null, otherwise trimmed string.
  const blankToNull = (v) => { v = (v || "").trim(); return v === "" ? null : v; };

  window.OAK = { $, api, showError, errorText, toast, flashThenReload, blankToNull };
  document.addEventListener("DOMContentLoaded", init);
})();
