(() => {
  const ROOM = window.CHAT_CONFIG.room;
  const USERNAME = window.CHAT_CONFIG.username;
  const $ = id => document.getElementById(id);
  const EMOJIS = ["😀","😃","😄","😁","😆","😅","😂","🤣","😊","🥰","😍","😘","😚","😋","😜","🤪","🤗","🤭","😎","🥳","😏","😒","🙄","🥺","😭","😡","🤯","😴","🤔","🫡","🤝","👏","🙌","🙏","👍","👎","❤️","🩷","🧡","💛","💚","💙","💜","🖤","🤍","💔","💕","💞","💓","💗","💖","💘","💝","🔥","✨","⭐","🌙","☀️","🌹","🌸","🍕","🍔","🍫","☕","🍻","🎂","🎉","🎁","🎵","🎮","📷","🐶","🐱","🐻","🦊","🐼","🙈","🙉","🙊","💋","💌","🏠","🚗","✈️","✅","❌","⚠️","💯"];

  let lastId = 0;
  let lastEventId = 0;
  let partnerLastReadId = 0;
  let loadingMessages = false;
  let loadingChanges = false;
  let markingRead = false;
  let replyTo = null;
  let editingMessage = null;
  let editDraftBackup = "";
  const seen = new Set();
  const messageById = new Map();
  let originalImage = null;
  let stickerObjectUrl = null;
  let renderTimer = null;
  let localTyping = false;
  let typingStopTimer = null;
  let lastTypingPingAt = 0;
  let pendingAttachment = null;
  let fofocaImage = null;
  let fofocaObjectUrl = null;
  let fofocaFrames = [];
  const fofocaOverlayCache = new Map();

  const messagesEl = $("messages"), form = $("messageForm"), input = $("messageInput");
  const statusBar = $("statusBar"), notifyBtn = $("notifyBtn"), installBtn = $("installBtn");
  const toolDrawer = $("toolDrawer"), emojiPanel = $("emojiPanel"), stickersPanel = $("stickersPanel"), emojiGrid = $("emojiGrid");
  const stickerGallery = $("stickerGallery"), emptyStickers = $("emptyStickers");
  const canvas = $("stickerCanvas"), ctx = canvas.getContext("2d", {willReadFrequently:true}), canvasHint = $("canvasHint");
  const fileInput = $("stickerFile"), opacityRange = $("opacityRange"), thresholdRange = $("thresholdRange"), softnessRange = $("softnessRange"), removeMode = $("removeMode");
  const replyPreview = $("replyPreview"), replyPreviewAuthor = $("replyPreviewAuthor"), replyPreviewText = $("replyPreviewText");
  const editPreview = $("editPreview"), editPreviewText = $("editPreviewText");
  const typingIndicator = $("typingIndicator");
  const attachBtn = $("attachBtn"), attachmentMenu = $("attachmentMenu");
  const attachImageBtn = $("attachImageBtn"), attachVideoBtn = $("attachVideoBtn"), attachDocBtn = $("attachDocBtn");
  const attachImageInput = $("attachImageInput"), attachVideoInput = $("attachVideoInput"), attachDocInput = $("attachDocInput");
  const attachmentPreview = $("attachmentPreview"), attachmentPreviewTitle = $("attachmentPreviewTitle"), attachmentPreviewText = $("attachmentPreviewText");
  const sendAttachmentBtn = $("sendAttachmentBtn"), cancelAttachmentBtn = $("cancelAttachmentBtn");
  const attachFofocaBtn = $("attachFofocaBtn");
  const fofocaComposer = $("fofocaComposer"), fofocaFile = $("fofocaFile"), fofocaFrameSelect = $("fofocaFrameSelect"), fofocaHeadline = $("fofocaHeadline");
  const fofocaCanvas = $("fofocaCanvas"), fofocaCtx = fofocaCanvas?.getContext("2d"), fofocaHint = $("fofocaHint");
  const sendFofocaBtn = $("sendFofocaBtn"), cancelFofocaBtn = $("cancelFofocaBtn"), resetFofocaBtn = $("resetFofocaBtn"), backFofocaBtn = $("backFofocaBtn");

  function showPartnerTyping(isTyping) {
    if (!typingIndicator) return;
    typingIndicator.classList.toggle("hidden", !isTyping);
  }

  async function sendTypingState(typing, keepalive=false) {
    if (typing === localTyping && typing && (Date.now() - lastTypingPingAt) < 1500) return;
    localTyping = typing;
    if (typing) lastTypingPingAt = Date.now();
    try {
      await fetch(`/api/typing/${ROOM}`, {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({typing}),
        keepalive
      });
    } catch (_) {}
  }

  function typingActivity() {
    if (document.visibilityState !== "visible") return;
    const hasText = input.value.trim().length > 0;
    if (!hasText) {
      if (typingStopTimer) clearTimeout(typingStopTimer);
      typingStopTimer = null;
      sendTypingState(false);
      return;
    }
    sendTypingState(true);
    if (typingStopTimer) clearTimeout(typingStopTimer);
    typingStopTimer = setTimeout(() => sendTypingState(false), 2200);
  }

  function stopTyping(keepalive=false) {
    if (typingStopTimer) clearTimeout(typingStopTimer);
    typingStopTimer = null;
    if (localTyping) sendTypingState(false, keepalive);
  }

  function status(message, error=false, timeout=4200) {
    statusBar.textContent = message;
    statusBar.classList.remove("hidden", "error");
    if (error) statusBar.classList.add("error");
    if (timeout) setTimeout(() => statusBar.classList.add("hidden"), timeout);
  }

  function refreshNotify() { notifyBtn.textContent = NossaSala.notificationLabel(); }
  notifyBtn.addEventListener("click", async () => {
    try {
      await NossaSala.enablePush();
      refreshNotify();
      status("Notificações ativadas neste aparelho.");
    } catch (err) {
      status(err.message, true, 8000);
    }
  });
  NossaSala.setupInstallButton(installBtn, msg => status(msg, false, 8000));
  refreshNotify();

  function formatBytes(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024) return `${n} B`;
    if (n < 1024*1024) return `${(n/1024).toFixed(1)} KB`;
    return `${(n/(1024*1024)).toFixed(1)} MB`;
  }

  function messageSummary(m) {
    if (m.deleted || m.kind === "deleted") return "Mensagem apagada";
    if (m.kind === "sticker") return "🖼️ Figurinha";
    if (["image","video","document"].includes(m.kind)) return m.text || "📎 Anexo";
    const text = (m.text || "").replace(/\s+/g, " ").trim();
    return text.length > 100 ? text.slice(0, 97) + "..." : text;
  }

  function hideAttachmentMenu() { if (attachmentMenu) attachmentMenu.classList.add("hidden"); }

  function clearAttachment() {
    pendingAttachment = null;
    if (attachmentPreview) attachmentPreview.classList.add("hidden");
    if (attachmentPreviewText) attachmentPreviewText.textContent = "";
    if (attachImageInput) attachImageInput.value = "";
    if (attachVideoInput) attachVideoInput.value = "";
    if (attachDocInput) attachDocInput.value = "";
  }

  function setPendingAttachment(file, kind) {
    if (!file) return;
    if (editingMessage) { status("Conclua ou cancele a edição antes de anexar um arquivo.", true); return; }
    pendingAttachment = {file, kind};
    const icon = kind === "image" ? "🖼️" : kind === "video" ? "🎞️" : "📎";
    attachmentPreviewTitle.textContent = `${icon} Anexo selecionado`;
    attachmentPreviewText.textContent = `${file.name} • ${formatBytes(file.size)}`;
    attachmentPreview.classList.remove("hidden");
    hideAttachmentMenu();
  }

  async function sendPendingAttachment() {
    if (!pendingAttachment) return;
    if (editingMessage) { status("Conclua ou cancele a edição antes de anexar um arquivo.", true); return; }
    const activeReply = replyTo;
    const fd = new FormData();
    fd.append("file", pendingAttachment.file);
    fd.append("device_id", NossaSala.deviceId || "");
    if (activeReply?.id) fd.append("reply_to_id", String(activeReply.id));
    sendAttachmentBtn.disabled = true;
    sendAttachmentBtn.textContent = "Enviando...";
    try {
      const r = await fetch(`/api/attachments/${ROOM}`, {method:"POST", body:fd});
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Falha ao enviar anexo.");
      clearAttachment();
      clearReply();
      addMessage(data);
      lastId = Math.max(lastId, data.id);
      await markRead();
      status("Anexo enviado.");
    } catch (err) {
      status(err.message, true, 6500);
    } finally {
      sendAttachmentBtn.disabled = false;
      sendAttachmentBtn.textContent = "Enviar anexo";
    }
  }

  function showFofocaComposer() {
    hideAttachmentMenu();
    loadFofocaFrames();
    clearAttachment();
    toolDrawer.classList.add("hidden");
    fofocaComposer?.classList.remove("hidden");
  }

  function clearFofoca(resetFields=true) {
    fofocaImage = null;
    if (fofocaObjectUrl) { URL.revokeObjectURL(fofocaObjectUrl); fofocaObjectUrl = null; }
    if (fofocaCtx) fofocaCtx.clearRect(0, 0, fofocaCanvas.width, fofocaCanvas.height);
    if (fofocaHint) fofocaHint.classList.remove("hidden");
    if (resetFields) {
      if (fofocaFile) fofocaFile.value = "";
      if (fofocaHeadline) fofocaHeadline.value = "";
      if (fofocaFrameSelect && fofocaFrames[0]) fofocaFrameSelect.value = fofocaFrames[0].id;
    }
  }

  function hideFofocaComposer(resetFields=true) {
    fofocaComposer?.classList.add("hidden");
    if (resetFields) clearFofoca(true);
  }

  function backFromFofoca() {
    hideFofocaComposer(false);
    hideAttachmentMenu();
    attachmentMenu?.classList.remove("hidden");
  }

  function wrapCanvasText(ctx, text, x, y, maxWidth, lineHeight, maxLines) {
    const words = String(text || "").trim().split(/\s+/).filter(Boolean);
    if (!words.length) return;
    const lines = [];
    let line = words.shift();
    for (const word of words) {
      const test = line + " " + word;
      if (ctx.measureText(test).width <= maxWidth) line = test;
      else { lines.push(line); line = word; }
    }
    if (line) lines.push(line);
    const finalLines = lines.slice(0, maxLines);
    if (lines.length > maxLines) {
      let last = finalLines[maxLines - 1];
      while (ctx.measureText(last + "…").width > maxWidth && last.length > 1) last = last.slice(0, -1);
      finalLines[maxLines - 1] = last + "…";
    }
    finalLines.forEach((ln, i) => ctx.fillText(ln, x, y + i * lineHeight));
  }

  async function loadFofocaFrames(force=false) {
    if (fofocaFrames.length && !force) return fofocaFrames;
    try {
      const r = await fetch('/api/fofoca-frames', {cache:'no-store'});
      if (!r.ok) throw new Error('Não foi possível carregar os modelos.');
      fofocaFrames = await r.json();
      const previous = fofocaFrameSelect?.value || '';
      fofocaFrameSelect.innerHTML = '';
      for (const frame of fofocaFrames) {
        const option = document.createElement('option');
        option.value = frame.id;
        option.textContent = frame.name;
        fofocaFrameSelect.appendChild(option);
      }
      if (previous && fofocaFrames.some(f => f.id === previous)) fofocaFrameSelect.value = previous;
      if (!fofocaFrameSelect.value && fofocaFrames[0]) fofocaFrameSelect.value = fofocaFrames[0].id;
      renderFofoca();
      return fofocaFrames;
    } catch (err) {
      status(err.message, true, 6500);
      return [];
    }
  }

  function currentFofocaFrame() {
    return fofocaFrames.find(frame => frame.id === fofocaFrameSelect?.value) || fofocaFrames[0] || null;
  }

  function drawCoverImage(ctx, img, rect) {
    const x = Number(rect?.x || 0), y = Number(rect?.y || 0);
    const width = Number(rect?.width || 1080), height = Number(rect?.height || 760);
    const iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height;
    const scale = Math.max(width / iw, height / ih);
    const dw = iw * scale, dh = ih * scale;
    const dx = x + (width - dw) / 2, dy = y + (height - dh) / 2;
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, width, height);
    ctx.clip();
    ctx.drawImage(img, dx, dy, dw, dh);
    ctx.restore();
  }

  function roundRectFill(ctx, x, y, width, height, radius, color) {
    ctx.fillStyle = color;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, y, width, height, radius);
    else ctx.rect(x, y, width, height);
    ctx.fill();
  }

  function loadOverlayForFrame(frame) {
    if (!frame?.overlay_url) return null;
    if (fofocaOverlayCache.has(frame.id)) return fofocaOverlayCache.get(frame.id);
    const img = new Image();
    const state = {img, loaded:false, failed:false};
    fofocaOverlayCache.set(frame.id, state);
    img.onload = () => { state.loaded = true; renderFofoca(); };
    img.onerror = () => { state.failed = true; status(`Não consegui carregar o overlay de ${frame.name}.`, true); };
    img.src = frame.overlay_url;
    return state;
  }

  function renderGeneratedFofoca(frame, headline) {
    const ctx = fofocaCtx;
    const header = frame.header || {};
    const card = frame.card || {};
    const badge = frame.badge || {};
    const headlineCfg = frame.headline || {};
    const footer = frame.footer || {};

    ctx.fillStyle = header.color || '#1f3a5f';
    ctx.fillRect(Number(header.x||0), Number(header.y||0), Number(header.width||1080), Number(header.height||118));
    ctx.textAlign = 'left';
    ctx.fillStyle = header.title_color || '#ffffff';
    ctx.font = '700 58px Inter, Arial, sans-serif';
    ctx.fillText(header.title || 'FOFOCA', 64, 78);
    ctx.font = '600 28px Inter, Arial, sans-serif';
    ctx.globalAlpha = .9;
    ctx.fillText(header.subtitle || '', 66, 106);
    ctx.globalAlpha = 1;

    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,.28)';
    ctx.shadowBlur = 26;
    roundRectFill(ctx, Number(card.x||52), Number(card.y||790), Number(card.width||976), Number(card.height||470), Number(card.radius||34), card.background || '#ffffff');
    ctx.restore();

    roundRectFill(ctx, Number(badge.x||86), Number(badge.y||818), Number(badge.width||280), Number(badge.height||56), 20, badge.background || '#d9e7ff');
    ctx.fillStyle = badge.color || '#163152';
    ctx.font = '700 28px Inter, Arial, sans-serif';
    ctx.fillText(badge.text || 'NOTÍCIA', Number(badge.x||86) + 28, Number(badge.y||818) + 36);

    drawHeadline(frame, headline);

    ctx.fillStyle = footer.color || '#475569';
    ctx.font = `500 ${Number(footer.font_size||24)}px Inter, Arial, sans-serif`;
    ctx.fillText(footer.text || 'Compartilhado no Nossa Sala', Number(footer.x||96), Number(footer.y||1218));
  }

  function drawHeadline(frame, headline) {
    const cfg = frame.headline || {};
    const weight = Number(cfg.font_weight || 800);
    const fontSize = Number(cfg.font_size || 70);
    fofocaCtx.fillStyle = cfg.color || '#ffffff';
    fofocaCtx.font = `${weight} ${fontSize}px Inter, Arial, sans-serif`;
    fofocaCtx.textAlign = 'left';
    const value = cfg.uppercase === false ? headline : headline.toUpperCase();
    wrapCanvasText(
      fofocaCtx,
      value,
      Number(cfg.x || 90),
      Number(cfg.y || 1010),
      Number(cfg.max_width || 900),
      Number(cfg.line_height || 78),
      Number(cfg.max_lines || 3)
    );
  }

  function renderFofoca() {
    if (!fofocaCtx || !fofocaCanvas) return;
    const frame = currentFofocaFrame();
    if (!frame) {
      fofocaCtx.clearRect(0, 0, fofocaCanvas.width, fofocaCanvas.height);
      return;
    }
    const canvasCfg = frame.canvas || {};
    const w = Number(canvasCfg.width || 1080), h = Number(canvasCfg.height || 1350);
    if (fofocaCanvas.width !== w) fofocaCanvas.width = w;
    if (fofocaCanvas.height !== h) fofocaCanvas.height = h;
    const headline = (fofocaHeadline?.value || 'A sua notícia aparece aqui').trim() || 'A sua notícia aparece aqui';
    fofocaCtx.clearRect(0, 0, w, h);
    fofocaCtx.fillStyle = canvasCfg.background || '#0f1720';
    fofocaCtx.fillRect(0, 0, w, h);

    if (fofocaImage) {
      drawCoverImage(fofocaCtx, fofocaImage, frame.photo || {x:0,y:0,width:w,height:760});
      if (fofocaHint) fofocaHint.classList.add('hidden');
    } else {
      const photo = frame.photo || {x:0,y:0,width:w,height:760};
      fofocaCtx.fillStyle = '#1a2330';
      fofocaCtx.fillRect(Number(photo.x||0), Number(photo.y||0), Number(photo.width||w), Number(photo.height||760));
      fofocaCtx.fillStyle = 'rgba(255,255,255,.22)';
      fofocaCtx.font = '600 44px Inter, Arial, sans-serif';
      fofocaCtx.textAlign = 'center';
      fofocaCtx.fillText('Escolha uma imagem', Number(photo.x||0) + Number(photo.width||w)/2, Number(photo.y||0) + Number(photo.height||760)/2);
      if (fofocaHint) fofocaHint.classList.remove('hidden');
    }

    if (frame.mode === 'overlay') {
      const overlayState = loadOverlayForFrame(frame);
      if (overlayState?.loaded) fofocaCtx.drawImage(overlayState.img, 0, 0, w, h);
      drawHeadline(frame, headline);
    } else {
      // Gradiente suave preservado nos modelos gerados.
      const photo = frame.photo || {x:0,y:0,width:w,height:760};
      const g = fofocaCtx.createLinearGradient(0, Number(photo.y||0)+Number(photo.height||760)*.66, 0, Number(photo.y||0)+Number(photo.height||760)+60);
      g.addColorStop(0, 'rgba(15,23,32,0)');
      g.addColorStop(1, 'rgba(15,23,32,0.55)');
      fofocaCtx.fillStyle = g;
      fofocaCtx.fillRect(Number(photo.x||0), Number(photo.y||0)+Number(photo.height||760)*.62, Number(photo.width||w), Number(photo.height||760)*.45);
      renderGeneratedFofoca(frame, headline);
    }
  }

  async function sendFofocaCard() {
    if (!fofocaImage) { status('Escolha uma imagem para a fofoca.', true); return; }
    renderFofoca();
    sendFofocaBtn.disabled = true;
    sendFofocaBtn.textContent = 'Gerando...';
    try {
      const blob = await new Promise(resolve => fofocaCanvas.toBlob(resolve, 'image/png', 0.95));
      if (!blob) throw new Error('Não foi possível gerar a imagem da fofoca.');
      const fd = new FormData();
      fd.append('file', new File([blob], `fofoca-${Date.now()}.png`, {type:'image/png'}));
      fd.append('device_id', NossaSala.deviceId || '');
      if (replyTo?.id) fd.append('reply_to_id', String(replyTo.id));
      const r = await fetch(`/api/attachments/${ROOM}`, {method:'POST', body:fd});
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || 'Falha ao enviar fofoca.');
      addMessage(data);
      lastId = Math.max(lastId, data.id);
      clearReply();
      hideFofocaComposer(true);
      await markRead();
      status('Fofoca enviada.');
    } catch (err) {
      status(err.message, true, 6500);
    } finally {
      sendFofocaBtn.disabled = false;
      sendFofocaBtn.textContent = 'Gerar e enviar';
    }
  }

  function clearReply() {
    replyTo = null;
    replyPreview.classList.add("hidden");
    replyPreviewAuthor.textContent = "";
    replyPreviewText.textContent = "";
  }

  function clearEdit(restoreDraft=false) {
    if (!editingMessage) return;
    editingMessage = null;
    editPreview.classList.add("hidden");
    editPreviewText.textContent = "";
    input.value = restoreDraft ? editDraftBackup : "";
    editDraftBackup = "";
  }

  function selectReply(m) {
    if (m.deleted || m.kind === "deleted") return;
    if (editingMessage) clearEdit(true);
    replyTo = {id:m.id, author:m.author, kind:m.kind, text:messageSummary(m)};
    replyPreviewAuthor.textContent = `Respondendo a @${m.author}`;
    replyPreviewText.textContent = replyTo.text || "Mensagem";
    replyPreview.classList.remove("hidden");
    input.focus();
  }

  function selectEdit(m) {
    if (m.author !== USERNAME || m.kind !== "text" || m.deleted) return;
    clearReply();
    editDraftBackup = input.value;
    editingMessage = {id:m.id, text:m.text};
    editPreviewText.textContent = messageSummary(m);
    editPreview.classList.remove("hidden");
    input.value = m.text || "";
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }

  $("cancelReplyBtn").addEventListener("click", clearReply);
  $("cancelEditBtn").addEventListener("click", () => { clearEdit(true); input.focus(); });

  function scrollToMessage(id) {
    const row = messagesEl.querySelector(`[data-message-id="${id}"]`);
    if (!row) return;
    row.scrollIntoView({behavior:"smooth", block:"center"});
    row.classList.add("message-highlight");
    setTimeout(() => row.classList.remove("message-highlight"), 1200);
  }

  function updateReceipts() {
    document.querySelectorAll(".msg-row.mine .receipt").forEach(el => {
      const id = Number(el.dataset.messageId || 0);
      const read = id > 0 && id <= partnerLastReadId;
      el.textContent = read ? "✓✓ Visualizada" : "✓ Enviada";
      el.classList.toggle("read", read);
    });
  }

  async function deleteMessage(m) {
    if (m.author !== USERNAME || m.deleted) return;
    if (!confirm("Apagar esta mensagem? Ela ficará marcada como ‘Mensagem apagada’.")) return;
    try {
      const r = await fetch(`/api/messages/${ROOM}/${m.id}`, {method:"DELETE"});
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Não foi possível apagar a mensagem.");
      if (editingMessage?.id === m.id) clearEdit(false);
      if (replyTo?.id === m.id) clearReply();
      applyMessageUpdate(data);
      status("Mensagem apagada.");
    } catch (err) {
      status(err.message, true);
    }
  }

  function buildMessageRow(m) {
    const mine = m.author === USERNAME;
    const row = document.createElement("div");
    row.className = "msg-row " + (mine ? "mine" : "other");
    row.dataset.messageId = String(m.id);

    const bubble = document.createElement("div");
    const isSticker = m.kind === "sticker" && !m.deleted;
    bubble.className = "bubble" + (isSticker ? " sticker-bubble" : "") + (m.deleted ? " deleted-bubble" : "");

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${m.author} • ${m.time}${m.edited ? " • editada" : ""}`;
    bubble.appendChild(meta);

    if (m.reply) {
      const quote = document.createElement("button");
      quote.type = "button";
      quote.className = "reply-quote";
      const who = document.createElement("strong");
      who.textContent = `@${m.reply.author}`;
      const preview = document.createElement("span");
      preview.textContent = m.reply.kind === "sticker" ? "🖼️ Figurinha" : (m.reply.text || "Mensagem");
      quote.append(who, preview);
      quote.addEventListener("click", () => scrollToMessage(m.reply.id));
      bubble.appendChild(quote);
    }

    if (m.deleted || m.kind === "deleted") {
      const text = document.createElement("div");
      text.className = "msg-text deleted-message";
      text.textContent = "Mensagem apagada";
      bubble.appendChild(text);
    } else if (m.kind === "sticker") {
      const img = document.createElement("img");
      img.className = "chat-sticker";
      img.src = m.sticker_url;
      img.alt = `Figurinha enviada por ${m.author}`;
      bubble.appendChild(img);
    } else if (["image","video","document"].includes(m.kind)) {
      const card = document.createElement("div");
      card.className = "attachment-card";
      if (m.kind === "image") {
        const link = document.createElement("a");
        link.href = m.attachment_url;
        link.target = "_blank";
        link.rel = "noopener";
        const img = document.createElement("img");
        img.className = "attachment-image";
        img.src = m.attachment_url;
        img.alt = m.attachment_name || "Imagem";
        link.appendChild(img);
        card.appendChild(link);
      } else if (m.kind === "video") {
        const video = document.createElement("video");
        video.className = "attachment-video";
        video.src = m.attachment_url;
        video.controls = true;
        video.preload = "metadata";
        card.appendChild(video);
      }
      const open = document.createElement("a");
      open.className = "attachment-open";
      open.href = m.attachment_url;
      open.target = "_blank";
      open.rel = "noopener";
      const metaWrap = document.createElement("div");
      const name = document.createElement("div");
      name.className = "attachment-name";
      name.textContent = m.attachment_name || (m.kind === "document" ? "Documento" : "Anexo");
      const metaLine = document.createElement("small");
      metaLine.textContent = `${m.kind === "image" ? "Imagem" : m.kind === "video" ? "Vídeo" : "Documento"} • ${formatBytes(m.attachment_size)}`;
      metaWrap.append(name, metaLine);
      open.textContent = m.kind === "document" ? "📎 " : (m.kind === "image" ? "🖼️ " : "🎞️ ");
      open.appendChild(metaWrap);
      card.appendChild(open);
      bubble.appendChild(card);
    } else {
      const text = document.createElement("div");
      text.className = "msg-text";
      text.textContent = m.text;
      bubble.appendChild(text);
    }

    const footer = document.createElement("div");
    footer.className = "message-footer";

    if (!m.deleted && m.kind !== "deleted") {
      const replyBtn = document.createElement("button");
      replyBtn.type = "button";
      replyBtn.className = "reply-message-btn";
      replyBtn.textContent = "↩ Responder";
      replyBtn.addEventListener("click", () => selectReply(m));
      footer.appendChild(replyBtn);
    }

    if (mine && !m.deleted) {
      if (m.kind === "text") {
        const editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "message-action-btn";
        editBtn.textContent = "✎ Editar";
        editBtn.addEventListener("click", () => selectEdit(m));
        footer.appendChild(editBtn);
      }
      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "message-action-btn delete-message-btn";
      deleteBtn.textContent = "🗑 Apagar";
      deleteBtn.addEventListener("click", () => deleteMessage(m));
      footer.appendChild(deleteBtn);
    }

    if (mine) {
      const receipt = document.createElement("span");
      receipt.className = "receipt";
      receipt.dataset.messageId = String(m.id);
      footer.appendChild(receipt);
    }

    bubble.appendChild(footer);
    row.appendChild(bubble);
    return row;
  }

  function renderExistingMessage(m) {
    messageById.set(m.id, m);
    const oldRow = messagesEl.querySelector(`[data-message-id="${m.id}"]`);
    if (oldRow) oldRow.replaceWith(buildMessageRow(m));
  }

  function applyMessageUpdate(m) {
    messageById.set(m.id, m);
    renderExistingMessage(m);

    const replacementReply = {
      id: m.id,
      author: m.author,
      kind: m.deleted ? "deleted" : m.kind,
      text: messageSummary(m)
    };

    for (const child of messageById.values()) {
      if (child.id !== m.id && child.reply?.id === m.id) {
        child.reply = replacementReply;
        renderExistingMessage(child);
      }
    }
    updateReceipts();
  }

  function addMessage(m) {
    if (seen.has(m.id)) {
      applyMessageUpdate(m);
      return;
    }
    seen.add(m.id);
    messageById.set(m.id, m);
    messagesEl.appendChild(buildMessageRow(m));
    updateReceipts();
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  async function markRead() {
    if (markingRead || lastId <= 0 || document.visibilityState !== "visible") return;
    markingRead = true;
    try {
      await fetch(`/api/messages/${ROOM}/read`, {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({last_read_id:lastId})
      });
    } catch (_) {
    } finally {
      markingRead = false;
    }
  }

  async function loadChanges() {
    if (loadingChanges) return;
    loadingChanges = true;
    try {
      const r = await fetch(`/api/messages/${ROOM}/changes?after_event=${lastEventId}`, {cache:"no-store"});
      if (!r.ok) return;
      const payload = await r.json();
      for (const change of (payload.changes || [])) {
        lastEventId = Math.max(lastEventId, Number(change.event_id || 0));
        if (seen.has(change.message.id)) applyMessageUpdate(change.message);
      }
    } catch (_) {
    } finally {
      loadingChanges = false;
    }
  }

  async function loadMessages() {
    if (loadingMessages) return;
    loadingMessages = true;
    try {
      const r = await fetch(`/api/messages/${ROOM}?after=${lastId}`, {cache:"no-store"});
      if (r.status === 401) { location.href = "/login"; return; }
      if (!r.ok) return;
      const payload = await r.json();
      partnerLastReadId = Number(payload.partner_last_read_id || 0);
      showPartnerTyping(Boolean(payload.partner_typing));
      const data = Array.isArray(payload) ? payload : (payload.messages || []);
      for (const m of data) {
        addMessage(m);
        lastId = Math.max(lastId, m.id);
      }
      await loadChanges();
      updateReceipts();
      await markRead();
    } catch (_) {
    } finally {
      loadingMessages = false;
    }
  }

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    stopTyping();

    if (editingMessage) {
      const activeEdit = editingMessage;
      try {
        const r = await fetch(`/api/messages/${ROOM}/${activeEdit.id}`, {
          method:"PATCH",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({text})
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || "Não foi possível editar.");
        clearEdit(false);
        applyMessageUpdate(data);
        status("Mensagem editada.");
        input.focus();
      } catch (err) {
        status(err.message, true);
      }
      return;
    }

    const activeReply = replyTo;
    input.value = "";
    input.focus();
    try {
      const r = await fetch(`/api/messages/${ROOM}`, {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({text, device_id:NossaSala.deviceId, reply_to_id:activeReply?.id || null})
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Não foi possível enviar.");
      clearReply();
      addMessage(data);
      lastId = Math.max(lastId, data.id);
      await markRead();
    } catch (err) {
      input.value = text;
      if (activeReply) {
        replyTo = activeReply;
        replyPreviewAuthor.textContent = `Respondendo a @${activeReply.author}`;
        replyPreviewText.textContent = activeReply.text;
        replyPreview.classList.remove("hidden");
      }
      status(err.message, true);
    }
  });

  input.addEventListener("input", typingActivity);
  input.addEventListener("blur", () => stopTyping());
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      loadMessages();
      setTimeout(markRead, 150);
      if (input.value.trim()) typingActivity();
    } else {
      stopTyping(true);
      showPartnerTyping(false);
    }
  });
  window.addEventListener("pagehide", () => stopTyping(true));
  window.addEventListener("focus", () => { loadMessages(); setTimeout(markRead, 150); });

  EMOJIS.forEach(emoji => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "emoji-item";
    btn.textContent = emoji;
    btn.addEventListener("click", () => {
      const s = input.selectionStart ?? input.value.length;
      const e = input.selectionEnd ?? input.value.length;
      input.value = input.value.slice(0, s) + emoji + input.value.slice(e);
      input.focus();
      input.setSelectionRange(s + emoji.length, s + emoji.length);
    });
    emojiGrid.appendChild(btn);
  });

  attachBtn?.addEventListener("click", e => {
    e.stopPropagation();
    attachmentMenu.classList.toggle("hidden");
  });
  attachImageBtn?.addEventListener("click", () => attachImageInput.click());
  attachFofocaBtn?.addEventListener("click", showFofocaComposer);
  attachVideoBtn?.addEventListener("click", () => attachVideoInput.click());
  attachDocBtn?.addEventListener("click", () => attachDocInput.click());
  attachImageInput?.addEventListener("change", () => setPendingAttachment(attachImageInput.files?.[0], "image"));
  attachVideoInput?.addEventListener("change", () => setPendingAttachment(attachVideoInput.files?.[0], "video"));
  attachDocInput?.addEventListener("change", () => setPendingAttachment(attachDocInput.files?.[0], "document"));
  sendAttachmentBtn?.addEventListener("click", sendPendingAttachment);
  cancelFofocaBtn?.addEventListener("click", () => hideFofocaComposer(true));
  backFofocaBtn?.addEventListener("click", backFromFofoca);
  resetFofocaBtn?.addEventListener("click", () => { clearFofoca(true); renderFofoca(); });
  sendFofocaBtn?.addEventListener("click", sendFofocaCard);
  fofocaFrameSelect?.addEventListener("change", renderFofoca);
  fofocaHeadline?.addEventListener("input", renderFofoca);
  fofocaFile?.addEventListener("change", () => {
    const file = fofocaFile.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) { status("Escolha uma imagem para a fofoca.", true); return; }
    if (fofocaObjectUrl) URL.revokeObjectURL(fofocaObjectUrl);
    fofocaObjectUrl = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { fofocaImage = img; renderFofoca(); };
    img.onerror = () => status("Não consegui abrir a imagem da fofoca.", true);
    img.src = fofocaObjectUrl;
  });
  cancelAttachmentBtn?.addEventListener("click", clearAttachment);
  document.addEventListener("click", e => { if (!e.target.closest(".attach-menu-wrap")) hideAttachmentMenu(); });

  function showTool(tab) {
    hideFofocaComposer(false);
    toolDrawer.classList.remove("hidden");
    document.querySelectorAll(".tool-tab").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
    emojiPanel.classList.toggle("hidden", tab !== "emoji");
    stickersPanel.classList.toggle("hidden", tab !== "stickers");
    if (tab === "stickers") loadStickers();
  }
  $("emojiBtn").addEventListener("click", () => showTool("emoji"));
  loadFofocaFrames();
  $("stickerBtn").addEventListener("click", () => showTool("stickers"));
  document.querySelectorAll(".tool-tab").forEach(btn => btn.addEventListener("click", () => showTool(btn.dataset.tab)));
  $("closeToolsBtn").addEventListener("click", () => toolDrawer.classList.add("hidden"));

  function scheduleRender() { clearTimeout(renderTimer); renderTimer = setTimeout(renderSticker, 35); }
  [opacityRange, thresholdRange, softnessRange].forEach(el => el.addEventListener("input", () => {
    $("opacityValue").textContent = `${opacityRange.value}%`;
    $("thresholdValue").textContent = thresholdRange.value;
    $("softnessValue").textContent = softnessRange.value;
    scheduleRender();
  }));
  removeMode.addEventListener("change", () => {
    if (removeMode.value === "dark") thresholdRange.value = "45";
    if (removeMode.value === "light") thresholdRange.value = "210";
    $("thresholdValue").textContent = thresholdRange.value;
    scheduleRender();
  });
  fileInput.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) { status("Escolha uma imagem.", true); return; }
    if (stickerObjectUrl) URL.revokeObjectURL(stickerObjectUrl);
    stickerObjectUrl = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { originalImage = img; canvasHint.classList.add("hidden"); renderSticker(); };
    img.onerror = () => status("Não consegui abrir a imagem.", true);
    img.src = stickerObjectUrl;
  });
  $("resetStickerBtn").addEventListener("click", () => {
    opacityRange.value = "100";
    thresholdRange.value = "45";
    softnessRange.value = "28";
    removeMode.value = "none";
    $("opacityValue").textContent = "100%";
    $("thresholdValue").textContent = "45";
    $("softnessValue").textContent = "28";
    if (originalImage) renderSticker();
  });

  function renderSticker() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!originalImage) return;
    const iw = originalImage.naturalWidth || originalImage.width;
    const ih = originalImage.naturalHeight || originalImage.height;
    const scale = Math.max(canvas.width / iw, canvas.height / ih);
    const dw = iw * scale, dh = ih * scale;
    const dx = (canvas.width - dw) / 2, dy = (canvas.height - dh) / 2;
    ctx.save();
    ctx.globalAlpha = Number(opacityRange.value) / 100;
    ctx.drawImage(originalImage, dx, dy, dw, dh);
    ctx.restore();
    const mode = removeMode.value;
    if (mode === "none") return;
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const data = imageData.data;
    const threshold = Number(thresholdRange.value);
    const softness = Math.max(1, Number(softnessRange.value));
    for (let i = 0; i < data.length; i += 4) {
      const lum = .2126 * data[i] + .7152 * data[i+1] + .0722 * data[i+2];
      let factor = 1;
      if (mode === "dark") {
        if (lum <= threshold) factor = 0;
        else if (lum < threshold + softness) factor = (lum - threshold) / softness;
      } else {
        if (lum >= threshold) factor = 0;
        else if (lum > threshold - softness) factor = (threshold - lum) / softness;
      }
      data[i+3] = Math.round(data[i+3] * Math.max(0, Math.min(1, factor)));
    }
    ctx.putImageData(imageData, 0, 0);
  }

  $("saveStickerBtn").addEventListener("click", () => {
    if (!originalImage) { status("Escolha uma foto antes de salvar.", true); return; }
    renderSticker();
    canvas.toBlob(async blob => {
      if (!blob) { status("Não consegui gerar a figurinha.", true); return; }
      const fd = new FormData();
      fd.append("image", blob, "figurinha.webp");
      const btn = $("saveStickerBtn");
      btn.disabled = true;
      btn.textContent = "Salvando...";
      try {
        const r = await fetch(`/api/stickers/${ROOM}`, {method:"POST", body:fd});
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || "Falha ao salvar.");
        status("Figurinha salva.");
        await loadStickers();
      } catch (err) {
        status(err.message, true);
      } finally {
        btn.disabled = false;
        btn.textContent = "Salvar figurinha";
      }
    }, "image/webp", .88);
  });

  async function loadStickers() {
    try {
      const r = await fetch(`/api/stickers/${ROOM}`, {cache:"no-store"});
      if (!r.ok) return;
      const stickers = await r.json();
      stickerGallery.innerHTML = "";
      emptyStickers.classList.toggle("hidden", stickers.length > 0);
      for (const sticker of stickers) {
        const item = document.createElement("div");
        item.className = "saved-sticker-item";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "saved-sticker";
        const img = document.createElement("img");
        img.src = sticker.url;
        img.alt = "Figurinha";
        btn.appendChild(img);
        btn.addEventListener("click", () => sendSticker(sticker.token));
        item.appendChild(btn);
        if (sticker.owner === USERNAME) {
          const del = document.createElement("button");
          del.type = "button";
          del.className = "delete-sticker";
          del.textContent = "×";
          del.addEventListener("click", async e => {
            e.stopPropagation();
            if (!confirm("Excluir esta figurinha?")) return;
            const res = await fetch(`/api/stickers/${ROOM}/${sticker.token}`, {method:"DELETE"});
            if (res.ok) loadStickers();
          });
          item.appendChild(del);
        }
        stickerGallery.appendChild(item);
      }
    } catch (_) {}
  }

  async function sendSticker(token) {
    if (editingMessage) {
      status("Conclua ou cancele a edição antes de enviar uma figurinha.", true);
      return;
    }
    const activeReply = replyTo;
    try {
      const r = await fetch(`/api/sticker-message/${ROOM}`, {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({token, device_id:NossaSala.deviceId, reply_to_id:activeReply?.id || null})
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Falha ao enviar.");
      clearReply();
      addMessage(data);
      lastId = Math.max(lastId, data.id);
      toolDrawer.classList.add("hidden");
    } catch (err) {
      status(err.message, true);
    }
  }

  loadMessages();
  loadStickers();
  input.focus();
  setInterval(loadMessages, 1200);
  setInterval(() => NossaSala.ping(), 20000);
})();
