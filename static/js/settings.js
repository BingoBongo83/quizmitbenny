const api = async (url, method = "GET", body) => {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    await Dlg.alert(e.detail || "Fehler: " + r.status);
    throw new Error(r.status);
  }
  return r.json().catch(() => ({}));
};

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// auto-growing textareas (question fields): expand up to max-height, then scroll
const taGrow = (el) => {
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";
};
document.querySelectorAll("textarea.autota").forEach((ta) =>
  ta.addEventListener("input", () => taGrow(ta)));

// debounce for live-search inputs
const debounce = (fn, ms = 250) => {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
};

const SKILL_SETS = [
  ["skills_preround", "Vorrunde"],
  ["skills_playoff", "Playoff"],
  ["skills_semifinal", "Halbfinale"],
  ["skills_final", "Finale"],
];

const MAXQ_STAGES = ["preround", "playoff", "semifinal", "final"];
const maxqVal = (s) => settings["max_questions_" + s] ?? settings.max_questions ?? 20;

const BUZZER_COLORS = [
  ["#2e6bff", "Blau"], ["#ffd23c", "Gelb"], ["#ff8c1a", "Orange"],
  ["#ff5ec4", "Pink"], ["#2ee56f", "Grün"], ["#e8ecf8", "Weiß"],
  ["#ff4d5e", "Rot"],
];
const SEAT_COUNT = 5;

// ---------------- modals ----------------
const MODAL_REFRESH = {
  "m-players": () => loadPlayers(),
  "m-rounds": () => loadRounds(),
  "m-questions": () => loadQuestions(),
  "m-cats": () => loadCategories(),
  "m-reported": () => loadReported(),
  "m-export": () => loadExportCats(),
  "m-stats": () => loadStats(),
};
function openModal(id) {
  document.getElementById(id).classList.remove("hidden");
  const fn = MODAL_REFRESH[id];
  if (fn) fn().catch(() => {});
}
function closeModal(el) {
  el.closest(".modal-overlay").classList.add("hidden");
}
document.querySelectorAll(".fn-card").forEach((c) =>
  (c.onclick = () => openModal(c.dataset.modal)));
document.querySelectorAll(".modal-overlay").forEach((ov) =>
  ov.addEventListener("click", (e) => {
    if (e.target === ov) ov.classList.add("hidden");
  }));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    // close only the topmost open overlay (DOM order = stacking order)
    const open = [...document.querySelectorAll(".modal-overlay:not(.hidden)")];
    if (open.length) open[open.length - 1].classList.add("hidden");
  }
});
document.querySelectorAll(".modalClose").forEach((b) => (b.onclick = () => closeModal(b)));
document.querySelectorAll(".modalCancel").forEach((b) => (b.onclick = () => {
  closeModal(b);
  loadSettings().catch(() => {});  // discard unsaved form edits
}));

// ---------------- shared question editor modal ----------------
// ctx: {kind:'q'|'c'|'r', id, catId, source, category}
let qeCtx = null;

function openQuestionEditor(x, ctx) {
  qeCtx = ctx;
  document.getElementById("qe_text").value = x.text;
  x.answers.forEach((a, i) => {
    document.getElementById(`qe_a${i + 1}`).value = a;
  });
  const r = document.querySelector(
    `input[name="qe_correct"][value="${x.correct}"]`);
  if (r) r.checked = true;
  document.getElementById("qe_skill").value = x.skill;
  document.getElementById("m-qedit").classList.remove("hidden");
  const ta = document.getElementById("qe_text");
  taGrow(ta);  // needs a visible element for correct scrollHeight
  ta.focus();
}

function closeQuestionEditor() {
  qeCtx = null;
  document.getElementById("m-qedit").classList.add("hidden");
}
document.getElementById("qe_cancel").onclick = closeQuestionEditor;

document.getElementById("qe_save").onclick = async () => {
  if (!qeCtx) return;
  const checked = document.querySelector('input[name="qe_correct"]:checked');
  const body = {
    text: document.getElementById("qe_text").value.trim(),
    answer1: document.getElementById("qe_a1").value,
    answer2: document.getElementById("qe_a2").value,
    answer3: document.getElementById("qe_a3").value,
    answer4: document.getElementById("qe_a4").value,
    correct: checked ? +checked.value : 1,
    skill: +document.getElementById("qe_skill").value,
  };
  if (!body.text || !body.answer1 || !body.answer2 || !body.answer3 || !body.answer4)
    return Dlg.alert("Frage + alle Antworten ausfüllen");
  const { kind, id, catId, source, category } = qeCtx;
  const custom = kind === "c" || (kind === "r" && source === "c");
  if (custom) {
    await api(`/api/categories/${catId}/questions/${id}`, "PUT", body);
  } else {
    body.category = category || "";
    await api(`/api/questions/${id}`, "PUT", body);
  }
  closeQuestionEditor();
  if (kind === "r") loadReported();
  else if (custom) { loadCustomQuestions(); loadCategories(); }
  else loadQuestions();
};

let players = [];
let settings = {};

// ---------------- players ----------------
let pickedIds = new Set();  // players selected for the next game

