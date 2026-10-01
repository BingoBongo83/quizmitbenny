const LETTERS = ["A", "B", "C", "D"];
const DEFAULT_SEAT_COLORS = ["#2e6bff", "#ffd23c", "#ff8c1a", "#ff5ec4", "#2ee56f"];

let isAdmin = false;
let prevScores = {};
let prevPhase = "idle";
let prevFifty = false;
let prevDouble = false;
let prevArmed = false;
let prevAudience = false;
let audio = null;

// ---------- sound ----------
function ensureAudio() {
  if (!audio) {
    try { audio = new (window.AudioContext || window.webkitAudioContext)(); }
    catch { return null; }
  }
  if (audio.state === "suspended") audio.resume();
  return audio;
}

function tone(freq, dur, type = "sine", gain = 0.18, when = 0) {
  const ctx = ensureAudio();
  if (!ctx || ctx.state !== "running") return;
  const t = ctx.currentTime + when;
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.type = type;
  o.frequency.value = freq;
  g.gain.setValueAtTime(gain, t);
  g.gain.exponentialRampToValueAtTime(0.001, t + dur);
  o.connect(g).connect(ctx.destination);
  o.start(t);
  o.stop(t + dur);
}

const sounds = {
  question() { tone(660, 0.12, "triangle"); tone(880, 0.18, "triangle", 0.12, 0.1); },
  buzzed() { tone(1200, 0.09, "square", 0.12); tone(1600, 0.14, "square", 0.12, 0.09); },
  correct() { [523, 659, 784, 1047].forEach((f, i) => tone(f, 0.22, "triangle", 0.16, i * 0.09)); },
  wrong() { tone(220, 0.4, "sawtooth", 0.14); tone(160, 0.5, "sawtooth", 0.12, 0.12); },
  joker_fifty() { tone(920, 0.07, "square", 0.14); tone(620, 0.1, "square", 0.14, 0.09); },
  joker_double() { [660, 880, 1320].forEach((f, i) => tone(f, 0.14, "triangle", 0.14, i * 0.08)); },
  joker_audience() { [392, 494, 587].forEach((f, i) => tone(f, 0.38, "sine", 0.1, i * 0.07)); },
};

// real sound files in /static/sounds/, synthesized jingles as fallback
const SOUND_FILES = {
  question: "question", buzzed: "buzzer", correct: "correct",
  wrong: "wrong", joker_fifty: "joker", joker_double: "joker",
  joker_audience: "joker", joker_activate: "joker_activate",
};
const audioCache = {};
function playSound(name) {
  const file = SOUND_FILES[name];
  if (file) {
    try {
      const a = audioCache[file] ||= new Audio(`/static/sounds/${file}.mp3`);
      a.currentTime = 0;
      const p = a.play();
      if (p && p.catch) p.catch(() => { if (sounds[name]) sounds[name](); });
      return;
    } catch { /* fall through to synth */ }
  }
  if (sounds[name]) sounds[name]();
}

// unlock audio on first interaction; small toggle button
const tog = document.getElementById("soundToggle");
let soundOn = true;
tog.onclick = () => { soundOn = !soundOn; tog.textContent = soundOn ? "Ton: an" : "Ton: aus"; if (soundOn) ensureAudio(); };
document.addEventListener("click", ensureAudio, { once: true });

// ---------- admin check for click-to-buzz ----------
fetch("/api/me").then((r) => r.json()).then((j) => { isAdmin = !!j.admin; }).catch(() => {});

function buzz(slot) {
  if (!isAdmin) return;
  QuizWS.send({ type: "buzzer", buzzer: slot });
}

