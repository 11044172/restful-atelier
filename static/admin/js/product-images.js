(() => {
  const root = document.querySelector("[data-product-image-manager]");
  const configNode = document.getElementById("product-image-config");
  if (!root || !configNode) return;

  const config = JSON.parse(configNode.textContent);
  const input = root.querySelector("[data-image-input]");
  const grid = root.querySelector("[data-image-grid]");
  const empty = root.querySelector("[data-image-empty]");
  const message = root.querySelector("[data-image-message]");
  const orderInput = document.getElementById("id_product_image_order");
  const maxBytes = config.limits.maxOutputBytes;
  const maxInputBytes = config.limits.maxInputBytes;
  const allowedTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
  const extensionsByType = {
    "image/jpeg": new Set(["jpg", "jpeg"]),
    "image/png": new Set(["png"]),
    "image/webp": new Set(["webp"]),
  };
  const items = (config.images || []).map((image) => ({
    key: `server-${image.id}`,
    serverId: image.id,
    previewUrl: image.url,
    filename: image.filename || image.alt_text || "商品圖片",
    status: "done",
    progress: 100,
    error: "",
    localUrl: false,
  }));
  let running = 0;
  let draggedKey = null;
  let reorderChain = Promise.resolve();

  const csrfToken = () => {
    const cookie = document.cookie.split("; ").find((part) => part.startsWith("csrftoken="));
    return cookie ? decodeURIComponent(cookie.split("=").slice(1).join("=")) : "";
  };

  const showMessage = (text) => {
    message.textContent = text;
    message.hidden = !text;
  };

  const api = async (url, body) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken()},
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      let payload = {};
      try { payload = await response.json(); } catch (_) { /* use generic message */ }
      if (!response.ok) throw new Error(payload.error || "圖片處理失敗，請重試。");
      return payload;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("連線逾時，請重試。");
      throw error;
    } finally {
      clearTimeout(timer);
    }
  };

  const scopePayload = () => ({
    upload_session: config.uploadSession,
    product_id: config.productId,
  });

  const completedIds = () => items.filter((item) => item.serverId && item.status === "done").map((item) => item.serverId);

  const syncOrder = () => {
    orderInput.value = completedIds().join(",");
    const firstCompleted = items.find((item) => item.serverId && item.status === "done");
    grid.querySelectorAll(".product-image-card").forEach((card) => {
      card.classList.toggle("is-main", card.dataset.key === firstCompleted?.key);
      const badge = card.querySelector("[data-main-badge]");
      if (badge) badge.hidden = card.dataset.key !== firstCompleted?.key;
    });
  };

  const persistOrder = () => {
    const ids = completedIds();
    if (!ids.length) return Promise.resolve();
    reorderChain = reorderChain.then(async () => {
      try {
        await api(config.reorderUrl, {...scopePayload(), images: ids});
      } catch (error) {
        showMessage(error.message || "調整圖片順序失敗。");
      }
    });
    return reorderChain;
  };

  const stateLabel = (item) => {
    if (item.status === "optimizing") return "圖片最佳化中";
    if (item.status === "queued") return "等待上傳";
    if (item.status === "uploading") return `${item.progress || 0}%`;
    if (item.status === "completing") return "確認中";
    if (item.status === "done") return "已完成";
    if (item.status === "failed") return "失敗";
    return "準備中";
  };

  const render = () => {
    grid.innerHTML = "";
    empty.hidden = items.length > 0;
    items.forEach((item) => {
      const card = document.createElement("article");
      card.className = `product-image-card status-${item.status}`;
      card.dataset.key = item.key;
      card.draggable = item.status === "done";
      card.innerHTML = `
        <div class="product-image-card__visual">
          <img src="" alt="">
          <span class="product-image-main-badge" data-main-badge hidden>主要圖片</span>
          <span class="product-image-drag" aria-hidden="true">⋮⋮</span>
        </div>
        <div class="product-image-card__body">
          <strong title=""></strong>
          <div class="product-image-progress" aria-hidden="true"><i></i></div>
          <span class="product-image-state"></span>
          <small class="product-image-error"></small>
          <div class="product-image-actions">
            <button type="button" class="button" data-retry ${item.status === "failed" ? "" : "hidden"}>重試</button>
            <button type="button" class="button product-image-delete" data-delete ${["optimizing", "uploading", "completing"].includes(item.status) ? "disabled" : ""}>刪除</button>
          </div>
        </div>`;
      const title = card.querySelector("strong");
      card.querySelector("img").src = item.previewUrl;
      title.textContent = item.filename;
      title.title = item.filename;
      card.querySelector(".product-image-state").textContent = stateLabel(item);
      card.querySelector(".product-image-error").textContent = item.error || "";
      card.querySelector(".product-image-progress i").style.width = `${item.progress || 0}%`;
      card.querySelector("[data-retry]").addEventListener("click", () => retry(item));
      card.querySelector("[data-delete]").addEventListener("click", () => removeItem(item));
      card.addEventListener("dragstart", () => { draggedKey = item.key; card.classList.add("is-dragging"); });
      card.addEventListener("dragend", () => { draggedKey = null; card.classList.remove("is-dragging"); });
      card.addEventListener("dragover", (event) => event.preventDefault());
      card.addEventListener("drop", (event) => {
        event.preventDefault();
        if (!draggedKey || draggedKey === item.key) return;
        const from = items.findIndex((candidate) => candidate.key === draggedKey);
        const to = items.findIndex((candidate) => candidate.key === item.key);
        if (from < 0 || to < 0) return;
        const [moved] = items.splice(from, 1);
        items.splice(to, 0, moved);
        render();
        syncOrder();
        persistOrder();
      });
      grid.appendChild(card);
    });
    syncOrder();
  };

  const decodeImage = async (file) => {
    if (window.createImageBitmap) {
      try { return await createImageBitmap(file, {imageOrientation: "from-image"}); }
      catch (_) { return createImageBitmap(file); }
    }
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const image = new Image();
      image.onload = () => { URL.revokeObjectURL(url); resolve(image); };
      image.onerror = () => { URL.revokeObjectURL(url); reject(new Error("decode failed")); };
      image.src = url;
    });
  };

  const optimize = async (file) => {
    const source = await decodeImage(file);
    const width = source.width || source.naturalWidth;
    const height = source.height || source.naturalHeight;
    if (!width || !height || width > config.limits.maxInputDimension || height > config.limits.maxInputDimension || width * height > config.limits.maxInputPixels) {
      if (source.close) source.close();
      throw new Error("圖片尺寸超過安全上限，請先縮小圖片。");
    }
    const longEdge = Math.max(width, height);
    const shouldResize = longEdge > config.limits.longEdge;
    const shouldReencode = shouldResize || (file.type !== "image/png" && file.size > 8 * 1024 * 1024);
    if (!shouldReencode) {
      if (source.close) source.close();
      return {file, width, height};
    }
    const scale = shouldResize ? config.limits.longEdge / longEdge : 1;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext("2d", {alpha: file.type === "image/png"});
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.drawImage(source, 0, 0, canvas.width, canvas.height);
    if (source.close) source.close();
    const blob = await new Promise((resolve, reject) => {
      canvas.toBlob((result) => result ? resolve(result) : reject(new Error("encode failed")), file.type, 0.90);
    });
    return {file: new File([blob], file.name, {type: file.type, lastModified: file.lastModified}), width: canvas.width, height: canvas.height};
  };

  const putToR2 = (item, uploadUrl, file) => new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", uploadUrl, true);
    request.timeout = 60000;
    request.setRequestHeader("Content-Type", file.type);
    request.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      item.progress = Math.round((event.loaded / event.total) * 100);
      render();
    };
    request.onload = () => request.status >= 200 && request.status < 300
      ? resolve()
      : reject(new Error("上傳至 R2 失敗。"));
    request.onerror = () => reject(new Error("請確認網路連線後重試。"));
    request.onabort = () => reject(new Error("上傳已中止。"));
    request.ontimeout = () => reject(new Error("圖片上傳逾時，請重試。"));
    request.send(file);
  });

  const upload = async (item) => {
    item.status = "uploading";
    item.progress = 0;
    item.error = "";
    render();
    try {
      const prepared = await api(config.presignUrl, {
        ...scopePayload(),
        filename: item.uploadFile.name,
        content_type: item.uploadFile.type,
        size: item.uploadFile.size,
        width: item.width,
        height: item.height,
      });
      item.pendingId = prepared.pending_image_id;
      await putToR2(item, prepared.upload_url, item.uploadFile);
      item.status = "completing";
      render();
      const completed = await api(config.completeUrl, {
        ...scopePayload(),
        object_key: prepared.object_key,
      });
      item.serverId = completed.image.id;
      item.status = "done";
      item.progress = 100;
      item.error = "";
      render();
      await persistOrder();
    } catch (error) {
      item.status = "failed";
      item.error = error.message || "上傳失敗，請重試。";
      render();
    } finally {
      running -= 1;
      pump();
    }
  };

  const pump = () => {
    while (running < 2) {
      const next = items.find((item) => item.status === "queued");
      if (!next) break;
      running += 1;
      upload(next);
    }
  };

  const prepare = async (item) => {
    try {
      const optimized = await optimize(item.file);
      item.uploadFile = optimized.file;
      item.width = optimized.width;
      item.height = optimized.height;
      if (item.uploadFile.size > maxBytes) throw new Error("每張圖片不得超過 20MB。");
      item.status = "queued";
      render();
      pump();
    } catch (error) {
      item.status = "failed";
      item.error = error.message || "圖片最佳化失敗。";
      render();
    }
  };

  const retry = (item) => {
    if (!item.file || item.status !== "failed") return;
    item.status = item.uploadFile ? "queued" : "optimizing";
    item.error = "";
    item.progress = 0;
    render();
    if (item.uploadFile) pump(); else prepare(item);
  };

  const removeItem = async (item) => {
    if (!window.confirm("確定要刪除這張圖片嗎？")) return;
    if (item.serverId) {
      try {
        await api(config.deleteUrlTemplate.replace("__IMAGE_ID__", item.serverId), scopePayload());
      } catch (error) {
        item.error = error.message || "刪除圖片失敗。";
        render();
        return;
      }
    }
    const index = items.indexOf(item);
    if (index >= 0) items.splice(index, 1);
    if (item.localUrl) URL.revokeObjectURL(item.previewUrl);
    render();
    await persistOrder();
  };

  input.addEventListener("change", () => {
    showMessage("");
    const files = Array.from(input.files || []);
    input.value = "";
    if (files.length > 10) {
      showMessage("一次最多只能選擇 10 張圖片。");
      return;
    }
    for (const file of files) {
      if (!allowedTypes.has(file.type)) {
        showMessage("不支援此圖片格式，請選擇 JPG、PNG 或 WebP。");
        return;
      }
      const extension = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
      if (!extensionsByType[file.type].has(extension)) {
        showMessage("圖片副檔名與格式不一致。");
        return;
      }
      if (file.size > maxInputBytes) {
        showMessage("原始圖片檔案過大，請選擇較小的圖片。");
        return;
      }
    }
    files.forEach((file) => {
      const item = {
        key: `local-${crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`}`,
        file,
        uploadFile: null,
        filename: file.name,
        previewUrl: URL.createObjectURL(file),
        localUrl: true,
        status: "optimizing",
        progress: 0,
        error: "",
        serverId: null,
      };
      items.push(item);
      prepare(item);
    });
    render();
  });

  window.addEventListener("beforeunload", (event) => {
    if (!items.some((item) => ["optimizing", "queued", "uploading", "completing"].includes(item.status))) return;
    event.preventDefault();
    event.returnValue = "";
  });

  render();
})();