async function loadPlayers() {
  players = await api("/api/settings/players");
  const actives = players.filter((p) => p.active);
  const inactives = players.filter((p) => !p.active);
  // selection only holds active players; default = all active
  pickedIds = new Set([...pickedIds].filter((id) => actives.some((p) => p.id === id)));
  if (!pickedIds.size && actives.length)
    pickedIds = new Set(actives.map((p) => p.id));
  const el = document.getElementById("playerList");
  document.getElementById("sub-players").textContent =
    `${actives.length} aktiv` +
    (inactives.length ? ` · ${inactives.length} inaktiv` : "");
  const row = (p) => `
    <div class="player-row ${!p.active ? "inactive" : ""}">
      <label><input type="checkbox" data-pid="${p.id}" ${p.active ? "checked" : ""} class="pactive"> aktiv</label>
      <input type="text" value="${esc(p.name)}" data-pid="${p.id}" class="pname" style="flex:1">
      <button data-pid="${p.id}" class="psave">Speichern</button>
      <button data-pid="${p.id}" class="pdel danger">×</button>
    </div>`;
  el.innerHTML = actives.map(row).join("") +
    (inactives.length
      ? `<div class="player-row"><button id="toggleInact" style="padding:4px 14px;font-size:.8em">
           ▸ Inaktive Spieler (${inactives.length})</button></div>
         <div id="inactList" class="hidden">${inactives.map(row).join("")}</div>`
      : "");
  const tg = document.getElementById("toggleInact");
  if (tg) tg.onclick = () => {
    const l = document.getElementById("inactList");
    const show = l.classList.toggle("hidden");
    tg.textContent = `${show ? "▸" : "▾"} Inaktive Spieler (${inactives.length})`;
  };
  el.querySelectorAll(".psave").forEach((b) => (b.onclick = async () => {
    const pid = b.dataset.pid;
    const name = el.querySelector(`.pname[data-pid="${pid}"]`).value;
    const active = el.querySelector(`.pactive[data-pid="${pid}"]`).checked;
    await api(`/api/settings/players/${pid}`, "PUT", { name, active });
    loadPlayers(); loadPreview();
  }));
  el.querySelectorAll(".pdel").forEach((b) => (b.onclick = async () => {
    if (await Dlg.confirm("Spieler löschen?")) {
      await api(`/api/settings/players/${b.dataset.pid}`, "DELETE");
      loadPlayers(); loadPreview();
    }
  }));
  el.querySelectorAll(".pactive").forEach((c) => (c.onchange = async () => {
    const pid = c.dataset.pid;
    const name = el.querySelector(`.pname[data-pid="${pid}"]`).value;
    await api(`/api/settings/players/${pid}`, "PUT", { name, active: c.checked });
    loadPlayers();
  }));
  renderGamePick();
  loadPreview();
}

// ---------------- game setup: player pick + plan choice ----------------
const stageTxt = (p) =>
  p.stages.map((s) => `${s.label} ${s.sizes.join("/")}`).join(" · ");

function renderGamePick() {
  const el = document.getElementById("gamePick");
  const actives = players.filter((p) => p.active);
  el.innerHTML = actives.length
    ? actives.map((p) => `
      <label class="pick-chip ${pickedIds.has(p.id) ? "on" : ""}">
        <input type="checkbox" class="pplay" data-pid="${p.id}"
          ${pickedIds.has(p.id) ? "checked" : ""}><span>${esc(p.name)}</span>
      </label>`).join("")
    : '<span class="muted">Keine aktiven Spieler</span>';
  el.querySelectorAll(".pplay").forEach((c) => (c.onchange = () => {
    const id = +c.dataset.pid;
    if (c.checked) pickedIds.add(id); else pickedIds.delete(id);
    c.closest(".pick-chip").classList.toggle("on", c.checked);
    loadPlans();
  }));
  loadPlans();
}

