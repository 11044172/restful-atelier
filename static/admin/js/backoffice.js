(() => {
  const normalize = (value) => value.replace(/\/+$/, "") || "/";
  const current = normalize(window.location.pathname);

  document.querySelectorAll("#nav-sidebar a.nav-entry").forEach((link) => {
    const target = normalize(new URL(link.href, window.location.origin).pathname);
    const isDashboard = link.classList.contains("nav-dashboard");
    const active = isDashboard ? current === target : current.startsWith(target);
    if (active) link.setAttribute("aria-current", "page");
  });

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-confirm]");
    if (!trigger) return;
    if (!window.confirm(trigger.dataset.confirm)) event.preventDefault();
  });

  document.querySelectorAll("[data-total-preview]").forEach((form) => {
    const input = form.querySelector('[name="shipping_fee"]');
    const output = form.querySelector("[data-preview-value]");
    if (!input || !output) return;
    const update = () => {
      const subtotal = Number(form.dataset.subtotal || 0);
      const fee = Number(input.value);
      output.textContent = Number.isFinite(fee) && fee >= 0
        ? `NT$ ${(subtotal + fee).toLocaleString("zh-TW", {maximumFractionDigits: 0})}`
        : "—";
    };
    input.addEventListener("input", update);
    update();
  });
})();
