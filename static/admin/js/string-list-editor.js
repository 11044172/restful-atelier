(() => {
  document.querySelectorAll("[data-string-list]").forEach(root => {
    const hidden = root.querySelector("[data-string-list-value]");
    const rows = root.querySelector("[data-string-list-rows]");
    const sync = () => {
      hidden.value = JSON.stringify(Array.from(rows.querySelectorAll("[data-string-list-item]")).map(input => input.value));
    };
    const bind = row => {
      row.querySelector("[data-string-list-item]").addEventListener("input", sync);
      row.querySelector("[data-remove]").addEventListener("click", () => { row.remove(); sync(); });
      row.querySelector("[data-move-up]").addEventListener("click", () => { if (row.previousElementSibling) rows.insertBefore(row, row.previousElementSibling); sync(); });
      row.querySelector("[data-move-down]").addEventListener("click", () => { if (row.nextElementSibling) rows.insertBefore(row.nextElementSibling, row); sync(); });
    };
    rows.querySelectorAll(".string-list-editor__row").forEach(bind);
    root.querySelector("[data-add]").addEventListener("click", () => {
      const row = document.createElement("div");
      row.className = "string-list-editor__row";
      row.innerHTML = '<input type="text" data-string-list-item><button type="button" class="button" data-move-up aria-label="上移">↑</button><button type="button" class="button" data-move-down aria-label="下移">↓</button><button type="button" class="button" data-remove aria-label="刪除">刪除</button>';
      rows.appendChild(row); bind(row); row.querySelector("input").focus(); sync();
    });
    sync();
  });
})();