async function loadPlans() {
  const el = document.getElementById("planList");
  const n = pickedIds.size;
  if (n < 2) {
    el.innerHTML = '<div class="muted" style="margin:6px 0">Mind. 2 Mitspieler auswählen.</div>';
    return;
  }
  const { plans } = await api(`/api/settings/plans?num_players=${n}`);
  if (!plans || !plans.length) {
    const max = (settings.players_per_round || 0) * (settings.num_prerounds || 0);
    el.innerHTML = `<div style="color:var(--red);margin:6px 0">
      Kein Spielplan für ${n} Spieler möglich (max. ${max} –
      weniger auswählen oder „Spieler pro Runde“/„Max. Vorrunden“ erhöhen).</div>`;
    return;
  }
  el.innerHTML = plans.map((p) => `
    <label class="plan-opt">
      <input type="radio" name="gplan" value="${esc(p.code)}" ${p.recommended ? "checked" : ""}>
      <span>
        <b>${esc(p.label)}</b> ${p.recommended ? '<span class="badge">empfohlen</span>' : ""}<br>
        <span class="muted">${stageTxt(p)}</span>
        ${p.playoffs.map((x) => `<br><span style="color:var(--accent2)">${esc(x)}</span>`).join("")}
        ${p.warnings.map((x) => `<br><span style="color:var(--red)">${esc(x)}</span>`).join("")}
      </span>
    </label>`).join("");
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
  document.getElementById("s_lockin").checked = settings.answer_lockin !== false;
  document.getElementById("s_maxq_on").checked = !!settings.max_questions_enabled;
  MAXQ_STAGES.forEach((s) => {
    const el = document.getElementById("s_maxq_" + s);
    el.value = maxqVal(s);
    el.disabled = !settings.max_questions_enabled;
  });
  document.getElementById("s_sound").value = settings.sound_target || "board";
  const reveal = settings.answer_reveal || "all";
  document.getElementById("s_reveal").value = reveal;
  document.getElementById("s_reveal_delay").value = settings.reveal_delay ?? 0;
  const rp = document.getElementById("s_reveal_pause");
  rp.value = settings.reveal_pause ?? 2;
  rp.disabled = reveal !== "seq";
  document.getElementById("s_jfifty").checked = settings.joker_fifty !== false;
  document.getElementById("s_jdouble").checked = settings.joker_double !== false;
  document.getElementById("s_jaudience").checked = settings.joker_audience !== false;

  const mqVals = MAXQ_STAGES.map(maxqVal);
  const mqTxt = new Set(mqVals).size === 1 ? `max ${mqVals[0]} Fr.` :
    `max ${mqVals.join("/")} Fr.`;
  document.getElementById("sub-game").textContent =
    `${settings.players_per_round}/Runde · max. ${settings.num_prerounds} Vorrunden · ` +
    `+${settings.points_correct}/${settings.points_wrong_self}/+${settings.points_wrong_others}` +
    (settings.max_questions_enabled ? ` · ${mqTxt}` : "");
  document.getElementById("sub-skills").textContent =
    SKILL_SETS.map(([k, l]) => `${l}: ${(settings[k] || []).join("-")}`).join(" · ");

  const sc = document.getElementById("seatColors");
  const seatColors = settings.seat_colors || [];
  sc.innerHTML = Array.from({ length: SEAT_COUNT }, (_, i) => `
    <div class="player-row">
      <span class="seat-dot" style="background:${seatColors[i] || "#888"}"></span>
      <b>Platz ${i + 1}</b>
      <select class="seatSel" data-seat="${i}">` +
      BUZZER_COLORS.map(([hex, name]) =>
        `<option value="${hex}"${seatColors[i] === hex ? " selected" : ""}>${name}</option>`).join("") +
      `</select>
    </div>`).join("");
  sc.querySelectorAll(".seatSel").forEach((sel) => (sel.onchange = () => {
    sel.closest(".player-row").querySelector(".seat-dot").style.background = sel.value;
  }));
  document.getElementById("sub-seats").innerHTML = seatColors.map((hex, i) =>
    `<span class="seat-dot" style="background:${hex || "#888"}" title="Platz ${i + 1}"></span>`).join("");

  const sk = document.getElementById("skillSets");
  sk.innerHTML = SKILL_SETS.map(([key, label]) => `
    <div style="margin:8px 0"><b>${label}:</b> ` +
    [1, 2, 3, 4, 5].map((s) => `
      <label class="skill-cb"><input type="checkbox" data-set="${key}" value="${s}"
        ${settings[key].includes(s) ? "checked" : ""}> ${s}</label>`).join("") +
    `</div>`).join("");
}

async function saveAllSettings(btn) {
  const body = {
    players_per_round: +document.getElementById("s_ppr").value,
    num_prerounds: +document.getElementById("s_pre").value,
    points_correct: +document.getElementById("s_pc").value,
    points_wrong_self: +document.getElementById("s_pws").value,
    points_wrong_others: +document.getElementById("s_pwo").value,
    block_on_wrong: document.getElementById("s_block").checked,
    answer_lockin: document.getElementById("s_lockin").checked,
    max_questions_enabled: document.getElementById("s_maxq_on").checked,
    sound_target: document.getElementById("s_sound").value,
    answer_reveal: document.getElementById("s_reveal").value,
    reveal_delay: +document.getElementById("s_reveal_delay").value || 0,
    reveal_pause: +document.getElementById("s_reveal_pause").value || 2,
    ...Object.fromEntries(MAXQ_STAGES.map((s) => [
      "max_questions_" + s,
      +document.getElementById("s_maxq_" + s).value || 20,
    ])),
    joker_fifty: document.getElementById("s_jfifty").checked,
    joker_double: document.getElementById("s_jdouble").checked,
    joker_audience: document.getElementById("s_jaudience").checked,
    seat_colors: [...document.querySelectorAll(".seatSel")].map((s) => s.value),
  };
  SKILL_SETS.forEach(([key]) => {
    body[key] = [...document.querySelectorAll(`input[data-set="${key}"]:checked`)]
      .map((c) => +c.value);
  });
  await api("/api/settings", "PUT", body);
  settings = { ...settings, ...body };
  closeModal(btn);
  loadSettings(); loadPreview();
}
document.getElementById("saveSettings").onclick = (e) => saveAllSettings(e.target);
document.getElementById("saveSeats").onclick = (e) => saveAllSettings(e.target);
document.getElementById("saveSkills").onclick = (e) => saveAllSettings(e.target);

