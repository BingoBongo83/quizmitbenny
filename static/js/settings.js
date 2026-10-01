const api = async (url, method = "GET", body) => {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    alert(e.detail || "Fehler: " + r.status);
    throw new Error(r.status);
  }
  return r.json().catch(() => ({}));
};

const esc = (s) => {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
};

const SKILL_SETS = [
  ["skills_preround", "Vorrunde"],
  ["skills_playoff", "Playoff"],
  ["skills_semifinal", "Halbfinale"],
  ["skills_final", "Finale"],
];

let players = [];
let settings = {};

// ---------------- players ----------------
async function loadPlayers() {
  players = await api("/api/settings/players");
  const el = document.getElementById("playerList");
  el.innerHTML = players.map((p) => `
    <div class="player-row">
      <label><input type="checkbox" data-pid="${p.id}" ${p.active ? "checked" : ""} class="pactive"> aktiv</label>
      <input type="text" value="${esc(p.name)}" data-pid="${p.id}" class="pname" style="flex:1">
      <button data-pid="${p.id}" class="psave">Speichern</button>
      <button data-pid="${p.id}" class="pdel danger">×</button>
    </div>`).join("");
  el.querySelectorAll(".psave").forEach((b) => (b.onclick = async () => {
    const pid = b.dataset.pid;
    const name = el.querySelector(`.pname[data-pid="${pid}"]`).value;
    const active = el.querySelector(`.pactive[data-pid="${pid}"]`).checked;
    await api(`/api/settings/players/${pid}`, "PUT", { name, active });
    loadPlayers(); loadPreview();
  }));
  el.querySelectorAll(".pdel").forEach((b) => (b.onclick = async () => {
    if (confirm("Spieler löschen?")) {
      await api(`/api/settings/players/${b.dataset.pid}`, "DELETE");
      loadPlayers(); loadPreview();
    }
  }));
  el.querySelectorAll(".pactive").forEach((c) => (c.onchange = async () => {
    const pid = c.dataset.pid;
    const name = el.querySelector(`.pname[data-pid="${pid}"]`).value;
    await api(`/api/settings/players/${pid}`, "PUT", { name, active: c.checked });
    loadPreview();
  }));
  loadPreview();
}

document.getElementById("addPlayer").onclick = async () => {
  const name = document.getElementById("newPlayerName").value.trim();
  if (!name) return;
  await api("/api/settings/players", "POST", { name });
  document.getElementById("newPlayerName").value = "";
  loadPlayers();
};

// ---------------- settings ----------------
async function loadSettings() {
  settings = await api("/api/settings");
  document.getElementById("s_ppr").value = settings.players_per_round;
  document.getElementById("s_pre").value = settings.num_prerounds;
  document.getElementById("s_pc").value = settings.points_correct;
  document.getElementById("s_pws").value = settings.points_wrong_self;
  document.getElementById("s_pwo").value = settings.points_wrong_others;
  document.getElementById("s_block").checked = settings.block_on_wrong;

  const sk = document.getElementById("skillSets");
  sk.innerHTML = SKILL_SETS.map(([key, label]) => `
    <div style="margin:8px 0"><b>${label}:</b> ` +
    [1, 2, 3, 4, 5].map((s) => `
      <label class="skill-cb"><input type="checkbox" data-set="${key}" value="${s}"
        ${settings[key].includes(s) ? "checked" : ""}> ${s}</label>`).join("") +
    `</div>`).join("");
}

document.getElementById("saveSettings").onclick = async () => {
  const body = {
    players_per_round: +document.getElementById("s_ppr").value,
    num_prerounds: +document.getElementById("s_pre").value,
    points_correct: +document.getElementById("s_pc").value,
    points_wrong_self: +document.getElementById("s_pws").value,
    points_wrong_others: +document.getElementById("s_pwo").value,
    block_on_wrong: document.getElementById("s_block").checked,
  };
  SKILL_SETS.forEach(([key]) => {
    body[key] = [...document.querySelectorAll(`input[data-set="${key}"]:checked`)]
      .map((c) => +c.value);
  });
  await api("/api/settings", "PUT", body);
  settings = body;
  loadPreview();
  alert("Gespeichert");
};