// ---------- render ----------
function render(state) {
  const rl = document.getElementById("roundLabel");
  rl.textContent = state.round
    ? `${state.round.type_label}${state.round.type === "preround" ? " " + state.round.number : ""}`
    : "Quiz mit Benny";

  const wrap = document.getElementById("players");
  wrap.innerHTML = "";
  const seatColors = state.seat_colors && state.seat_colors.length
    ? state.seat_colors : DEFAULT_SEAT_COLORS;
  state.players.forEach((p) => {
    const d = document.createElement("div");
    d.className = "player-card"
      + (p.buzzed ? " buzzed" : "")
      + (p.blocked ? " blocked" : "")
      + (isAdmin ? " clickable" : "");
    const hex = seatColors[p.slot - 1] || "#888";
    d.style.setProperty("--slot", hex);
    d.style.setProperty("--slot-glow", hex + "66");  // 40% alpha
    const pop = prevScores[p.slot] !== undefined && prevScores[p.slot] !== p.score;
    const JICON = { fifty: "50", double: "2×", audience: "👥" };
    const JTIP = { fifty: "50:50", double: "Doppelte Punkte", audience: "Publikumsjoker" };
    const jk = (state.jokers_enabled || [])
      .map((k) => `<span class="jchip ${k} ${(p.jokers || {})[k] ? "used" : ""}"
        title="${JTIP[k]}">${JICON[k]}</span>`)
      .join("");
    d.innerHTML = `<div class="pname">${esc(p.name)}</div>
                   <div class="pscore${pop ? " pop" : ""}">${p.score}</div>
                   ${jk ? `<div class="pjokers">${jk}</div>` : ""}`;
    if (isAdmin) {
      d.title = "Klicken = Buzzer simulieren";
      d.onclick = () => buzz(p.slot);
    }
    prevScores[p.slot] = p.score;
    wrap.appendChild(d);
  });

  const qbox = document.getElementById("questionBox");
  const idle = document.getElementById("idleBox");
  document.querySelector(".board").classList.toggle("idle-mode", !state.question);
  if (state.question) {
    qbox.classList.remove("hidden");
    idle.classList.add("hidden");
    const q = state.question;
    document.getElementById("qmeta").innerHTML =
      `<span class="stars">${"★".repeat(q.skill)}${"☆".repeat(5 - q.skill)}</span>` +
      (q.category ? ` · ${esc(q.category)}` : "") +
      (state.double_active ? ` <span class="badge joker-active">2× PUNKTE</span>` : "") +
      (state.audience_pick ? ` <span class="badge joker-active">Publikum: ${LETTERS[state.audience_pick - 1]}</span>` : "");
    document.getElementById("qtext").textContent = q.text;
    const aw = document.getElementById("answers");
    aw.innerHTML = "";
    const hidden = state.fifty_hidden || [];
    q.answers.forEach((a, i) => {
      const d = document.createElement("div");
      d.className = "answer";
      if (hidden.includes(i + 1)) {
        d.classList.add("fifty-hidden");
        d.innerHTML = `<span class="letter">${LETTERS[i]}</span><span>—</span>`;
      } else {
        if (state.phase === "resolved") {
          if (i + 1 === q.correct) d.classList.add("correct");
          else if (i + 1 === q.picked) d.classList.add("wrong");
        }
        if (state.audience_pick === i + 1) d.classList.add("audience-pick");
        d.innerHTML = `<span class="letter">${LETTERS[i]}</span><span>${esc(a)}</span>`;
      }
      aw.appendChild(d);
    });
  } else {
    qbox.classList.add("hidden");
    idle.classList.remove("hidden");
    idle.textContent = state.game_started
      ? (state.round ? "Buzzer bereit" : "Nächste Runde")
      : "Quiz startet gleich";
  }

  // sounds on phase transitions (only if this host is the sound target)
  const playHere = (state.sound_target || "board") !== "admin";
  if (soundOn && playHere) {
    if (state.phase !== prevPhase) {
      if (state.phase === "question") playSound("question");
      else if (state.phase === "buzzed") playSound("buzzed");
      else if (state.phase === "resolved") {
        const q = state.question;
        playSound(q && q.picked === q.correct ? "correct" : "wrong");
      }
    }
    // joker activation effects (not phase changes -> watch field transitions)
    const fiftyNow = (state.fifty_hidden || []).length > 0;
    if (fiftyNow && !prevFifty) { playSound("joker_activate"); playSound("joker_fifty"); }
    if (state.double_active && !prevDouble) { playSound("joker_activate"); playSound("joker_double"); }
    if (state.audience_armed && !prevArmed) playSound("joker_activate");
    if (state.audience_pick && !prevAudience) playSound("joker_audience");
  }
  prevPhase = state.phase;
  prevFifty = (state.fifty_hidden || []).length > 0;
  prevDouble = !!state.double_active;
  prevArmed = !!state.audience_armed;
  prevAudience = !!state.audience_pick;
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

QuizWS.on("state", (m) => render(m.data));