// ---------------- bracket preview ----------------
async function loadPreview() {
  const n = pickedIds.size || players.filter((p) => p.active).length;
  const el = document.getElementById("preview");
  if (!n) { el.textContent = "Keine aktiven Spieler"; return; }
  const { plans } = await api(`/api/settings/plans?num_players=${n}`);
  const p = plans && plans[0];
  el.innerHTML = !p
    ? `${n} Spieler → <span style="color:var(--red)">kein gültiger Spielplan</span>`
    : `<div>${n} Spieler → ${esc(p.label)}</div>
       <div class="muted">${stageTxt(p)}</div>` +
      p.playoffs.map((x) => `<div style="color:var(--accent2)">${esc(x)}</div>`).join("") +
      p.warnings.map((x) => `<div style="color:var(--red)">${esc(x)}</div>`).join("");
}

// ---------------- game create / reset / round assignment ----------------
document.getElementById("createGameShuffle").onclick = () => createGame(true);
document.getElementById("createGameOrder").onclick = () => createGame(false);
async function createGame(shuffle) {
  const ids = [...pickedIds];
  const plan = document.querySelector('input[name="gplan"]:checked');
  if (!plan) {
    await Dlg.alert("Bitte zuerst Mitspieler und Spielplan wählen.");
    return;
  }
  if (!(await Dlg.confirm(`Spiel mit ${ids.length} ausgewählten Spielern erstellen? Bisherige Runden werden gelöscht.`))) return;
  await api("/api/game/create", "POST", { player_ids: ids, plan: plan.value, shuffle });
  loadRounds();
}
document.getElementById("resetGame").onclick = async () => {
  if (await Dlg.confirm("Spiel komplett zurücksetzen (Runden + Punkte löschen)?"))
    await api("/api/settings/reset-game", "POST");
};

// ---------------- categories + custom questions ----------------
let categories = [];
let activeCat = null;
const CQ_PAGE_SIZE = 10;
let cqPage = 0;

async function loadCategories() {
  categories = await api("/api/categories");
  document.getElementById("sub-cats").textContent =
    `${categories.length} Kategorien · ${categories.reduce((a, c) => a + c.count, 0)} eigene Fragen`;
  const el = document.getElementById("categoryList");
  el.innerHTML = categories.map((c) => `
    <div class="player-row">
      <input type="text" value="${esc(c.name)}" data-cid="${c.id}" class="cname" style="flex:1">
      <span class="badge">${c.count} Fragen</span>
      <button data-cid="${c.id}" class="csave">Speichern</button>
      <button data-cid="${c.id}" data-name="${esc(c.name)}" class="copen">Fragen</button>
      <button data-cid="${c.id}" class="cdel danger">×</button>
    </div>`).join("") || '<span class="muted">Keine Kategorien – lege eine an, dann Fragen hinzufügen.</span>';
  el.querySelectorAll(".csave").forEach((b) => (b.onclick = async () => {
    const name = el.querySelector(`.cname[data-cid="${b.dataset.cid}"]`).value;
    await api(`/api/categories/${b.dataset.cid}`, "PUT", { name });
    loadCategories();
  }));
  el.querySelectorAll(".copen").forEach((b) => (b.onclick = () => openCategory(+b.dataset.cid, b.dataset.name)));
  el.querySelectorAll(".cdel").forEach((b) => (b.onclick = async () => {
    if (await Dlg.confirm("Kategorie inkl. aller Fragen löschen?")) {
      await api(`/api/categories/${b.dataset.cid}`, "DELETE");
      if (activeCat === +b.dataset.cid) document.getElementById("catDetail").classList.add("hidden");
      loadCategories(); loadRounds();
    }
  }));
}

document.getElementById("addCat").onclick = async () => {
  const name = document.getElementById("newCatName").value.trim();
  if (!name) return;
  await api("/api/categories", "POST", { name });
  document.getElementById("newCatName").value = "";
  loadCategories(); loadRounds();
};

async function openCategory(cid, name) {
  activeCat = cid;
  cqPage = 0;
  document.getElementById("cqSearch").value = "";
  document.getElementById("cqSkillFilter").value = "";
  document.getElementById("catDetail").classList.remove("hidden");
  document.getElementById("catDetailName").textContent = `Fragen in „${name}“`;
  loadCustomQuestions();
}

