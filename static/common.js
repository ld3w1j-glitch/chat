window.NossaSala = (() => {
  let deviceId = localStorage.getItem("nossa_sala_device_id") || "";
  if (!deviceId) {
    deviceId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    localStorage.setItem("nossa_sala_device_id", deviceId);
  }

  let deferredInstallPrompt = null;

  async function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) throw new Error("Este navegador não suporta Service Worker.");
    await navigator.serviceWorker.register("/sw.js");
    return navigator.serviceWorker.ready;
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(base64);
    return Uint8Array.from([...raw].map(ch => ch.charCodeAt(0)));
  }

  async function enablePush() {
    if (!("Notification" in window) || !("PushManager" in window)) {
      throw new Error("Este navegador não suporta notificações Web Push.");
    }
    const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const standalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;
    if (isIOS && !standalone) {
      throw new Error("No iPhone/iPad, adicione o site à Tela de Início e abra pelo ícone antes de ativar notificações.");
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("A permissão de notificação não foi concedida.");

    const registration = await registerServiceWorker();
    const keyResponse = await fetch("/api/push/public-key", {cache: "no-store"});
    const keyData = await keyResponse.json();
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(keyData.publicKey)
      });
    }
    const response = await fetch("/api/push/subscribe", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({device_id: deviceId, subscription: subscription.toJSON()})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Não consegui ativar as notificações.");
    return true;
  }

  function notificationLabel() {
    if (!("Notification" in window) || !("PushManager" in window)) return "🔕 Não suportado";
    if (Notification.permission === "granted") return "🔔 Ativas";
    if (Notification.permission === "denied") return "🔕 Bloqueadas";
    return "🔔 Notificações";
  }

  function setupInstallButton(button, onMessage) {
    if (!button) return;
    window.addEventListener("beforeinstallprompt", event => {
      event.preventDefault();
      deferredInstallPrompt = event;
      button.classList.remove("hidden");
    });
    window.addEventListener("appinstalled", () => {
      deferredInstallPrompt = null;
      button.classList.add("hidden");
      onMessage?.("Nossa Sala foi instalado neste aparelho.");
    });

    const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
    const standalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone;
    if (isIOS && !standalone) button.classList.remove("hidden");

    button.addEventListener("click", async () => {
      if (deferredInstallPrompt) {
        deferredInstallPrompt.prompt();
        await deferredInstallPrompt.userChoice;
        deferredInstallPrompt = null;
        button.classList.add("hidden");
        return;
      }
      if (isIOS) {
        onMessage?.("No iPhone/iPad: Safari → Compartilhar → Adicionar à Tela de Início. Depois abra pelo ícone.");
      } else {
        onMessage?.("Use a opção “Instalar aplicativo” do menu do navegador, se disponível.");
      }
    });
  }

  async function ping() {
    try { await fetch("/api/presence/ping", {method: "POST"}); } catch (_) {}
  }

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

  return {deviceId, enablePush, notificationLabel, setupInstallButton, ping};
})();