// ---------------- bracket preview ----------------
async function loadPreview() {
  const n = players.filter((p) => p.active).length;
  const el = document.getElementById("preview");
  if (!n) { el.textContent = "Keine aktiven Spieler"; return; }
  const p = await api(`/api/settings/preview?num_players=${n}`);
  el.innerHTML = `
    <div>${n} Spieler → ${p.prerounds.length} Vorrunden (${p.prerounds.join(", ")})</div>
    <div>Halbfinals: ${p.semifinals.join(" + ")} Spieler · Finale: ${p.final}</div>
    ${p.playoffs.map((x) => `<div style="color:var(--accent2)">${esc(x)}</div>`).join("")}
    ${p.warnings.map((x) => `<div style="color:var(--red)">${esc(x)}</div>`).join("")}`;
}

// ---------------- game create / reset / round assignment ----------------
document.getElementById("createGameShuffle").onclick = () => createGame(true);
document.getElementById("createGameOrder").onclick = () => createGame(false);
async function createGame(shuffle) {
  const ids = players.filter((p) => p.active).map((p) => p.id);
  if (!confirm(`Spiel mit ${ids.length} Spielern erstellen? Bisherige Runden werden gelöscht.`)) return;
  await api("/api/game/create", "POST", { player_ids: ids, shuffle });
  loadRounds();
}
document.getElementById("resetGame").onclick = async () => {
  if (confirm("Spiel komplett zurücksetzen (Runden + Punkte löschen)?"))
    await api("/api/settings/reset-game", "POST");
};

async function loadRounds() {
  const st = await api("/api/game/state");
  const el = document.getElementById("roundAssign");
  if (!st.rounds.length) { el.textContent = "Noch kein Spiel erstellt."; return; }
  el.innerHTML = st.rounds.map((r) => `
    <div style="margin:6px 0"><b>${r.type_label} #${r.number}</b> (${r.status}):
      ${r.players.map((p) => `<span class="badge">${esc(p.name)}</span>`).join(" ")}
    </div>`).join("");
}

// ---------------- questions ----------------
async function loadQuestions() {
  const q = document.getElementById("qSearch").value;
  const skill = document.getElementById("qSkillFilter").value;
  const qs = await api(`/api/questions?q=${encodeURIComponent(q)}&skill=${skill}`);
  document.getElementById("questionTable").innerHTML =
    `<tr><th>Frage</th><th>Skill</th><th>Kat.</th><th>✓</th><th></th></tr>` +
    qs.map((x) => `<tr>
      <td>${esc(x.text)}</td><td>${x.skill}</td><td>${esc(x.category)}</td>
      <td>${"ABCD"[x.correct - 1]}</td>
      <td><button class="qdel danger" data-qid="${x.id}">×</button></td></tr>`).join("");
  document.querySelectorAll(".qdel").forEach((b) => (b.onclick = async () => {
    if (confirm("Frage löschen?")) { await api(`/api/questions/${b.dataset.qid}`, "DELETE"); loadQuestions(); }
  }));
}

document.getElementById("qSearchBtn").onclick = loadQuestions;
document.getElementById("nq_add").onclick = async () => {
  const body = {
    text: nq_text.value, answer1: nq_a1.value, answer2: nq_a2.value,
    answer3: nq_a3.value, answer4: nq_a4.value, correct: 1,
    skill: +nq_skill.value,
  };
  if (!body.text || !body.answer1) return alert("Frage + Antworten ausfüllen");
  await api("/api/questions", "POST", body);
  ["nq_text", "nq_a1", "nq_a2", "nq_a3", "nq_a4"].forEach((id) => (document.getElementById(id).value = ""));
  loadQuestions();
};

document.getElementById("qImport").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  const r = await fetch("/api/questions/import", { method: "POST", body: fd });
  const j = await r.json();
  alert(r.ok ? `${j.imported} Fragen importiert` : j.detail || "Fehler");
  loadQuestions();
};

document.getElementById("qSeedBtn").onclick = async () => {
  const amount = +prompt("Wie viele Fragen von OpenTriviaDB laden? (werden via DeepL übersetzt)", "20");
  if (!amount) return;
  const j = await api("/api/questions/seed", "POST", { amount, translate: true });
  alert(`${j.imported} Fragen importiert`);
  loadQuestions();
};

document.getElementById("resetUsed").onclick = async () => {
  if (confirm("Alle 'genutzt'-Markierungen zurücksetzen?")) {
    await api("/api/settings/reset-questions", "POST");
    loadQuestions();
  }
};

// ---------------- init ----------------
(async () => {
  await loadSettings();
  await loadPlayers();
  await loadRounds();
  await loadQuestions();
  document.getElementById("s_ppr").onchange = loadPreview;
  document.getElementById("s_pre").onchange = loadPreview;
})();
