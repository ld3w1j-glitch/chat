(() => {
  const ROOM = window.CHAT_CONFIG.room;
  const USERNAME = window.CHAT_CONFIG.username;
  const $ = id => document.getElementById(id);
  const EMOJIS = ["😀","😃","😄","😁","😆","😅","😂","🤣","😊","🥰","😍","😘","😚","😋","😜","🤪","🤗","🤭","😎","🥳","😏","😒","🙄","🥺","😭","😡","🤯","😴","🤔","🫡","🤝","👏","🙌","🙏","👍","👎","❤️","🩷","🧡","💛","💚","💙","💜","🖤","🤍","💔","💕","💞","💓","💗","💖","💘","💝","🔥","✨","⭐","🌙","☀️","🌹","🌸","🍕","🍔","🍫","☕","🍻","🎂","🎉","🎁","🎵","🎮","📷","🐶","🐱","🐻","🦊","🐼","🙈","🙉","🙊","💋","💌","🏠","🚗","✈️","✅","❌","⚠️","💯"];

  let lastId = 0;
  let partnerLastReadId = 0;
  let loadingMessages = false;
  let markingRead = false;
  let replyTo = null;
  const seen = new Set();
  const messageById = new Map();
  let originalImage = null;
  let stickerObjectUrl = null;
  let renderTimer = null;

  const messagesEl = $("messages"), form = $("messageForm"), input = $("messageInput");
  const statusBar = $("statusBar"), notifyBtn = $("notifyBtn"), installBtn = $("installBtn");
  const toolDrawer = $("toolDrawer"), emojiPanel = $("emojiPanel"), stickersPanel = $("stickersPanel"), emojiGrid = $("emojiGrid");
  const stickerGallery = $("stickerGallery"), emptyStickers = $("emptyStickers");
  const canvas = $("stickerCanvas"), ctx = canvas.getContext("2d", {willReadFrequently:true}), canvasHint = $("canvasHint");
  const fileInput = $("stickerFile"), opacityRange = $("opacityRange"), thresholdRange = $("thresholdRange"), softnessRange = $("softnessRange"), removeMode = $("removeMode");
  const replyPreview = $("replyPreview"), replyPreviewAuthor = $("replyPreviewAuthor"), replyPreviewText = $("replyPreviewText");

  function status(message, error=false, timeout=4200) {
    statusBar.textContent = message; statusBar.classList.remove("hidden","error");
    if (error) statusBar.classList.add("error");
    if (timeout) setTimeout(() => statusBar.classList.add("hidden"), timeout);
  }
  function refreshNotify() { notifyBtn.textContent = NossaSala.notificationLabel(); }
  notifyBtn.addEventListener("click", async () => {
    try { await NossaSala.enablePush(); refreshNotify(); status("Notificações ativadas neste aparelho."); }
    catch (err) { status(err.message, true, 8000); }
  });
  NossaSala.setupInstallButton(installBtn, msg => status(msg, false, 8000)); refreshNotify();

  function messageSummary(m) {
    if (m.kind === "sticker") return "🖼️ Figurinha";
    const text = (m.text || "").replace(/\s+/g, " ").trim();
    return text.length > 100 ? text.slice(0, 97) + "..." : text;
  }

  function clearReply() {
    replyTo = null;
    replyPreview.classList.add("hidden");
    replyPreviewAuthor.textContent = "";
    replyPreviewText.textContent = "";
  }

  function selectReply(m) {
    replyTo = {id:m.id, author:m.author, kind:m.kind, text:messageSummary(m)};
    replyPreviewAuthor.textContent = `Respondendo a @${m.author}`;
    replyPreviewText.textContent = replyTo.text || "Mensagem";
    replyPreview.classList.remove("hidden");
    input.focus();
  }

  $("cancelReplyBtn").addEventListener("click", clearReply);

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

  function addMessage(m) {
    if (seen.has(m.id)) return;
    seen.add(m.id);
    messageById.set(m.id, m);
    const mine = m.author === USERNAME;
    const row = document.createElement("div");
    row.className = "msg-row " + (mine ? "mine" : "other");
    row.dataset.messageId = String(m.id);

    const bubble = document.createElement("div");
    bubble.className = "bubble" + (m.kind === "sticker" ? " sticker-bubble" : "");
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${m.author} • ${m.time}`;
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

    if (m.kind === "sticker") {
      const img = document.createElement("img");
      img.className = "chat-sticker";
      img.src = m.sticker_url;
      img.alt = `Figurinha enviada por ${m.author}`;
      bubble.appendChild(img);
    } else {
      const text = document.createElement("div");
      text.className = "msg-text";
      text.textContent = m.text;
      bubble.appendChild(text);
    }

    const footer = document.createElement("div");
    footer.className = "message-footer";
    const replyBtn = document.createElement("button");
    replyBtn.type = "button";
    replyBtn.className = "reply-message-btn";
    replyBtn.textContent = "↩ Responder";
    replyBtn.addEventListener("click", () => selectReply(m));
    footer.appendChild(replyBtn);
    if (mine) {
      const receipt = document.createElement("span");
      receipt.className = "receipt";
      receipt.dataset.messageId = String(m.id);
      footer.appendChild(receipt);
    }
    bubble.appendChild(footer);
    row.appendChild(bubble);
    messagesEl.appendChild(row);
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
    } catch (_) {} finally { markingRead = false; }
  }

  async function loadMessages() {
    if (loadingMessages) return;
    loadingMessages = true;
    try {
      const r = await fetch(`/api/messages/${ROOM}?after=${lastId}`, {cache:"no-store"});
      if (r.status === 401) { location.href="/login"; return; }
      if (!r.ok) return;
      const payload = await r.json();
      partnerLastReadId = Number(payload.partner_last_read_id || 0);
      const data = Array.isArray(payload) ? payload : (payload.messages || []);
      for (const m of data) { addMessage(m); lastId = Math.max(lastId, m.id); }
      updateReceipts();
      await markRead();
    } catch (_) {} finally { loadingMessages = false; }
  }

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
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
      await loadMessages();
    } catch (err) {
      input.value = text;
      if (activeReply) { replyTo = activeReply; replyPreviewAuthor.textContent = `Respondendo a @${activeReply.author}`; replyPreviewText.textContent = activeReply.text; replyPreview.classList.remove("hidden"); }
      status(err.message,true);
    }
  });
  input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { loadMessages(); setTimeout(markRead, 150); } });
  window.addEventListener("focus", () => { loadMessages(); setTimeout(markRead, 150); });

  EMOJIS.forEach(emoji => {
    const btn = document.createElement("button"); btn.type="button"; btn.className="emoji-item"; btn.textContent=emoji;
    btn.addEventListener("click", () => { const s=input.selectionStart??input.value.length,e=input.selectionEnd??input.value.length; input.value=input.value.slice(0,s)+emoji+input.value.slice(e); input.focus(); input.setSelectionRange(s+emoji.length,s+emoji.length); });
    emojiGrid.appendChild(btn);
  });
  function showTool(tab) {
    toolDrawer.classList.remove("hidden");
    document.querySelectorAll(".tool-tab").forEach(b=>b.classList.toggle("active",b.dataset.tab===tab));
    emojiPanel.classList.toggle("hidden",tab!=="emoji"); stickersPanel.classList.toggle("hidden",tab!=="stickers");
    if (tab === "stickers") loadStickers();
  }
  $("emojiBtn").addEventListener("click",()=>showTool("emoji")); $("stickerBtn").addEventListener("click",()=>showTool("stickers"));
  document.querySelectorAll(".tool-tab").forEach(btn=>btn.addEventListener("click",()=>showTool(btn.dataset.tab)));
  $("closeToolsBtn").addEventListener("click",()=>toolDrawer.classList.add("hidden"));

  function scheduleRender() { clearTimeout(renderTimer); renderTimer=setTimeout(renderSticker,35); }
  [opacityRange,thresholdRange,softnessRange].forEach(el=>el.addEventListener("input",()=>{
    $("opacityValue").textContent=`${opacityRange.value}%`; $("thresholdValue").textContent=thresholdRange.value; $("softnessValue").textContent=softnessRange.value; scheduleRender();
  }));
  removeMode.addEventListener("change",()=>{ if(removeMode.value==="dark")thresholdRange.value="45"; if(removeMode.value==="light")thresholdRange.value="210"; $("thresholdValue").textContent=thresholdRange.value; scheduleRender(); });
  fileInput.addEventListener("change",()=>{
    const file=fileInput.files?.[0]; if(!file)return; if(!file.type.startsWith("image/")){status("Escolha uma imagem.",true);return;}
    if(stickerObjectUrl)URL.revokeObjectURL(stickerObjectUrl); stickerObjectUrl=URL.createObjectURL(file); const img=new Image();
    img.onload=()=>{originalImage=img;canvasHint.classList.add("hidden");renderSticker();}; img.onerror=()=>status("Não consegui abrir a imagem.",true); img.src=stickerObjectUrl;
  });
  $("resetStickerBtn").addEventListener("click",()=>{opacityRange.value="100";thresholdRange.value="45";softnessRange.value="28";removeMode.value="none";$("opacityValue").textContent="100%";$("thresholdValue").textContent="45";$("softnessValue").textContent="28";if(originalImage)renderSticker();});

  function renderSticker() {
    ctx.clearRect(0,0,canvas.width,canvas.height); if(!originalImage)return;
    const iw=originalImage.naturalWidth||originalImage.width, ih=originalImage.naturalHeight||originalImage.height, scale=Math.max(canvas.width/iw,canvas.height/ih),dw=iw*scale,dh=ih*scale,dx=(canvas.width-dw)/2,dy=(canvas.height-dh)/2;
    ctx.save(); ctx.globalAlpha=Number(opacityRange.value)/100; ctx.drawImage(originalImage,dx,dy,dw,dh); ctx.restore();
    const mode=removeMode.value; if(mode==="none")return; const imageData=ctx.getImageData(0,0,canvas.width,canvas.height),data=imageData.data,threshold=Number(thresholdRange.value),softness=Math.max(1,Number(softnessRange.value));
    for(let i=0;i<data.length;i+=4){const lum=.2126*data[i]+.7152*data[i+1]+.0722*data[i+2];let factor=1;if(mode==="dark"){if(lum<=threshold)factor=0;else if(lum<threshold+softness)factor=(lum-threshold)/softness;}else{if(lum>=threshold)factor=0;else if(lum>threshold-softness)factor=(threshold-lum)/softness;}data[i+3]=Math.round(data[i+3]*Math.max(0,Math.min(1,factor)));}
    ctx.putImageData(imageData,0,0);
  }

  $("saveStickerBtn").addEventListener("click",()=>{
    if(!originalImage){status("Escolha uma foto antes de salvar.",true);return;} renderSticker();
    canvas.toBlob(async blob=>{
      if(!blob){status("Não consegui gerar a figurinha.",true);return;} const fd=new FormData(); fd.append("image",blob,"figurinha.webp"); const btn=$("saveStickerBtn");btn.disabled=true;btn.textContent="Salvando...";
      try{const r=await fetch(`/api/stickers/${ROOM}`,{method:"POST",body:fd});const data=await r.json();if(!r.ok)throw new Error(data.error||"Falha ao salvar.");status("Figurinha salva.");await loadStickers();}catch(err){status(err.message,true);}finally{btn.disabled=false;btn.textContent="Salvar figurinha";}
    },"image/webp",.88);
  });

  async function loadStickers(){
    try{const r=await fetch(`/api/stickers/${ROOM}`,{cache:"no-store"});if(!r.ok)return;const stickers=await r.json();stickerGallery.innerHTML="";emptyStickers.classList.toggle("hidden",stickers.length>0);
      for(const sticker of stickers){const item=document.createElement("div");item.className="saved-sticker-item";const btn=document.createElement("button");btn.type="button";btn.className="saved-sticker";const img=document.createElement("img");img.src=sticker.url;img.alt="Figurinha";btn.appendChild(img);btn.addEventListener("click",()=>sendSticker(sticker.token));item.appendChild(btn);
        if(sticker.owner===USERNAME){const del=document.createElement("button");del.type="button";del.className="delete-sticker";del.textContent="×";del.addEventListener("click",async e=>{e.stopPropagation();if(!confirm("Excluir esta figurinha?"))return;const res=await fetch(`/api/stickers/${ROOM}/${sticker.token}`,{method:"DELETE"});if(res.ok)loadStickers();});item.appendChild(del);}stickerGallery.appendChild(item);}
    }catch(_){}
  }
  async function sendSticker(token){
    const activeReply = replyTo;
    try{
      const r=await fetch(`/api/sticker-message/${ROOM}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({token,device_id:NossaSala.deviceId,reply_to_id:activeReply?.id||null})});
      const data=await r.json();
      if(!r.ok)throw new Error(data.error||"Falha ao enviar.");
      clearReply();
      await loadMessages();
      toolDrawer.classList.add("hidden");
    }catch(err){status(err.message,true);}
  }

  loadMessages(); loadStickers(); input.focus(); setInterval(loadMessages,1200); setInterval(()=>NossaSala.ping(),20000);
})();
