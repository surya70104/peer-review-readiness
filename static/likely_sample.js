// Screen 3 · Likely sample: keep the URL clean, expand or collapse the reasons.
(function () {
  const { $ } = window.OAK;

  // An empty selection year means "use the default", so leave it out of the address.
  const form = $("#sample-form");
  if (form) form.addEventListener("submit", () => {
    const y = form.selection_year;
    if (y && !y.value) y.disabled = true;
    if (form.limit.value === "25") form.limit.disabled = true;
  });

  const toggle = $("#toggle-all");
  if (toggle) toggle.addEventListener("click", () => {
    const all = [...document.querySelectorAll("#rank-list details")];
    const open = all.some((d) => !d.open);
    all.forEach((d) => { d.open = open; });
    toggle.textContent = open ? "Collapse all reasons" : "Expand all reasons";
  });
})();
