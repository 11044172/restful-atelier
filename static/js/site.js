(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const FAVORITES_KEY = 'restful-favorites';
  let toastTimer;
  const toast = (message) => {
    const el = $('[data-toast]');
    if (!el) return;
    clearTimeout(toastTimer);
    el.textContent = message;
    el.classList.add('is-visible');
    toastTimer = setTimeout(() => el.classList.remove('is-visible'), 2800);
  };
  if ($('[data-toast]')?.textContent.trim()) toastTimer = setTimeout(() => $('[data-toast]').classList.remove('is-visible'), 3200);
  const validSlugs = new Set([
    ...(document.body.dataset.productSlugs || '').split(',').filter(Boolean),
    ...$$('[data-product-slug]').map((node) => node.dataset.productSlug),
  ]);
  const readFavorites = () => {
    try {
      const value = JSON.parse(localStorage.getItem(FAVORITES_KEY) || '[]');
      return Array.isArray(value) ? [...new Set(value.filter((slug) => typeof slug === 'string' && (!validSlugs.size || validSlugs.has(slug))))] : [];
    } catch { return []; }
  };
  const updateFavorites = () => {
    const favorites = readFavorites();
    $$('[data-favorite-count]').forEach((el) => { el.textContent = String(favorites.length); });
    $$('[data-favorite]').forEach((button) => {
      const active = favorites.includes(button.dataset.favorite);
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
      const status = $('.favorite-status', button);
      if (status) status.textContent = active ? '已收藏' : '尚未收藏';
      const label = $('[data-favorite-label]', button);
      if (label) label.textContent = active ? '移除收藏' : '加入收藏';
    });
    $$('[data-favorites-grid] [data-product-card]').forEach((card) => { card.hidden = !favorites.includes(card.dataset.productSlug); });
    const favoritesGrid = $('[data-favorites-grid]');
    if (favoritesGrid) favoritesGrid.classList.add('is-ready');
    const empty = $('[data-favorites-empty]');
    if (empty) empty.hidden = favorites.length > 0;
    const count = $('[data-favorites-page-count]');
    if (count) count.textContent = String(favorites.length);
  };
  $$('[data-favorite]').forEach((button) => button.addEventListener('click', () => {
    const favorites = readFavorites();
    const active = favorites.includes(button.dataset.favorite);
    const next = active ? favorites.filter((slug) => slug !== button.dataset.favorite) : [...favorites, button.dataset.favorite];
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(next));
    updateFavorites();
    toast(active ? '已從收藏中移除。' : '已加入收藏。');
  }));
  const setupMenu = (toggle, menu) => {
    menu.setAttribute('role', 'dialog'); menu.setAttribute('aria-modal', 'true');
    const focusable = $$('a,button,input,select,textarea,[tabindex]:not([tabindex="-1"])', menu);
    const close = () => { const wasOpen = toggle.getAttribute('aria-expanded') === 'true'; toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-label', '開啟選單'); menu.setAttribute('aria-hidden', 'true'); document.body.classList.remove('menu-open'); if (wasOpen) toggle.focus(); };
    toggle.addEventListener('click', () => { const open = toggle.getAttribute('aria-expanded') !== 'true'; toggle.setAttribute('aria-expanded', String(open)); toggle.setAttribute('aria-label', open ? '關閉選單' : '開啟選單'); menu.setAttribute('aria-hidden', String(!open)); document.body.classList.toggle('menu-open', open); if (open) focusable[0]?.focus(); });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); if (event.key === 'Tab' && toggle.getAttribute('aria-expanded') === 'true' && focusable.length) { const first = focusable[0]; const last = focusable[focusable.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } } });
  };
  $$('.menu-toggle').forEach((toggle) => { const menu = document.getElementById(toggle.getAttribute('aria-controls')); if (menu) setupMenu(toggle, menu); });
  const categoryToggle = $('[data-shop-category-toggle]');
  const categoryMenu = $('[data-shop-category-menu]');
  categoryToggle?.addEventListener('click', (event) => { event.stopPropagation(); const open = categoryToggle.getAttribute('aria-expanded') !== 'true'; categoryToggle.setAttribute('aria-expanded', String(open)); categoryMenu?.setAttribute('aria-hidden', String(!open)); categoryMenu?.classList.toggle('is-open', open); });
  categoryMenu?.addEventListener('click', (event) => event.stopPropagation());
  document.addEventListener('click', () => { categoryToggle?.setAttribute('aria-expanded', 'false'); categoryMenu?.setAttribute('aria-hidden', 'true'); categoryMenu?.classList.remove('is-open'); });
  const searchPanel = $('[data-search-panel]');
  const searchToggle = $('[data-search-toggle]');
  const setSearch = (open) => { searchPanel?.classList.toggle('is-open', open); searchPanel?.setAttribute('aria-hidden', String(!open)); searchToggle?.setAttribute('aria-expanded', String(open)); document.body.classList.toggle('menu-open', open); if (open) setTimeout(() => $('input', searchPanel)?.focus(), 50); else searchToggle?.focus(); };
  searchToggle?.addEventListener('click', () => setSearch(true));
  $('[data-search-close]')?.addEventListener('click', () => setSearch(false));
  searchPanel?.addEventListener('click', (event) => { if (event.target === searchPanel) setSearch(false); });
  document.addEventListener('keydown', (event) => { if (!searchPanel?.classList.contains('is-open')) return; if (event.key === 'Escape') { event.preventDefault(); setSearch(false); return; } if (event.key === 'Tab') { const focusable = $$('a,button,input,select,textarea,[tabindex]:not([tabindex="-1"])', searchPanel); const first = focusable[0]; const last = focusable[focusable.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); } } });
  $$('[data-quantity]').forEach((button) => button.addEventListener('click', () => { const input = button.closest('[data-quantity-root]')?.querySelector('[data-quantity-input]'); const output = button.closest('[data-quantity-root]')?.querySelector('[data-quantity-value]'); if (!input) return; input.value = String(Math.max(1, Math.min(99, Number(input.value) + Number(button.dataset.quantity)))); if (output) output.textContent = input.value; }));
  $$('[data-gallery-thumb]').forEach((button) => button.addEventListener('click', () => {
    const root = button.closest('[data-product-gallery]'); const main = $('[data-gallery-main]', root); if (!main) return;
    const url = button.dataset.imageUrl; const alt = button.dataset.imageAlt || button.dataset.imageLabel || '';
    const visual = document.createElement('div');
    visual.className = `editorial-image tone-${button.dataset.imageTone || 'linen'}`;
    visual.style.setProperty('--ratio', '4 / 4.75');
    visual.setAttribute('role', 'img');
    visual.setAttribute('aria-label', alt);
    if (url) {
      const image = document.createElement('img'); image.className = 'cms-image'; image.src = url; image.alt = alt; visual.append(image);
    } else {
      const corner = document.createElement('span'); corner.className = 'image-corner'; corner.textContent = 'RESTFULL ATELIER';
      const label = document.createElement('span'); label.className = 'image-label'; label.textContent = alt;
      const index = document.createElement('span'); index.className = 'image-index'; index.setAttribute('aria-hidden', 'true'); index.textContent = '靜';
      visual.append(corner, label, index);
    }
    main.replaceChildren(visual);
    $$('[data-gallery-thumb]', root).forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
  }));
  const revealObserver = 'IntersectionObserver' in window ? new IntersectionObserver((entries) => entries.forEach((entry) => { if (entry.isIntersecting) { entry.target.classList.add('is-shown'); revealObserver.unobserve(entry.target); } }), {threshold: .08}) : null;
  $$('.reveal').forEach((el) => revealObserver ? revealObserver.observe(el) : el.classList.add('is-shown'));
  const header = $('[data-header]');
  const setHeader = () => header?.classList.toggle('is-scrolled', window.scrollY > 24);
  setHeader(); window.addEventListener('scroll', setHeader, {passive:true});
  const lineFriendWatch = $('[data-line-friend-watch]');
  if (lineFriendWatch) {
    const endpoint = lineFriendWatch.dataset.statusUrl;
    const status = $('[data-line-friend-status]', lineFriendWatch);
    let checking = false;
    let complete = false;
    let attempts = 0;
    const maxAttempts = 48;
    const checkFriend = async () => {
      if (!endpoint || checking || complete || document.hidden || attempts >= maxAttempts) return;
      checking = true; attempts += 1;
      try {
        const response = await fetch(endpoint, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
        if (!response.ok) return;
        const result = await response.json();
        if (result.friend) {
          complete = true;
          if (status) status.textContent = '已確認 LINE 好友。正在進入配送資料填寫…';
          window.location.assign(result.redirect || window.location.href);
        }
      } catch { /* A later poll or the manual confirmation link can retry. */ }
      finally { checking = false; }
    };
    const restartCheck = () => { attempts = 0; checkFriend(); };
    $('[data-line-add-friend]', lineFriendWatch)?.addEventListener('click', () => {
      if (status) status.textContent = '加入完成後請回到這個頁面，系統會自動確認。';
      restartCheck();
    });
    window.addEventListener('pageshow', restartCheck);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) restartCheck(); });
    setInterval(checkFriend, 2500);
    checkFriend();
  }
  const paymentMethods = $('[data-payment-methods]');
  if (paymentMethods) {
    $$('[data-payment-method]', paymentMethods).forEach((method) => {
      method.addEventListener('toggle', () => {
        if (!method.open) return;
        $$('[data-payment-method]', paymentMethods).forEach((other) => {
          if (other !== method) other.open = false;
        });
      });
    });
  }
  updateFavorites(); window.addEventListener('storage', updateFavorites); window.addEventListener('pageshow', updateFavorites);
})();
