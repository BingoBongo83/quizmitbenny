const LETTERS = ["A", "B", "C", "D"];

let voter = localStorage.getItem("audience_voter");
if (!voter) {
  voter = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));
  localStorage.setItem("audience_voter", voter);
}
let myVote = null;
let lastQid = null;
let lastState = null;

const WAIT_HTML = (text) => `<div class="aud-wait">
  <div class="wave">${"<span></span>".repeat(5)}</div>
  <p class="muted">${text}</p>
</div>`;

function render(st) {
  lastState = st;
  const el = document.getElementById("audState");
  const open = st.audience_voting && st.question;
  if (!open) {
    const done = st.question && st.audience_result;
    el.innerHTML = WAIT_HTML(done
      ? "Voting beendet – Ergebnis läuft am Scoreboard"
      : "Warte auf den Joker…");
    return;
  }
  if (st.question.id !== lastQid) { myVote = null; lastQid = st.question.id; }
  const hidden = st.fifty_hidden || [];
  el.innerHTML =
    `<div class="aud-q">${esc(st.question.text)}</div>
     <div class="aud-answers">` +
    st.question.answers.map((a, i) => hidden.includes(i + 1)
      ? ""
      : `<button class="aud-btn ${myVote === i + 1 ? "mine" : ""}" data-a="${i + 1}">
           <span class="letter">${LETTERS[i]}</span>${esc(a)}</button>`).join("") +
    `</div>
     <p class="muted">${myVote
       ? "Stimme gezählt – du kannst bis zum Stopp wechseln"
       : "Tippe deine Antwort"}</p>`;
  el.querySelectorAll(".aud-btn").forEach((b) => (b.onclick = () => {
    myVote = +b.dataset.a;
    QuizWS.send({ type: "audience_vote", answer: myVote, voter });
    render(lastState);
  }));
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

QuizWS.on("state", (m) => render(m.data));
