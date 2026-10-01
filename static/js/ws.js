/* Reconnecting WebSocket helper shared by board + admin. */
window.QuizWS = (function () {
  let ws = null;
  const handlers = { state: [], serial_cmd: [], open: [], close: [] };

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const channel = document.body.dataset.channel || "board";
    ws = new WebSocket(`${proto}://${location.host}/ws/${channel}`);
    ws.onopen = () => handlers.open.forEach((f) => f());
    ws.onclose = () => {
      handlers.close.forEach((f) => f());
      setTimeout(connect, 1500);
    };
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      (handlers[msg.type] || []).forEach((f) => f(msg));
    };
  }

  connect();
  return {
    on(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
    send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); },
  };
})();
