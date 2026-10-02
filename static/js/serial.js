/* Web Serial integration for the admin panel (Chrome/Edge only).
   Reads buzzer numbers from the Arduino and forwards them via the admin WS;
   writes serial_cmd commands from the server back to the Arduino. */
window.QuizSerial = (function () {
  let port = null;
  let writer = null;
  let reading = false;

  function supported() {
    return "serial" in navigator;
  }

  async function connect() {
    if (!supported()) {
      await Dlg.alert("Web Serial wird von diesem Browser nicht unterstützt (Chrome/Edge nötig). Alternativ: scripts/buzzer_bridge.py");
      return false;
    }
    try {
      port = await navigator.serial.requestPort();
      await port.open({ baudRate: 9600 });
      writer = port.writable.getWriter();
      QuizWS.send({ type: "serial_ready" });
      reading = true;
      readLoop();
      return true;
    } catch (e) {
      console.error("serial open failed", e);
      return false;
    }
  }

  async function readLoop() {
    const decoder = new TextDecoderStream();
    const streamClosed = port.readable.pipeTo(decoder.writable).catch(() => {});
    const reader = decoder.readable.getReader();
    let buf = "";
    try {
      while (reading) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += value;
        let idx;
        while ((idx = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, idx).trim();
          buf = buf.slice(idx + 1);
          if (line) handleLine(line);
        }
      }
    } catch (e) {
      console.error("serial read error", e);
    } finally {
      reader.releaseLock();
      streamClosed.catch(() => {});
    }
  }

  function handleLine(line) {
    // Arduino sends the pressed buzzer index 0-4 as a line -> slot = +1
    const n = parseInt(line, 10);
    if (!isNaN(n)) QuizWS.send({ type: "buzzer", buzzer: n + 1 });
  }

  async function write(cmd) {
    if (!writer) return;
    try {
      await writer.write(new TextEncoder().encode(cmd + "\n"));
    } catch (e) {
      console.error("serial write error", e);
    }
  }

  async function disconnect() {
    reading = false;
    QuizWS.send({ type: "serial_off" });
    try {
      if (writer) { writer.releaseLock(); writer = null; }
      if (port) await port.close();
    } catch (e) {}
    port = null;
  }

  QuizWS.on("serial_cmd", (m) => (m.cmds || []).forEach(write));

  return { supported, connect, disconnect, isConnected: () => !!port };
})();