async function loadCustomQuestions() {
  if (!activeCat) return;
  const all = await api(`/api/categories/${activeCat}/questions`);
  const term = document.getElementById("cqSearch").value.trim().toLowerCase();
  const skill = +document.getElementById("cqSkillFilter").value || 0;
  const qs = all.filter((x) =>
    (!term || x.text.toLowerCase().includes(term)) &&
    (!skill || x.skill === skill));
  window._cqs = qs;
  const pages = Math.max(1, Math.ceil(qs.length / CQ_PAGE_SIZE));
  if (cqPage >= pages) cqPage = pages - 1;
  const page = qs.slice(cqPage * CQ_PAGE_SIZE, (cqPage + 1) * CQ_PAGE_SIZE);
  document.getElementById("cqTable").innerHTML =
    `<tr><th>Frage</th><th>Skill</th><th>✓</th><th></th></tr>` +
    (page.map((x) => `<tr>
      <td>${esc(x.text)}</td><td>${x.skill}</td>
      <td>${"ABCD"[x.correct - 1]}</td>
      <td>
        <button class="cqedit" data-qid="${x.id}">edit</button>
        <button class="cqdel danger" data-qid="${x.id}">×</button>
      </td></tr>`).join("") || '<tr><td class="muted">Keine Fragen</td></tr>');
  document.getElementById("cqPager").innerHTML = qs.length > CQ_PAGE_SIZE
    ? `<button id="cqPrev" ${cqPage === 0 ? "disabled" : ""}>◀</button>
       <span class="muted">Seite ${cqPage + 1} / ${pages} (${qs.length} Fragen)</span>
       <button id="cqNext" ${cqPage >= pages - 1 ? "disabled" : ""}>▶</button>`
    : `<span class="muted">${qs.length} Fragen</span>`;
  const prev = document.getElementById("cqPrev"), next = document.getElementById("cqNext");
  if (prev) prev.onclick = () => { cqPage--; loadCustomQuestions(); };
  if (next) next.onclick = () => { cqPage++; loadCustomQuestions(); };
  document.querySelectorAll(".cqdel").forEach((b) => (b.onclick = async () => {
    if (await Dlg.confirm("Frage löschen?")) {
      await api(`/api/categories/${activeCat}/questions/${b.dataset.qid}`, "DELETE");
      loadCustomQuestions(); loadCategories();
    }
  }));
  document.querySelectorAll(".cqedit").forEach((b) => (b.onclick = () => {
    const q = window._cqs.find((x) => x.id === +b.dataset.qid);
    if (q) openQuestionEditor(q, { kind: "c", id: q.id, catId: activeCat });
  }));
}

document.getElementById("cq_add").onclick = async () => {
  const checked = document.querySelector('input[name="cq_correct"]:checked');
  const body = {
    text: cq_text.value, answer1: cq_a1.value, answer2: cq_a2.value,
    answer3: cq_a3.value, answer4: cq_a4.value,
    correct: checked ? +checked.value : 1, skill: +cq_skill.value,
  };
  if (!body.text || !body[`answer${body.correct}`])
    return Dlg.alert("Frage + richtige Antwort ausfüllen");
  await api(`/api/categories/${activeCat}/questions`, "POST", body);
  ["cq_text", "cq_a1", "cq_a2", "cq_a3", "cq_a4"].forEach((id) => (document.getElementById(id).value = ""));
  document.querySelector('input[name="cq_correct"][value="1"]').checked = true;
  taGrow(document.getElementById("cq_text"));
  loadCustomQuestions(); loadCategories();
};

document.getElementById("cqImport").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f || !activeCat) return;
  const fd = new FormData();
  fd.append("file", f);
  impOverlay(true);
  try {
    const r = await fetch(`/api/categories/${activeCat}/import`, { method: "POST", body: fd });
    const j = await r.json();
    await Dlg.alert(r.ok ? `${j.imported} Fragen importiert` : j.detail || "Fehler");
  } finally {
    impOverlay(false);
    e.target.value = "";
  }
  loadCustomQuestions(); loadCategories();
};

// ---------------- rounds + pools ----------------
async function loadRounds() {
  const st = await api("/api/game/state");
  const poolOpts = (sel) =>
    `<option value="standard"${sel === "standard" ? " selected" : ""}>Standard-Pool</option>` +
    categories.map((c) =>
      `<option value="cat:${c.id}"${sel === `cat:${c.id}` ? " selected" : ""}>${esc(c.name)}</option>`
    ).join("");
  const defSel = document.getElementById("s_default_pool");
  if (defSel) {
    defSel.innerHTML = poolOpts(settings.default_pool || "standard");
    defSel.onchange = async () => {
      settings = await api("/api/settings", "PUT", { default_pool: defSel.value });
    };
  }
  const defName = settings.default_pool && settings.default_pool !== "standard"
    ? (categories.find((c) => `cat:${c.id}` === settings.default_pool) || {}).name || "Kategorie"
    : "Standard-Pool";
  document.getElementById("sub-rounds").textContent =
    (st.rounds.length ? `${st.rounds.length} Runden` : "kein Spiel") + ` · Standard: ${defName}`;
  const el = document.getElementById("roundAssign");
  if (!st.rounds.length) { el.textContent = "Noch kein Spiel erstellt."; return; }
  el.innerHTML = st.rounds.map((r) => `
    <div style="margin:8px 0"><b>${r.type_label} #${r.number}</b>
      <span class="badge ${r.status === "active" ? "active" : r.status === "finished" ? "finished" : ""}">${r.status}</span>
      ${r.status !== "finished" ? `<select class="poolSel" data-rid="${r.id}">${poolOpts(r.question_pool)}</select>` : ""}
      <br>${r.players.map((p) => {
        const hex = (st.seat_colors || [])[p.slot - 1];
        return `<span class="badge"${hex ? ` style="background:${hex};border-color:transparent;color:#10131f"` : ""}>${esc(p.name)}</span>`;
      }).join(" ")}
    </div>`).join("");
  el.querySelectorAll(".poolSel").forEach((s) => (s.onchange = async () => {
    await api(`/api/game/round/${s.dataset.rid}/pool`, "PUT", { pool: s.value });
  }));
}

