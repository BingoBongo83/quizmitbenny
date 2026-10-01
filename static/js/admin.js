const LETTERS = ["A", "B", "C", "D"];
let lastState = null;

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    alert(e.detail || "Fehler: " + r.status);
  }
  return r;
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

function render(st) {
  lastState = st;
  document.getElementById("phaseBadge").textContent = st.phase;

  // current round + players
  const cur = document.getElementById("curRound");
  if (st.round) {
    cur.innerHTML = `<span class="badge active">${st.round.type_label}</span> Runde #${st.round.number}` +
      (st.round.pool_label ? ` <span class="muted">· ${esc(st.round.pool_label)}</span>` : "");
  } else {
    cur.textContent = st.game_started ? "Keine aktive Runde" : "Spiel noch nicht gestartet";
  }
  const tbl = document.getElementById("curPlayers");
  tbl.innerHTML = "<tr><th>Slot</th><th>Spieler</th><th>Punkte</th><th></th></tr>" +
    st.players.map((p) =>
      `<tr class="buzzsim ${p.buzzed ? "buzzed" : ""} ${p.blocked ? "blocked" : ""}"
           data-slot="${p.slot}" title="Klicken = Buzzer simulieren">
        <td>${p.slot}</td><td>${esc(p.name)}</td><td>${p.score}</td>
        <td>${p.buzzed ? "GEBUZZERT" : p.blocked ? "gesperrt" : ""}</td></tr>`
    ).join("");
  tbl.querySelectorAll("tr.buzzsim").forEach((row) =>
    (row.onclick = () => QuizWS.send({ type: "buzzer", buzzer: +row.dataset.slot })));

  // current question
  const cq = document.getElementById("curQ");
  const cqt = document.getElementById("curQText");
  const btns = document.getElementById("answerBtns");
  btns.innerHTML = "";
  if (st.question) {
    cq.textContent = `Frage (Skill ${st.question.skill}${st.question.category ? ", " + st.question.category : ""})`;
    cqt.textContent = st.question.text;
    st.question.answers.forEach((a, i) => {
      const b = document.createElement("button");
      let cls = "";
      if (st.phase === "resolved") {
        if (i + 1 === st.question.correct) cls = "reveal-correct";
        else if (i + 1 === st.question.picked) cls = "reveal-wrong";
      }
      b.className = cls;
      b.innerHTML = `<span class="letter">${LETTERS[i]}</span>${esc(a)}` +
        (i + 1 === st.question.correct ? " ✓" : "");
      b.disabled = st.phase !== "buzzed";
      b.onclick = () => post("/api/game/answer", { answer: i + 1 });
      btns.appendChild(b);
    });
    if (st.phase === "buzzed") {
      const p = st.players.find((x) => x.buzzed);
      cqt.innerHTML = `<b>${esc(p ? p.name : "?")} hat gebuzzert!</b><br>` + esc(st.question.text);
    }
  } else {
    cq.textContent = "Keine Frage aktiv";
    cqt.textContent = "";
  }

  // next question preview
  const nq = document.getElementById("nextQ");
  if (st.next_question) {
    nq.innerHTML = `<b>Nächste Frage (Vorschau):</b> ${esc(st.next_question.text)}<br>` +
      `<span class="muted">Richtig: ${LETTERS[st.next_question.correct - 1]} – ` +
      `${esc(st.next_question.answers[st.next_question.correct - 1])} (Skill ${st.next_question.skill})</span>`;
  } else {
    nq.textContent = "";
  }

  // rounds list
  const rl = document.getElementById("roundsList");
  rl.innerHTML = st.rounds.map((r) => {
    const statusBadge = r.status === "active" ? "active" : r.status === "finished" ? "finished" : "";
    const players = r.players.map((p) =>
      `${esc(p.name)} (${p.score}${p.place ? ", Platz " + p.place : ""})`).join(", ");
    const startBtn = r.status === "pending" && !st.round
      ? ` <button data-round="${r.id}" class="startRound">Starten</button>` : "";
    return `<div style="margin:8px 0">
      <span class="badge ${statusBadge}">${r.type_label}</span>
      <b>#${r.number}</b> – ${players || "–"}${startBtn}</div>`;
  }).join("") || '<span class="muted">Keine Runden – erst unter Einstellungen ein Spiel erstellen.</span>';
  rl.querySelectorAll(".startRound").forEach((b) =>
    (b.onclick = () => post(`/api/game/round/${b.dataset.round}/start`)));

  // previous questions
  document.getElementById("prevQs").innerHTML = st.previous_questions.length
    ? st.previous_questions.map((q) =>
        `<div style="margin:6px 0"><b>${esc(q.text)}</b><br>
         <span class="muted">✓ ${LETTERS[q.correct - 1]}: ${esc(q.answers[q.correct - 1])}</span></div>`
      ).join("")
    : "–";

  // buttons enabled state
  document.getElementById("btnShow").disabled = !(st.round && (st.phase === "idle" || st.phase === "resolved"));
  document.getElementById("btnSkip").disabled = !(st.question);
  document.getElementById("btnHide").disabled = !(st.question);
  document.getElementById("btnFinish").disabled = !(st.round && st.round.status === "active");
}

document.getElementById("btnShow").onclick = () => post("/api/game/question/show");
document.getElementById("btnSkip").onclick = () => post("/api/game/question/skip");
document.getElementById("btnHide").onclick = () => post("/api/game/question/hide");
document.getElementById("btnFinish").onclick = () => {
  if (lastState && lastState.round && confirm("Runde wirklich beenden?"))
    post(`/api/game/round/${lastState.round.id}/finish`);
};

// serial connect button
const serialBtn = document.getElementById("serialBtn");
serialBtn.onclick = async () => {
  if (QuizSerial.isConnected()) {
    await QuizSerial.disconnect();
  } else {
    await QuizSerial.connect();
  }
  updateSerialUI();
};
function updateSerialUI() {
  const dot = document.getElementById("serialDot");
  dot.className = "dot " + (QuizSerial.isConnected() ? "ok" : "off");
  serialBtn.textContent = QuizSerial.isConnected()
    ? "Buzzer trennen" : "Buzzer verbinden (Web Serial)";
}
if (!QuizSerial.supported()) {
  serialBtn.textContent = "Web Serial nicht verfügbar (Bridge nutzen)";
}
updateSerialUI();

QuizWS.on("state", (m) => render(m.data));
QuizWS.on("open", () => (document.getElementById("wsDot").className = "dot ok"));
QuizWS.on("close", () => {
  document.getElementById("wsDot").className = "dot off";
  document.getElementById("serialDot").className = "dot off";
});
