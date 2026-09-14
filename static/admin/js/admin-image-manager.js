(() => {
  "use strict";

  const allowedTypes = new Set(["image/jpeg", "image/png", "image/webp"]);
  const extensionsByType = {
    "image/jpeg": new Set(["jpg", "jpeg"]),
    "image/png": new Set(["png"]),
    "image/webp": new Set(["webp"]),
  };
  const busyStatuses = new Set(["optimizing", "queued", "uploading", "completing"]);

  const csrfToken = () => {
    const cookie = document.cookie.split("; ").find((part) => part.startsWith("csrftoken="));
    return cookie ? decodeURIComponent(cookie.split("=").slice(1).join("=")) : "";
  };

  const api = async (url, body) => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken()},
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      let payload = {};
      try { payload = await response.json(); } catch (_) { /* generic error below */ }
      if (!response.ok) throw new Error(payload.error || "圖片處理失敗，請重試。");
      return payload;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("連線逾時，請重試。");
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  };

  const directWidgetConfig = (root) => {
    const hidden = root.querySelector('[data-image-value]');
    const hasCurrent = root.dataset.currentUrl && hidden && hidden.value;
    const focusXInput = root.dataset.focusXInput ? document.getElementById(root.dataset.focusXInput) : null;
    const focusYInput = root.dataset.focusYInput ? document.getElementById(root.dataset.focusYInput) : null;
    const focalPoint = root.dataset.focalPoint === "true" ? {
      xField: "focus_x",
      yField: "focus_y",
      xInputId: root.dataset.focusXInput,
      yInputId: root.dataset.focusYInput,
      defaultX: 50,
      defaultY: 50,
      previews: [
        {label: "列表預覽", ratio: "5 / 3.4"},
        {label: "作品頁預覽", ratio: "16 / 9"},
      ],
    } : null;
    const currentImage = hasCurrent ? {
      url: root.dataset.currentUrl,
      filename: root.dataset.currentLabel || "目前圖片",
      formValue: hidden.value,
    } : null;
    if (currentImage && focalPoint) {
      currentImage[focalPoint.xField] = Number(focusXInput?.value || focalPoint.defaultX);
      currentImage[focalPoint.yField] = Number(focusYInput?.value || focalPoint.defaultY);
    }
    return {
      mode: "single",
      adapter: "direct",
      scope: {category: root.dataset.category},
      images: currentImage ? [currentImage] : [],
      presignUrl: root.dataset.presignUrl,
      completeUrl: root.dataset.completeUrl,
      directDeleteUrl: root.dataset.deleteUrl,
      valueInputId: hidden ? hidden.id : "",
      limits: {
        maxFiles: 1,
        maxInputBytes: Number(root.dataset.maxInputBytes),
        maxOutputBytes: Number(root.dataset.maxOutputBytes),
        maxInputPixels: Number(root.dataset.maxInputPixels),
        maxInputDimension: Number(root.dataset.maxInputDimension),
        longEdge: Number(root.dataset.longEdge),
      },
      labels: {fallbackFilename: "圖片", mainBadge: ""},
      focalPoint,
    };
  };

  const readConfig = (root) => {
    const configId = root.dataset.configId;
    if (!configId) return directWidgetConfig(root);
    const node = document.getElementById(configId);
    if (!node) throw new Error(`Missing image manager config: ${configId}`);
    return JSON.parse(node.textContent);
  };

  const decodeImage = async (file) => {
    try {
      if (window.createImageBitmap) {
        try { return await createImageBitmap(file, {imageOrientation: "from-image"}); }
        catch (_) { return await createImageBitmap(file); }
      }
      return await new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const image = new Image();
        image.onload = () => { URL.revokeObjectURL(url); resolve(image); };
        image.onerror = () => { URL.revokeObjectURL(url); reject(new Error("decode failed")); };
        image.src = url;
      });
    } catch (_) {
      throw new Error("無法讀取這張圖片，檔案可能已損壞。");
    }
  };

  const optimize = async (file, limits) => {
    const source = await decodeImage(file);
    const width = source.width || source.naturalWidth;
    const height = source.height || source.naturalHeight;
    if (!width || !height || width > limits.maxInputDimension || height > limits.maxInputDimension || width * height > limits.maxInputPixels) {
      if (source.close) source.close();
      throw new Error("圖片尺寸超過安全上限，請先縮小圖片。");
    }
    const longEdge = Math.max(width, height);
    const scale = Math.min(1, limits.longEdge / longEdge);
    const shouldReencode = scale < 1
      || file.size > limits.maxOutputBytes
      || (limits.reencodeThresholdBytes && file.type !== "image/png" && file.size > limits.reencodeThresholdBytes);
    if (!shouldReencode) {
      if (source.close) source.close();
      return {file, width, height};
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext("2d", {alpha: file.type !== "image/jpeg"});
    if (!context) {
      if (source.close) source.close();
      throw new Error("瀏覽器無法處理這張圖片。");
    }
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.drawImage(source, 0, 0, canvas.width, canvas.height);
    if (source.close) source.close();
    const blob = await new Promise((resolve, reject) => {
      canvas.toBlob(
        (result) => result ? resolve(result) : reject(new Error("瀏覽器無法處理這張圖片。")),
        file.type,
        file.type === "image/jpeg" ? 0.9 : 0.96,
      );
    });
    const optimized = new File([blob], file.name, {type: file.type, lastModified: file.lastModified});
    if (optimized.size > limits.maxOutputBytes) {
      throw new Error("處理後的圖片仍然過大，請選擇較小的圖片。");
    }
    return {file: optimized, width: canvas.width, height: canvas.height};
  };

  const putToR2 = (item, uploadUrl, file, onChange) => new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("PUT", uploadUrl, true);
    request.timeout = 60000;
    request.setRequestHeader("Content-Type", file.type);
    request.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      item.progress = Math.round((event.loaded / event.total) * 100);
      onChange();
    };
    request.onload = () => request.status >= 200 && request.status < 300
      ? resolve()
      : reject(new Error("上傳至 R2 失敗，請重試。"));
    request.onerror = () => reject(new Error("請確認網路連線後重試。"));
    request.onabort = () => reject(new Error("上傳已中止。"));
    request.ontimeout = () => reject(new Error("圖片上傳逾時，請重試。"));
    request.send(file);
  });

  const initialize = (root) => {
    if (root.dataset.imageManagerReady === "true") return;
    root.dataset.imageManagerReady = "true";

    let config;
    try { config = readConfig(root); }
    catch (error) {
      const message = root.querySelector("[data-image-message]");
      if (message) { message.textContent = "圖片管理器載入失敗，請重新整理頁面。"; message.hidden = false; }
      return;
    }

    const input = root.querySelector("[data-image-input]");
    const grid = root.querySelector("[data-image-grid]");
    const empty = root.querySelector("[data-image-empty]");
    const message = root.querySelector("[data-image-message]");
    const valueInput = config.valueInputId ? document.getElementById(config.valueInputId) : root.querySelector("[data-image-value]");
    if (!input || !grid || !empty || !message) return;

    const labels = config.labels || {};
    const focalPoint = config.focalPoint || null;
    root.classList.toggle("admin-image-manager--focal", Boolean(focalPoint));
    const metadataNames = (config.metadataFields || []).map((field) => field.name);
    if (focalPoint) metadataNames.push(focalPoint.xField, focalPoint.yField);
    const items = (config.images || []).map((image) => ({
      key: image.id ? `server-${image.id}` : `existing-${Math.random()}`,
      serverId: image.id || null,
      previewUrl: image.url,
      filename: image.filename || image.alt_text || labels.fallbackFilename || "圖片",
      status: "done",
      progress: 100,
      error: "",
      localUrl: false,
      formValue: image.formValue || "",
      width: image.width || null,
      height: image.height || null,
      metadata: Object.fromEntries(metadataNames.map((name) => [
        name,
        image[name] ?? (name === focalPoint?.xField ? focalPoint.defaultX : name === focalPoint?.yField ? focalPoint.defaultY : ""),
      ])),
    }));
    let previousSingleItem = null;
    let running = 0;
    let draggedKey = null;
    let reorderChain = Promise.resolve();

    const scopePayload = () => ({...(config.scope || {})});
    const completedIds = () => items.filter((item) => item.serverId && item.status === "done").map((item) => item.serverId);
    const showMessage = (text) => {
      message.textContent = text || "";
      message.hidden = !text;
    };
    const normalizedFocus = (value, fallback = 50) => {
      const number = Number(value);
      return Number.isFinite(number) ? Math.max(0, Math.min(100, Math.round(number))) : fallback;
    };
    const syncFocalFormInputs = (item) => {
      if (!focalPoint || config.mode !== "single") return;
      const xInput = focalPoint.xInputId ? document.getElementById(focalPoint.xInputId) : null;
      const yInput = focalPoint.yInputId ? document.getElementById(focalPoint.yInputId) : null;
      if (xInput) xInput.value = normalizedFocus(item.metadata[focalPoint.xField], focalPoint.defaultX);
      if (yInput) yInput.value = normalizedFocus(item.metadata[focalPoint.yField], focalPoint.defaultY);
    };
    const syncInputs = () => {
      const sessionInput = config.sessionInputId ? document.getElementById(config.sessionInputId) : null;
      const orderInput = config.orderInputId ? document.getElementById(config.orderInputId) : null;
      if (sessionInput && config.scope?.upload_session) {
        sessionInput.value = config.scope.upload_session;
        sessionInput.setAttribute("value", config.scope.upload_session);
      }
      if (orderInput) {
        const orderValue = completedIds().join(",");
        orderInput.value = orderValue;
        orderInput.setAttribute("value", orderValue);
      }
      if (config.mode === "single" && valueInput && items[0]?.status === "done") {
        valueInput.value = items[0].formValue || valueInput.value;
      }
      if (items[0]) syncFocalFormInputs(items[0]);
    };
    const persistOrder = () => {
      if (config.mode !== "multiple" || !config.reorderUrl) return Promise.resolve();
      if (items.some((item) => busyStatuses.has(item.status))) return Promise.resolve();
      const ids = completedIds();
      reorderChain = reorderChain.then(async () => {
        try { await api(config.reorderUrl, {...scopePayload(), images: ids}); }
        catch (error) { showMessage(error.message || "調整圖片順序失敗。"); }
      });
      return reorderChain;
    };
    const stateLabel = (item) => ({
      optimizing: "圖片最佳化中",
      queued: "等待上傳",
      uploading: `${item.progress || 0}%`,
      completing: "R2 完成確認中",
      done: "已完成",
      failed: "失敗",
    }[item.status] || "準備中");

    const persistMetadata = async (item, card) => {
      if (!config.metadataUrlTemplate || !item.serverId) return;
      const values = {};
      card.querySelectorAll("[data-metadata-field]").forEach((field) => { values[field.name] = field.value; });
      const state = card.querySelector("[data-metadata-state]");
      state.classList.remove("is-error");
      state.textContent = "儲存中…";
      try {
        const result = await api(
          config.metadataUrlTemplate.replace("__IMAGE_ID__", item.serverId),
          {...scopePayload(), ...values},
        );
        item.metadata = {...item.metadata, ...(result.image || values)};
        state.textContent = "已儲存";
        state.classList.remove("is-error");
      } catch (error) {
        state.textContent = error.message || "儲存失敗";
        state.classList.add("is-error");
      }
    };

    const focalPointMarkup = (item) => {
      if (!focalPoint || item.status !== "done") return "";
      const previews = (focalPoint.previews || []).map((preview) => `
        <figure class="admin-focal-preview" style="--preview-ratio:${preview.ratio}">
          <div><img src="" alt=""></div><figcaption>${preview.label}</figcaption>
        </figure>`).join("");
      const metadataAttributes = config.metadataUrlTemplate ? "data-metadata-field" : "";
      return `
        <section class="admin-focal-editor" data-focal-editor>
          <div class="admin-focal-editor__heading">
            <strong>圖片焦點</strong>
            <span>點擊或拖曳圓點調整希望顯示的圖片位置</span>
          </div>
          <div class="admin-focal-surface" data-focal-surface tabindex="0" role="application" aria-label="圖片焦點，可使用方向鍵調整">
            <img src="" alt=""><span class="admin-focal-marker" data-focal-marker aria-hidden="true"></span>
          </div>
          <input type="hidden" name="${focalPoint.xField}" data-focus-x ${metadataAttributes}>
          <input type="hidden" name="${focalPoint.yField}" data-focus-y ${metadataAttributes}>
          <div class="admin-focal-previews">${previews}</div>
          <button type="button" class="button admin-focal-reset" data-focal-reset>重設為置中</button>
        </section>`;
    };

    const setupFocalPoint = (item, card) => {
      if (!focalPoint) return;
      const editor = card.querySelector("[data-focal-editor]");
      if (!editor) return;
      const surface = editor.querySelector("[data-focal-surface]");
      const sourceImage = surface.querySelector("img");
      const marker = editor.querySelector("[data-focal-marker]");
      const xControl = editor.querySelector("[data-focus-x]");
      const yControl = editor.querySelector("[data-focus-y]");
      const previewImages = editor.querySelectorAll(".admin-focal-preview img");
      let activePointer = null;
      let restoreDraggable = false;

      const update = (x, y) => {
        const nextX = normalizedFocus(x, focalPoint.defaultX);
        const nextY = normalizedFocus(y, focalPoint.defaultY);
        item.metadata[focalPoint.xField] = nextX;
        item.metadata[focalPoint.yField] = nextY;
        xControl.value = nextX;
        yControl.value = nextY;
        marker.style.left = `${nextX}%`;
        marker.style.top = `${nextY}%`;
        previewImages.forEach((image) => { image.style.objectPosition = `${nextX}% ${nextY}%`; });
        surface.setAttribute("aria-valuetext", `水平 ${nextX}%，垂直 ${nextY}%`);
        syncFocalFormInputs(item);
      };
      const updateFromPointer = (event) => {
        const bounds = surface.getBoundingClientRect();
        if (!bounds.width || !bounds.height) return;
        update(
          ((event.clientX - bounds.left) / bounds.width) * 100,
          ((event.clientY - bounds.top) / bounds.height) * 100,
        );
      };
      const persistFocus = () => {
        if (config.metadataUrlTemplate && item.serverId) persistMetadata(item, card);
      };
      const setSurfaceRatio = () => {
        const width = item.width || sourceImage.naturalWidth;
        const height = item.height || sourceImage.naturalHeight;
        if (width && height) surface.style.aspectRatio = `${width} / ${height}`;
      };

      sourceImage.src = item.previewUrl || "";
      sourceImage.addEventListener("load", setSurfaceRatio, {once: true});
      if (sourceImage.complete) setSurfaceRatio();
      previewImages.forEach((image) => {
        image.src = item.previewUrl || "";
        image.alt = "";
      });
      update(item.metadata[focalPoint.xField], item.metadata[focalPoint.yField]);

      surface.addEventListener("pointerdown", (event) => {
        if (event.button !== undefined && event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        restoreDraggable = card.draggable;
        card.draggable = false;
        activePointer = event.pointerId;
        surface.setPointerCapture?.(event.pointerId);
        updateFromPointer(event);
      });
      surface.addEventListener("pointermove", (event) => {
        if (activePointer !== event.pointerId) return;
        event.preventDefault();
        updateFromPointer(event);
      });
      const finishPointer = (event) => {
        if (activePointer !== event.pointerId) return;
        updateFromPointer(event);
        activePointer = null;
        card.draggable = restoreDraggable;
        restoreDraggable = false;
        persistFocus();
      };
      surface.addEventListener("pointerup", finishPointer);
      surface.addEventListener("pointercancel", finishPointer);
      surface.addEventListener("keydown", (event) => {
        const movements = {
          ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
        };
        if (!movements[event.key]) return;
        event.preventDefault();
        const amount = event.shiftKey ? 5 : 1;
        const [moveX, moveY] = movements[event.key];
        update(
          Number(item.metadata[focalPoint.xField]) + moveX * amount,
          Number(item.metadata[focalPoint.yField]) + moveY * amount,
        );
        persistFocus();
      });
      editor.querySelector("[data-focal-reset]").addEventListener("click", () => {
        update(focalPoint.defaultX, focalPoint.defaultY);
        persistFocus();
        surface.focus();
      });
    };

    const moveItem = (item, offset) => {
      const from = items.indexOf(item);
      const to = from + offset;
      if (from < 0 || to < 0 || to >= items.length || item.status !== "done" || items[to].status !== "done") return;
      items.splice(from, 1);
      items.splice(to, 0, item);
      render();
      persistOrder();
    };

    const render = () => {
      grid.innerHTML = "";
      empty.hidden = items.length > 0;
      const firstCompleted = items.find((item) => item.serverId && item.status === "done");
      items.forEach((item, index) => {
        const card = document.createElement("article");
        card.className = `admin-image-card status-${item.status}`;
        card.dataset.key = item.key;
        card.draggable = config.mode === "multiple" && item.status === "done";
        const metadataMarkup = item.status === "done" ? (config.metadataFields || []).map((field) => {
          if (field.type === "select") {
            const options = (field.options || []).map((option) => `<option value="${option}">${option}</option>`).join("");
            return `<label class="admin-image-metadata-field"><span>${field.label}</span><select name="${field.name}" data-metadata-field>${options}</select></label>`;
          }
          return `<label class="admin-image-metadata-field"><span>${field.label}</span><input type="text" name="${field.name}" maxlength="${field.maxLength || 255}" data-metadata-field></label>`;
        }).join("") : "";
        const navigation = config.mode === "multiple" ? `
          <button type="button" class="button" data-move-previous aria-label="前へ移動" ${index === 0 ? "disabled" : ""}>←</button>
          <button type="button" class="button" data-move-next aria-label="後ろへ移動" ${index === items.length - 1 ? "disabled" : ""}>→</button>` : "";
        card.innerHTML = `
          <div class="admin-image-card__visual">
            <img src="" alt="">
            <span class="admin-image-main-badge" data-main-badge hidden></span>
            ${config.mode === "multiple" ? '<span class="admin-image-drag" aria-hidden="true">⋮⋮</span>' : ""}
          </div>
          <div class="admin-image-card__body">
            <strong title=""></strong>
            <div class="admin-image-progress" aria-hidden="true"><i></i></div>
            <span class="admin-image-state"></span>
            <small class="admin-image-error"></small>
            ${metadataMarkup || (focalPoint && config.metadataUrlTemplate) ? `<div class="admin-image-metadata">${metadataMarkup}<small data-metadata-state aria-live="polite"></small></div>` : ""}
            ${focalPointMarkup(item)}
            <div class="admin-image-actions">
              ${navigation}
              <button type="button" class="button" data-retry ${item.status === "failed" ? "" : "hidden"}>重試</button>
              <button type="button" class="button admin-image-delete" data-delete ${busyStatuses.has(item.status) ? "disabled" : ""}>${config.mode === "single" ? "移除圖片" : "刪除"}</button>
            </div>
          </div>`;
        const image = card.querySelector("img");
        image.src = item.previewUrl || "";
        image.alt = item.metadata?.alt_text || item.filename;
        const title = card.querySelector("strong");
        title.textContent = item.filename;
        title.title = item.filename;
        const badge = card.querySelector("[data-main-badge]");
        if (badge && labels.mainBadge && item === firstCompleted) {
          badge.textContent = labels.mainBadge;
          badge.hidden = false;
          card.classList.add("is-main");
        }
        card.querySelector(".admin-image-state").textContent = stateLabel(item);
        card.querySelector(".admin-image-error").textContent = item.error || "";
        card.querySelector(".admin-image-progress i").style.width = `${item.progress || 0}%`;
        (config.metadataFields || []).forEach((field) => {
          const control = card.querySelector(`[data-metadata-field][name="${field.name}"]`);
          if (!control) return;
          control.value = item.metadata[field.name] || "";
          control.addEventListener("change", () => persistMetadata(item, card));
          if (field.type !== "select") control.addEventListener("blur", () => persistMetadata(item, card));
        });
        setupFocalPoint(item, card);
        card.querySelector("[data-retry]").addEventListener("click", () => retry(item));
        card.querySelector("[data-delete]").addEventListener("click", () => removeItem(item));
        card.querySelector("[data-move-previous]")?.addEventListener("click", () => moveItem(item, -1));
        card.querySelector("[data-move-next]")?.addEventListener("click", () => moveItem(item, 1));
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
          persistOrder();
        });
        grid.appendChild(card);
      });
      syncInputs();
    };

    const discardDirectUpload = async (item) => {
      if (config.adapter !== "direct" || !item.uploadId || !item.objectKey || !config.directDeleteUrl) return;
      await api(config.directDeleteUrl, {
        ...scopePayload(), upload_id: item.uploadId, object_key: item.objectKey,
      });
    };

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
        item.pendingId = prepared.pending_image_id || prepared.upload_id;
        item.objectKey = prepared.object_key;
        await putToR2(item, prepared.upload_url, item.uploadFile, render);
        item.status = "completing";
        render();
        const completed = await api(config.completeUrl, {
          ...scopePayload(),
          upload_id: prepared.upload_id,
          object_key: prepared.object_key,
        });
        if (completed.image) {
          item.serverId = completed.image.id;
          item.filename = completed.image.filename || item.filename;
          item.width = completed.image.width || item.width;
          item.height = completed.image.height || item.height;
          item.metadata = Object.fromEntries(metadataNames.map((name) => [
            name,
            completed.image[name] ?? (name === focalPoint?.xField ? focalPoint.defaultX : name === focalPoint?.yField ? focalPoint.defaultY : ""),
          ]));
        } else {
          item.uploadId = completed.upload_id || prepared.upload_id;
          item.objectKey = completed.object_key || prepared.object_key;
          item.formValue = `upload:${completed.token}`;
          if (valueInput) valueInput.value = item.formValue;
          if (previousSingleItem?.uploadId) {
            discardDirectUpload(previousSingleItem).catch(() => {});
          }
          previousSingleItem = null;
        }
        item.status = "done";
        item.progress = 100;
        render();
        showMessage(config.mode === "single" ? "圖片已上傳完成，儲存表單後生效。" : "");
        await persistOrder();
      } catch (error) {
        item.status = "failed";
        item.error = error.message || "上傳失敗，請重試。";
        render();
        showMessage(item.error);
        await persistOrder();
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
        const optimized = await optimize(item.file, config.limits);
        item.uploadFile = optimized.file;
        item.width = optimized.width;
        item.height = optimized.height;
        item.status = "queued";
        render();
        pump();
      } catch (error) {
        item.status = "failed";
        item.error = error.message || "圖片最佳化失敗。";
        render();
        showMessage(item.error);
        await persistOrder();
      }
    };

    const retry = (item) => {
      if (!item.file || item.status !== "failed") return;
      item.status = item.uploadFile ? "queued" : "optimizing";
      item.error = "";
      item.progress = 0;
      showMessage("");
      render();
      if (item.uploadFile) pump(); else prepare(item);
    };

    const removeItem = async (item) => {
      if (!window.confirm("確定要移除這張圖片嗎？")) return;
      try {
        if (config.mode === "multiple" && item.serverId) {
          await api(config.deleteUrlTemplate.replace("__IMAGE_ID__", item.serverId), scopePayload());
        } else if (config.mode === "single" && item.uploadId) {
          await discardDirectUpload(item);
        }
      } catch (error) {
        item.error = error.message || "刪除圖片失敗。";
        render();
        showMessage(item.error);
        return;
      }
      const index = items.indexOf(item);
      if (index >= 0) items.splice(index, 1);
      if (item.localUrl) URL.revokeObjectURL(item.previewUrl);
      if (config.mode === "single") {
        if (item.status === "failed" && previousSingleItem) {
          items.push(previousSingleItem);
          if (valueInput) valueInput.value = previousSingleItem.formValue || "";
        } else if (valueInput) {
          valueInput.value = "";
        }
        previousSingleItem = null;
        showMessage(items.length ? "" : "儲存後將移除圖片。");
      }
      render();
      await persistOrder();
    };

    input.addEventListener("change", () => {
      showMessage("");
      const files = Array.from(input.files || []);
      input.value = "";
      if (!files.length) return;
      if (config.mode === "single" && items.some((item) => busyStatuses.has(item.status))) {
        showMessage("目前圖片仍在上傳，請等待完成後再選擇其他圖片。");
        return;
      }
      if (files.length > (config.limits.maxFiles || 10)) {
        showMessage(`一次最多只能選擇 ${config.limits.maxFiles || 10} 張圖片。`);
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
        if (file.size <= 0 || file.size > config.limits.maxInputBytes) {
          showMessage("原始圖片檔案過大或為空，請選擇其他圖片。");
          return;
        }
      }
      if (config.mode === "single") {
        previousSingleItem = items[0] || null;
        items.splice(0, items.length);
      }
      files.forEach((file) => {
        const item = {
          key: `local-${window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random()}`}`,
          file,
          uploadFile: null,
          filename: file.name,
          previewUrl: URL.createObjectURL(file),
          localUrl: true,
          status: "optimizing",
          progress: 0,
          error: "",
          serverId: null,
          metadata: focalPoint ? {
            [focalPoint.xField]: focalPoint.defaultX,
            [focalPoint.yField]: focalPoint.defaultY,
          } : {},
        };
        items.push(item);
        prepare(item);
      });
      render();
    });

    const form = root.closest("form");
    form?.addEventListener("submit", (event) => {
      syncInputs();
      if (!items.some((item) => busyStatuses.has(item.status))) return;
      event.preventDefault();
      showMessage("圖片仍在上傳，請等待完成後再儲存。");
    }, {capture: true});
    window.addEventListener("pageshow", syncInputs);
    window.addEventListener("beforeunload", (event) => {
      if (!items.some((item) => busyStatuses.has(item.status))) return;
      event.preventDefault();
      event.returnValue = "";
    });
    render();
    window.setTimeout(syncInputs, 0);
  };

  const boot = () => document.querySelectorAll("[data-admin-image-manager]").forEach(initialize);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, {once: true});
  } else {
    boot();
  }
})();
