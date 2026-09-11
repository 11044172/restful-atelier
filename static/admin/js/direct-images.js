(() => {
  const allowed = new Set(["image/jpeg", "image/png", "image/webp"]);
  const csrf = () => (document.cookie.split("; ").find(v => v.startsWith("csrftoken=")) || "=").split("=").slice(1).join("=");
  const api = async (url, body) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(url, {method:"POST", credentials:"same-origin", headers:{"Content-Type":"application/json","X-CSRFToken":decodeURIComponent(csrf())}, body:JSON.stringify(body), signal:controller.signal});
      let data = {}; try { data = await response.json(); } catch (_) {}
      if (!response.ok) throw new Error(data.error || "圖片處理失敗，請重試。");
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("連線逾時，請重試。");
      throw error;
    } finally { clearTimeout(timer); }
  };
  const decode = async file => {
    if (!allowed.has(file.type)) throw new Error("僅支援 JPG、PNG 或 WebP 圖片。");
    if (file.size > cfg.maxInputBytes) throw new Error("原始圖片檔案過大，請選擇較小的圖片。");
    try {
      if (window.createImageBitmap) return await createImageBitmap(file, {imageOrientation:"from-image"});
      return await new Promise((resolve,reject) => { const url=URL.createObjectURL(file), image=new Image(); image.onload=()=>{URL.revokeObjectURL(url);resolve(image);}; image.onerror=()=>{URL.revokeObjectURL(url);reject();}; image.src=url; });
    } catch (_) { throw new Error("無法讀取這張圖片，檔案可能已損壞。"); }
  };
  const prepare = async (file, profile) => {
    const source = await decode(file), width=source.width||source.naturalWidth, height=source.height||source.naturalHeight;
    if (!width || !height || width > cfg.maxInputDimension || height > cfg.maxInputDimension || width*height > cfg.maxInputPixels) { if(source.close)source.close(); throw new Error("圖片尺寸超過安全上限，請先縮小圖片。"); }
    const limit = profile === "photo" ? cfg.photoLongEdge : cfg.artworkLongEdge;
    const scale = Math.min(1, limit/Math.max(width,height));
    if (scale === 1 && file.size <= cfg.maxOutputBytes) { if(source.close)source.close(); return {file,width,height}; }
    const canvas=document.createElement("canvas"); canvas.width=Math.max(1,Math.round(width*scale)); canvas.height=Math.max(1,Math.round(height*scale));
    const ctx=canvas.getContext("2d",{alpha:file.type!=="image/jpeg"}); ctx.imageSmoothingEnabled=true; ctx.imageSmoothingQuality="high"; ctx.drawImage(source,0,0,canvas.width,canvas.height); if(source.close)source.close();
    const quality = profile === "photo" ? .9 : .96;
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,file.type,quality));
    if(!blob) throw new Error("瀏覽器無法處理這張圖片。");
    const result=new File([blob],file.name,{type:file.type,lastModified:file.lastModified});
    if(result.size>cfg.maxOutputBytes) throw new Error("處理後的圖片仍然過大，請選擇較小的圖片。");
    return {file:result,width:canvas.width,height:canvas.height};
  };
  const put = (url,file,onProgress) => new Promise((resolve,reject)=>{ const xhr=new XMLHttpRequest(); xhr.open("PUT",url); xhr.timeout=60000; xhr.setRequestHeader("Content-Type",file.type); xhr.upload.onprogress=e=>e.lengthComputable&&onProgress(Math.round(e.loaded/e.total*100)); xhr.onload=()=>xhr.status>=200&&xhr.status<300?resolve():reject(new Error("圖片上傳失敗，請重試。")); xhr.onerror=()=>reject(new Error("網路連線中斷，請重試。")); xhr.ontimeout=()=>reject(new Error("圖片上傳逾時，請重試。")); xhr.send(file); });
  document.querySelectorAll("[data-direct-image]").forEach(root=>{
    const cfg = {
      presignUrl: root.dataset.presignUrl, completeUrl: root.dataset.completeUrl,
      maxInputBytes: Number(root.dataset.maxInputBytes), maxOutputBytes: Number(root.dataset.maxOutputBytes),
      maxInputPixels: Number(root.dataset.maxInputPixels), maxInputDimension: Number(root.dataset.maxInputDimension),
      photoLongEdge: Number(root.dataset.photoLongEdge), artworkLongEdge: Number(root.dataset.artworkLongEdge),
    };
    const hidden=root.querySelector('input[type="hidden"]'), input=root.querySelector("[data-file-input]"), preview=root.querySelector("[data-preview]"), img=root.querySelector("[data-preview-image]"), remove=root.querySelector("[data-remove]"), retry=root.querySelector("[data-retry]"), progress=root.querySelector("[data-progress]"), bar=progress.querySelector("i"), status=progress.querySelector("span"), message=root.querySelector("[data-message]");
    let selected=null, localUrl=null;
    const showError=text=>{message.textContent=text;retry.hidden=!selected;progress.hidden=true;};
    const upload=async()=>{ try { retry.hidden=true;message.textContent="";progress.hidden=false;status.textContent="圖片處理中…";bar.style.width="0%"; const ready=await prepare(selected,root.dataset.profile); status.textContent="上傳中 0%"; const pre=await api(cfg.presignUrl,{category:root.dataset.category,filename:ready.file.name,content_type:ready.file.type,size:ready.file.size,width:ready.width,height:ready.height}); await put(pre.upload_url,ready.file,p=>{bar.style.width=`${p}%`;status.textContent=`上傳中 ${p}%`;}); status.textContent="確認中…"; const done=await api(cfg.completeUrl,{category:root.dataset.category,upload_id:pre.upload_id,object_key:pre.object_key}); hidden.value=`upload:${done.token}`; if(localUrl)URL.revokeObjectURL(localUrl);localUrl=URL.createObjectURL(ready.file);img.src=localUrl;preview.hidden=false;remove.hidden=false;progress.hidden=true;message.textContent="圖片已準備完成，儲存表單後生效。"; } catch(error) { showError(error.message||"圖片上傳失敗，請重試。"); }};
    input.addEventListener("change",()=>{selected=input.files[0]||null;if(selected)upload();input.value="";}); retry.addEventListener("click",upload); remove.addEventListener("click",()=>{hidden.value="";preview.hidden=true;remove.hidden=true;retry.hidden=true;message.textContent="儲存後將移除圖片。";selected=null;});
  });
})();
