(() => {
  const $ = id => document.getElementById(id);
  const onlineList = $("onlineList");
  const conversationsList = $("conversationsList");
  const invitesBox = $("invitesBox");
  const statusBar = $("statusBar");
  const notifyBtn = $("notifyBtn");
  const installBtn = $("installBtn");
  let loading = false;

  function status(message, error=false, timeout=4200) {
    statusBar.textContent = message;
    statusBar.classList.remove("hidden", "error");
    if (error) statusBar.classList.add("error");
    if (timeout) setTimeout(() => statusBar.classList.add("hidden"), timeout);
  }

  function refreshNotifyLabel() {
    notifyBtn.textContent = NossaSala.notificationLabel();
  }

  notifyBtn.addEventListener("click", async () => {
    try {
      await NossaSala.enablePush();
      refreshNotifyLabel();
      status("Notificações ativadas neste aparelho.");
    } catch (err) { status(err.message, true, 8000); }
  });
  NossaSala.setupInstallButton(installBtn, message => status(message, false, 8000));
  refreshNotifyLabel();

  async function callUser(id, button) {
    button.disabled = true;
    button.textContent = "Chamando...";
    try {
      const r = await fetch(`/api/call/${id}`, {method: "POST"});
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Não foi possível chamar.");
      status(data.status === "already_pending" ? "O chamado já está aguardando resposta." : "Chamado enviado. 🔔");
    } catch (err) { status(err.message, true); }
    finally { button.disabled = false; button.textContent = "Chamar"; }
  }

  function renderOnline(users) {
    onlineList.innerHTML = "";
    if (!users.length) {
      onlineList.innerHTML = '<div class="empty-state">Ninguém está online agora.</div>';
      return;
    }
    for (const user of users) {
      const row = document.createElement("div");
      row.className = "person-row";
      row.innerHTML = `<div class="avatar">${user.full_name.slice(0,1).toUpperCase()}</div><div class="person-main"><strong>${escapeHtml(user.full_name)}</strong><span>@${escapeHtml(user.username)}</span></div><span class="online-dot" title="Online"></span>`;
      const btn = document.createElement("button");
      btn.className = "primary small-btn";
      btn.type = "button";
      btn.textContent = "Chamar";
      btn.addEventListener("click", () => callUser(user.id, btn));
      row.appendChild(btn);
      onlineList.appendChild(row);
    }
  }

  function renderConversations(conversations) {
    conversationsList.innerHTML = "";
    if (!conversations.length) {
      conversationsList.innerHTML = '<div class="empty-state">Suas conversas aparecerão aqui.</div>';
      return;
    }
    for (const conv of conversations) {
      const a = document.createElement("a");
      a.className = "conversation-row";
      a.href = `/conversation/${conv.code}`;
      a.innerHTML = `<div class="avatar">${conv.partner_name.slice(0,1).toUpperCase()}</div><div class="person-main"><strong>${escapeHtml(conv.partner_name)}</strong><span>${escapeHtml(conv.last_message)}</span></div><span class="presence ${conv.online ? 'online' : ''}">${conv.online ? 'online' : 'offline'}</span>`;
      conversationsList.appendChild(a);
    }
  }

  function renderInvites(invites) {
    invitesBox.innerHTML = "";
    invitesBox.classList.toggle("hidden", invites.length === 0);
    for (const invite of invites) {
      const item = document.createElement("div");
      item.className = "invite-card";
      item.innerHTML = `<div><strong>📞 ${escapeHtml(invite.sender_name)} está chamando</strong><div class="muted">Aceite para abrir a conversa privada.</div></div>`;
      const actions = document.createElement("div");
      actions.className = "invite-actions";
      const accept = document.createElement("button");
      accept.className = "primary"; accept.textContent = "Aceitar"; accept.type = "button";
      const reject = document.createElement("button");
      reject.className = "secondary"; reject.textContent = "Recusar"; reject.type = "button";
      accept.addEventListener("click", async () => {
        const r = await fetch(`/api/invites/${invite.id}/accept`, {method:"POST"});
        const data = await r.json();
        if (r.ok) location.href = data.url; else status(data.error || "Convite inválido.", true);
      });
      reject.addEventListener("click", async () => {
        await fetch(`/api/invites/${invite.id}/reject`, {method:"POST"});
        load();
      });
      actions.append(accept, reject); item.appendChild(actions); invitesBox.appendChild(item);
    }
  }

  function escapeHtml(value) {
    const div = document.createElement("div"); div.textContent = value ?? ""; return div.innerHTML;
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const r = await fetch("/api/dashboard", {cache: "no-store"});
      if (r.status === 401) { location.href = "/login"; return; }
      const data = await r.json();
      renderOnline(data.online || []);
      renderConversations(data.conversations || []);
      renderInvites(data.invites || []);
    } catch (_) {} finally { loading = false; }
  }

  load();
  setInterval(load, 4000);
  setInterval(() => NossaSala.ping(), 20000);
})();
