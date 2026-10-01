document.body.dataset.channel = "board";

const LETTERS = ["A", "B", "C", "D"];

function render(state) {
  const rl = document.getElementById("roundLabel");
  rl.textContent = state.round
    ? `${state.round.type_label} ${state.round.type === "preround" ? state.round.number : ""}`.trim()
    : "Quiz";

  const wrap = document.getElementById("players");
  wrap.innerHTML = "";
  state.players.forEach((p) => {
    const d = document.createElement("div");
    d.className = "player-card" + (p.buzzed ? " buzzed" : "") + (p.blocked ? " blocked" : "");
    d.innerHTML = `<div class="pname"><span class="slot-dot" style="background:${slotColor(p.slot)}"></span>${esc(p.name)}</div>
                   <div class="pscore">${p.score}</div>`;
    wrap.appendChild(d);
  });

  const qbox = document.getElementById("questionBox");
  const idle = document.getElementById("idleBox");
  if (state.question) {
    qbox.classList.remove("hidden");
    idle.classList.add("hidden");
    const q = state.question;
    document.getElementById("qmeta").textContent =
      `Schwierigkeit ${"★".repeat(q.skill)}${q.category ? " · " + q.category : ""}`;
    document.getElementById("qtext").textContent = q.text;
    const aw = document.getElementById("answers");
    aw.innerHTML = "";
    q.answers.forEach((a, i) => {
      const d = document.createElement("div");
      d.className = "answer";
      if (state.phase === "resolved") {
        if (i + 1 === q.correct) d.classList.add("correct");
        else if (i + 1 === q.picked) d.classList.add("wrong");
      }
      d.innerHTML = `<span class="letter">${LETTERS[i]}</span><span>${esc(a)}</span>`;
      aw.appendChild(d);
    });
  } else {
    qbox.classList.add("hidden");
    idle.classList.remove("hidden");
    idle.textContent = state.game_started
      ? state.round ? "Buzzer bereit …" : "Nächste Runde …"
      : "Quiz startet gleich …";
  }
}

function slotColor(slot) {
  return ["#4f7cff", "#2ecc71", "#ffb02e", "#e74c3c", "#b16bff"][slot - 1] || "#888";
}
function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

QuizWS.on("state", (m) => render(m.data));