// ---------------- questions ----------------
const Q_PAGE_SIZE = 10;
let qPage = 0;
let qTotal = 0;

async function loadQuestions() {
  const params = new URLSearchParams();
  const q = document.getElementById("qSearch").value;
  const skill = document.getElementById("qSkillFilter").value;
  if (q) params.set("q", q);
  if (skill) params.set("skill", skill);
  params.set("limit", Q_PAGE_SIZE);
  params.set("offset", qPage * Q_PAGE_SIZE);
  const res = await api(`/api/questions?${params}`);
  let qs = res.items;
  qTotal = res.total;
  document.getElementById("sub-questions").textContent = `${qTotal} Fragen`;
  if (!qs.length && qPage > 0) { qPage--; return loadQuestions(); }
  window._qs = qs;
  document.getElementById("questionTable").innerHTML =
    `<tr><th>Frage</th><th>Skill</th><th>Kat.</th><th>✓</th><th></th></tr>` +
    (qs.map((x) => `<tr>
      <td>${esc(x.text)}</td><td>${x.skill}</td><td>${esc(x.category)}</td>
      <td>${"ABCD"[x.correct - 1]}</td>
      <td style="white-space:nowrap">
        <button class="qedit" data-qid="${x.id}">edit</button>
        <button class="qdel danger" data-qid="${x.id}">×</button></td></tr>`).join("")
     || '<tr><td class="muted">Keine Fragen</td></tr>');
  document.querySelectorAll(".qdel").forEach((b) => (b.onclick = async () => {
    if (await Dlg.confirm("Frage löschen?")) { await api(`/api/questions/${b.dataset.qid}`, "DELETE"); loadQuestions(); }
  }));
  document.querySelectorAll(".qedit").forEach((b) => (b.onclick = () => {
    const x = window._qs.find((v) => v.id === +b.dataset.qid);
    if (x) openQuestionEditor(x, { kind: "q", id: x.id, category: x.category });
  }));
  const pages = Math.max(1, Math.ceil(qTotal / Q_PAGE_SIZE));
  document.getElementById("qPager").innerHTML = qTotal > Q_PAGE_SIZE
    ? `<button id="qPrev" ${qPage === 0 ? "disabled" : ""}>◀</button>
       <span class="muted">Seite ${qPage + 1} / ${pages} (${qTotal} Fragen)</span>
       <button id="qNext" ${qPage >= pages - 1 ? "disabled" : ""}>▶</button>`
    : `<span class="muted">${qTotal} Fragen</span>`;
  const prev = document.getElementById("qPrev"), next = document.getElementById("qNext");
  if (prev) prev.onclick = () => { qPage--; loadQuestions(); };
  if (next) next.onclick = () => { qPage++; loadQuestions(); };
}

document.getElementById("qSearchBtn").onclick = () => { qPage = 0; loadQuestions(); };
document.getElementById("qSearch").addEventListener("input",
  debounce(() => { qPage = 0; loadQuestions(); }));
document.getElementById("qSkillFilter").onchange = () => { qPage = 0; loadQuestions(); };
document.getElementById("cqSearch").addEventListener("input",
  debounce(() => { cqPage = 0; loadCustomQuestions(); }));
document.getElementById("cqSkillFilter").onchange =
  () => { cqPage = 0; loadCustomQuestions(); };

// ---------------- reported questions ----------------
const RQ_PAGE_SIZE = 10;
let rqPage = 0;

