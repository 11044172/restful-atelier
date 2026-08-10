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
})();