async function loadReported() {
  const qs = await api("/api/questions/reported");
  document.getElementById("sub-reported").textContent =
    qs.length ? `${qs.length} gemeldet` : "keine";
  window._rqs = qs;
  const rqPages = Math.max(1, Math.ceil(qs.length / RQ_PAGE_SIZE));
  if (rqPage >= rqPages) rqPage = rqPages - 1;
  const page = qs.slice(rqPage * RQ_PAGE_SIZE, (rqPage + 1) * RQ_PAGE_SIZE);
  document.getElementById("reportedTable").innerHTML =
    `<tr><th>Frage</th><th>Pool</th><th>✓</th><th></th></tr>` +
    (page.map((x) => `<tr>
      <td>${esc(x.text)}</td>
      <td><span class="badge">${esc(x.pool)}</span></td>
      <td>${"ABCD"[x.correct - 1]}</td>
      <td style="white-space:nowrap">
        <button class="rqedit" data-i="${qs.indexOf(x)}">edit</button>
        <button class="rqreact primary" data-src="${x.source}" data-qid="${x.id}">Aktivieren</button>
        <button class="rqdel danger" data-i="${qs.indexOf(x)}">×</button></td></tr>`).join("")
     || '<tr><td class="muted" colspan="4">Keine gemeldeten Fragen</td></tr>');
  document.getElementById("rqPager").innerHTML = qs.length > RQ_PAGE_SIZE
    ? `<button id="rqPrev" ${rqPage === 0 ? "disabled" : ""}>◀</button>
       <span class="muted">Seite ${rqPage + 1} / ${rqPages} (${qs.length} gemeldet)</span>
       <button id="rqNext" ${rqPage >= rqPages - 1 ? "disabled" : ""}>▶</button>`
    : "";
  const rqPrev = document.getElementById("rqPrev"), rqNext = document.getElementById("rqNext");
  if (rqPrev) rqPrev.onclick = () => { rqPage--; loadReported(); };
  if (rqNext) rqNext.onclick = () => { rqPage++; loadReported(); };
  document.querySelectorAll(".rqreact").forEach((b) => (b.onclick = async () => {
    await api(`/api/questions/reported/${b.dataset.src}/${b.dataset.qid}/reactivate`, "POST");
    loadReported();
  }));
  document.querySelectorAll(".rqdel").forEach((b) => (b.onclick = async () => {
    const x = window._rqs[+b.dataset.i];
    if (!(await Dlg.confirm("Frage endgültig löschen?"))) return;
    await api(x.source === "c"
      ? `/api/categories/${x.category_id}/questions/${x.id}`
      : `/api/questions/${x.id}`, "DELETE");
    loadReported(); loadQuestions();
  }));
  document.querySelectorAll(".rqedit").forEach((b) => (b.onclick = () => {
    const x = window._rqs[+b.dataset.i];
    if (x) openQuestionEditor(x, {
      kind: "r", id: x.id, source: x.source,
      catId: x.category_id, category: x.category || "",
    });
  }));
}

// ---------------- statistics ----------------
const ST_PAGE_SIZE = 10;
let stEvents = [], stGameSeq = 0, stPage = 0;

async function loadStats() {
  const st = await api("/api/settings/stats");
  stGameSeq = st.game_seq;

  document.getElementById("stSkills").innerHTML =
    `<tr><th>Level</th><th>Gesamt</th><th>Frei</th><th>Genutzt</th><th>Gemeldet</th></tr>` +
    (st.skills.map((s) => `<tr>
      <td>${"★".repeat(s.skill)}</td><td>${s.total}</td>
      <td>${s.free}</td><td>${s.used}</td><td>${s.reported}</td></tr>`).join("")
     || '<tr><td class="muted" colspan="5">Keine Fragen</td></tr>');

  document.getElementById("stCats").innerHTML =
    `<tr><th>Kategorie</th><th>Gesamt</th><th>Frei</th><th>Genutzt</th><th>Gemeldet</th></tr>` +
    (st.categories.map((c) => `<tr>
      <td>${esc(c.name)}${c.custom ? ' <span class="badge">eigene</span>' : ""}</td>
      <td>${c.total}</td><td>${c.free}</td>
      <td>${c.used}</td><td>${c.reported}</td></tr>`).join("")
     || '<tr><td class="muted" colspan="5">Keine Fragen</td></tr>');

  const ptable = (rows, showGames) => rows.length
    ? `<tr><th>Spieler</th>${showGames ? "<th>Spiele</th>" : ""}
       <th>Antw.</th><th>✓</th><th>✗</th><th>Quote</th><th>Pkt.</th></tr>` +
      rows.map((p) => {
        const rate = p.answered ? Math.round(100 * p.correct / p.answered) : 0;
        return `<tr><td>${esc(p.name)}</td>${showGames ? `<td>${p.games}</td>` : ""}
          <td>${p.answered}</td><td>${p.correct}</td><td>${p.wrong}</td>
          <td>${rate}%</td><td>${p.points}</td></tr>`;
      }).join("")
    : '<tr><td class="muted">Noch keine Antworten</td></tr>';
  document.getElementById("stPlayersGame").innerHTML =
    ptable(st.players_game, false);
  document.getElementById("stPlayersAll").innerHTML =
    ptable(st.players_all, true);

  stEvents = st.events;
  stPage = 0;
  renderStEvents();
}

function renderStEvents() {
  const ICON = { correct: "✓", wrong: "✗", skipped: "⤼" };
  const CLS = { correct: "st-ok", wrong: "st-bad", skipped: "muted" };
  const pages = Math.max(1, Math.ceil(stEvents.length / ST_PAGE_SIZE));
  const page = stEvents.slice(stPage * ST_PAGE_SIZE, (stPage + 1) * ST_PAGE_SIZE);
  document.getElementById("stEvents").innerHTML = page.map((e) => {
    const pts = e.points ? ` ${e.points > 0 ? "+" : ""}${e.points}` : "";
    return `<div class="st-ev">
      <span class="badge">${e.game === stGameSeq ? "aktuell" : `Spiel ${e.game}`}</span>
      <span class="muted">${esc(e.round)}</span>
      <b>${esc(e.player)}</b>
      <span class="st-q">${esc(e.question)}</span>
      <span class="${CLS[e.outcome] || ""}">${ICON[e.outcome] || e.outcome}${pts}</span>
    </div>`;
  }).join("") || '<div class="muted">Noch keine Einträge</div>';
  document.getElementById("stEventsPager").innerHTML =
    stEvents.length > ST_PAGE_SIZE
      ? `<button id="stPrev" ${stPage === 0 ? "disabled" : ""}>◀</button>
         <span class="muted">Seite ${stPage + 1} / ${pages} (${stEvents.length} Einträge)</span>
         <button id="stNext" ${stPage >= pages - 1 ? "disabled" : ""}>▶</button>`
      : "";
  const prev = document.getElementById("stPrev"), next = document.getElementById("stNext");
  if (prev) prev.onclick = () => { stPage--; renderStEvents(); };
  if (next) next.onclick = () => { stPage++; renderStEvents(); };
}

document.getElementById("nq_add").onclick = async () => {
  const checked = document.querySelector('input[name="nq_correct"]:checked');
  const body = {
    text: nq_text.value, answer1: nq_a1.value, answer2: nq_a2.value,
    answer3: nq_a3.value, answer4: nq_a4.value,
    correct: checked ? +checked.value : 1,
    skill: +nq_skill.value,
  };
  if (!body.text || !body[`answer${body.correct}`])
    return Dlg.alert("Frage + richtige Antwort ausfüllen");
  await api("/api/questions", "POST", body);
  ["nq_text", "nq_a1", "nq_a2", "nq_a3", "nq_a4"].forEach((id) => (document.getElementById(id).value = ""));
  document.querySelector('input[name="nq_correct"][value="1"]').checked = true;
  taGrow(document.getElementById("nq_text"));
  loadQuestions();
};

// ---------------- question export ----------------
let expPrecheck = null;  // category value to pre-tick when the dialog opens

async function loadExportCats() {
  const [stdCats, cats] = await Promise.all([
    api("/api/questions/categories"), api("/api/categories")]);
  const row = (val, label) =>
    `<label class="field" style="margin:4px 0"><input type="checkbox" class="expCat" value="${esc(val)}"> ${esc(label)}</label>`;
  const el = document.getElementById("expCats");
  el.innerHTML =
    stdCats.map((c) => row(`std:${c}`, c)).join("") +
    cats.map((c) => row(`cat:${c.id}`, `Kategorie: ${c.name}`)).join("") ||
    '<span class="muted">Keine Kategorien</span>';
  if (expPrecheck) {
    const cb = el.querySelector(`.expCat[value="${expPrecheck}"]`);
    if (cb) cb.checked = true;
    expPrecheck = null;
  }
}

document.getElementById("cqExport").onclick = () => {
  if (activeCat) expPrecheck = `cat:${activeCat}`;
  openModal("m-export");
};

document.getElementById("expGo").onclick = () => {
  const params = new URLSearchParams();
  params.set("format",
    document.querySelector('input[name="exp_fmt"]:checked').value);
  const skill = document.getElementById("exp_skill").value;
  if (skill) params.set("skill", skill);
  document.querySelectorAll(".expCat:checked").forEach((c) =>
    params.append("cats", c.value));
  window.location.assign(`/api/questions/export?${params}`);
};

const impOverlay = (on) =>
  document.getElementById("impOverlay").classList.toggle("hidden", !on);

document.getElementById("qImport").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  impOverlay(true);
  try {
    const r = await fetch("/api/questions/import", { method: "POST", body: fd });
    const j = await r.json();
    await Dlg.alert(r.ok ? `${j.imported} Fragen importiert` : j.detail || "Fehler");
  } finally {
    impOverlay(false);
    e.target.value = "";
  }
  loadQuestions();
};

document.getElementById("qSeedBtn").onclick = async () => {
  const raw = await Dlg.prompt(
    "Wie viele Fragen von OpenTriviaDB laden? (max. 200, werden ins Deutsche übersetzt)",
    "20", { type: "number" });
  const amount = +raw;
  if (!amount) return;
  if (amount > 200) {
    await Dlg.alert("Maximal 200 Fragen pro Import.");
    return;
  }
  impOverlay(true);
  try {
    const j = await api("/api/questions/seed", "POST", { amount, translate: true });
    await Dlg.alert(`${j.imported} Fragen importiert`);
  } catch (e) { /* api() already alerted */ } finally {
    impOverlay(false);
  }
  loadQuestions();
};

document.getElementById("resetUsed").onclick = async () => {
  if (await Dlg.confirm("Alle 'genutzt'-Markierungen zurücksetzen?")) {
    await api("/api/settings/reset-questions", "POST");
    loadQuestions();
  }
};

// ---------------- init ----------------
(async () => {
  for (const fn of [loadSettings, loadPlayers, loadCategories, loadRounds, loadQuestions, loadReported]) {
    try { await fn(); } catch (e) { console.error(fn.name, "failed:", e); }
  }
  document.getElementById("s_ppr").onchange = loadPreview;
  document.getElementById("s_pre").onchange = loadPreview;
  document.getElementById("s_reveal").onchange = (e) =>
    (document.getElementById("s_reveal_pause").disabled = e.target.value !== "seq");
  document.getElementById("s_maxq_on").onchange = (e) =>
    MAXQ_STAGES.forEach((s) =>
      (document.getElementById("s_maxq_" + s).disabled = !e.target.checked));
})();
