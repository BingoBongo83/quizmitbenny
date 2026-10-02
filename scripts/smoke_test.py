"""Smoke test: exercise the whole game flow against a test DB.

Run:  DATABASE_URL=sqlite:///./quiz_test.db python -m scripts.smoke_test
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "sqlite:///./quiz_test.db")

from app.db import Base, SessionLocal, engine
from app import game
from app.models import Player, Question, Round, RoundPlayer

Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
db = SessionLocal()

# --- seed: 12 players, 40 questions ---
players = [Player(name=f"Spieler {i+1}") for i in range(12)]
db.add_all(players)
for i in range(40):
    db.add(Question(
        text=f"Frage {i+1}?", answer1="Richtig", answer2="f2",
        answer3="f3", answer4="f4", correct=1, skill=(i % 5) + 1,
        category="Test",
    ))
db.commit()

# --- create game: 12 players, defaults (4/round, 3 prerounds) ---
game.create_game(db, [p.id for p in players], shuffle=True)
rounds = db.query(Round).order_by(Round.number).all()
assert len(rounds) == 3, f"expected 3 prerounds, got {len(rounds)}"
assert all(r.type == "preround" for r in rounds)
sizes = [db.query(RoundPlayer).filter(RoundPlayer.round_id == r.id).count() for r in rounds]
assert sizes == [4, 4, 4], sizes
print(f"OK 3 Vorrunden à {sizes[0]} erstellt")

# --- round 1: full question cycle ---
r1 = rounds[0]
game.start_round(db, r1.id)
assert game.get_state()["pending_question_id"] is not None
q, err = game.show_question(db)
assert q and not err
assert game.get_state()["phase"] == "question"
cmds = game.serial_commands_for_phase()
assert cmds == ["5", "9"], cmds

# buzzer 2 presses
slot, ok = game.buzzer_pressed(db, 2)
assert ok and slot == 2
# double-buzz ignored
slot, ok = game.buzzer_pressed(db, 3)
assert not ok
# wrong answer → others +2, slot 2 blocked
# (lock-in: first click locks, second click judges)
res, _ = game.pick_answer(db, 2)
assert res == "locked", res
# other answers are rejected while locked
res, _ = game.pick_answer(db, 3)
assert res == "locked"
correct, _ = game.pick_answer(db, 2)
assert correct is False
st = game.board_state(db)
scores = {p["slot"]: p["score"] for p in st["players"]}
assert scores[2] == 0 and scores[1] == 2, scores
assert game.get_state()["blocked_slots"] == [2]
assert game.serial_commands_for_phase() == ["R", "1"]  # wrong + block slot2 -> red
print("OK falsche Antwort: andere +2, Slot 2 gesperrt, cmd=R+Sperre")

# next question: blocked slot 2 can't buzz
game.show_question(db)
assert game.serial_commands_for_phase() == ["5", "1"]  # block slot2 -> index 1
slot, ok = game.buzzer_pressed(db, 2)
assert not ok
slot, ok = game.buzzer_pressed(db, 1)
assert ok
game.pick_answer(db, 1)  # lock-in click
correct, _ = game.pick_answer(db, 1)
assert correct is True
rp = db.query(RoundPlayer).filter(RoundPlayer.round_id == r1.id, RoundPlayer.slot == 1).first()
assert rp.score == 4
assert game.get_state()["blocked_slots"] == []
print("OK richtige Antwort: +2, Sperre aufgehoben, cmd=G")

# --- finish all 3 prerounds -> semifinals + maybe playoff ---
for r in rounds:
    if r.status != "finished":
        game.start_round(db, r.id)
        game.finish_round(db, r.id)

rounds = db.query(Round).order_by(Round.number).all()
types = [r.type for r in rounds]
# 12 players, 3x4, semis need 8: 6 direct + playoff for 2
assert "playoff_presemi" in types, types
playoff = [r for r in rounds if r.type == "playoff_presemi"][0]
po_players = db.query(RoundPlayer).filter(RoundPlayer.round_id == playoff.id).count()
assert po_players == 4, po_players
print(f"OK Playoff vor Halbfinale mit {po_players} Spielern erstellt")

game.start_round(db, playoff.id)
game.finish_round(db, playoff.id)
semis = db.query(Round).filter(Round.type == "semifinal").order_by(Round.number).all()
assert len(semis) == 2
sizes = [db.query(RoundPlayer).filter(RoundPlayer.round_id == r.id).count() for r in semis]
assert sizes == [4, 4], sizes
print(f"OK 2 Halbfinals à {sizes[0]} erstellt")

# finish semis -> final (top 2 each = 4 finalists, N=4, no playoff needed)
for r in semis:
    game.start_round(db, r.id)
    game.finish_round(db, r.id)
final = db.query(Round).filter(Round.type == "final").first()
assert final
fp = db.query(RoundPlayer).filter(RoundPlayer.round_id == final.id).count()
assert fp == 4, fp
print("OK Finale mit 4 Spielern erstellt")

game.start_round(db, final.id)
game.finish_round(db, final.id)
ranked = db.query(RoundPlayer).filter(RoundPlayer.round_id == final.id).order_by(RoundPlayer.score.desc()).all()
print(f"OK Spiel beendet. Sieger: {db.get(Player, ranked[0].player_id).name}")

# --- category pool: custom questions only when category chosen ---
from app.models import Category, CustomQuestion
cat = Category(name="Testkategorie")
db.add(cat)
db.flush()
for i in range(5):
    db.add(CustomQuestion(
        category_id=cat.id, text=f"Eigene Frage {i+1}?",
        answer1="R", answer2="x", answer3="y", answer4="z",
        correct=1, skill=3,
    ))
db.commit()

# new game, first round on the category pool
db.query(RoundPlayer).delete(); db.query(Round).delete()
db.query(Question).update({Question.used: False})
db.query(CustomQuestion).update({CustomQuestion.used: False})
game.get_state().update({"phase": "idle", "round_id": None,
                         "pending_question_id": None, "pending_source": None,
                         "question_source": None, "game_started": False})
game.create_game(db, [p.id for p in players], shuffle=True)
r1 = db.query(Round).order_by(Round.number).first()
r1.question_pool = f"cat:{cat.id}"
db.commit()
game.start_round(db, r1.id)
q, err = game.show_question(db)
assert q and isinstance(q, CustomQuestion), "expected a custom question"
assert q.text.startswith("Eigene Frage")
print("OK Kategorie-Pool: eigene Frage gezogen")

# category exhausted -> falls back to any unused custom, then standard
db.query(CustomQuestion).update({CustomQuestion.used: True})
db.commit()
game.get_state().update({"phase": "idle", "question_id": None,
                         "pending_question_id": None, "pending_source": None})
q, err = game.show_question(db)
assert q is not None  # fell back to standard pool
assert not isinstance(q, CustomQuestion)
print("OK Kategorie leer -> Fallback auf Standard-Pool")

# standard pool never serves custom questions
db.query(CustomQuestion).update({CustomQuestion.used: False})
db.commit()
r1.question_pool = "standard"
db.commit()
game.get_state().update({"phase": "idle", "question_id": None,
                         "pending_question_id": None, "pending_source": None})
for _ in range(5):
    q, _src = game.pick_question(db)
    assert not isinstance(q, CustomQuestion)
print("OK Standard-Pool zieht niemals eigene Fragen")

# --- jokers ---
db.query(RoundPlayer).delete(); db.query(Round).delete()
db.query(Question).update({Question.used: False})
db.query(CustomQuestion).update({CustomQuestion.used: False})
game.get_state().update({"phase": "idle", "round_id": None,
                         "question_id": None, "question_source": None,
                         "pending_question_id": None, "pending_source": None,
                         "buzzed_slot": None, "picked_answer": None,
                         "correct_answer": None, "blocked_slots": [],
                         "game_started": False,
                         "jokers": {}, "fifty_hidden": [],
                         "double_active": False, "audience_voting": False,
                         "audience_votes": {}, "audience_result": None,
                         "audience_pick": None, "locked_answer": None})
from app.models import set_setting
set_setting(db, "players_per_round", 4)
set_setting(db, "num_prerounds", 3)
pids = [p.id for p in db.query(Player).order_by(Player.id).limit(12).all()]
game.create_game(db, pids, shuffle=False)
r1 = db.query(Round).order_by(Round.number).first()
assert r1.status == "pending"  # moderator starts the quiz explicitly
game.start_round(db, r1.id)
assert game.get_state()["round_id"] == r1.id

# joker requires a buzzed player first
ok, err = game.use_joker(db, "fifty")
assert not ok and "Buzzern" in err
q, _ = game.show_question(db)
slot, ok = game.buzzer_pressed(db, 1)
assert ok

# 50:50 hides exactly two wrong answers, correct stays
ok, err = game.use_joker(db, "fifty")
assert ok, err
st = game.board_state(db)
hid = st["fifty_hidden"]
assert len(hid) == 2 and q.correct not in hid
ok, err = game.use_joker(db, "fifty")
assert not ok and "verbraucht" in err

# double points: correct answer scores 2x
ok, err = game.use_joker(db, "double")
assert ok, err
assert game.get_state()["double_active"]
res, _ = game.pick_answer(db, q.correct)
assert res == "locked"
correct, _ = game.pick_answer(db, q.correct)
assert correct
rp = db.query(RoundPlayer).filter(
    RoundPlayer.round_id == r1.id, RoundPlayer.slot == 1).first()
assert rp.score == 4, rp.score
assert not game.get_state()["double_active"] or game.get_state()["phase"] == "resolved"
print("OK Joker: 50:50 + doppelte Punkte (2x auf richtig)")

# audience joker: /audience vote -> moderator stops -> bars on board
q, _ = game.show_question(db)
slot, ok = game.buzzer_pressed(db, 1)
assert ok
ok, err = game.use_joker(db, "audience")
assert ok, err
assert game.get_state()["audience_voting"]
# three audience votes (recast allowed -> last counts)
assert game.audience_vote(db, "v1", 2)
assert game.audience_vote(db, "v2", 2)
assert game.audience_vote(db, "v2", 1)  # v2 changes vote
assert game.audience_vote(db, "v3", 1)
assert not game.audience_vote(db, "v4", 9)  # invalid answer
assert game.stop_audience_voting(db)
st = game.board_state(db)
assert not st["audience_voting"]
assert st["audience_result"] == [67, 33, 0, 0], st["audience_result"]
assert st["audience_pick"] == 1  # majority (first max)
assert not game.audience_vote(db, "v5", 3)  # voting closed
game.pick_answer(db, q.correct)
correct, _ = game.pick_answer(db, q.correct)
assert correct is True
j = game.board_state(db)["players"][0]["jokers"]
assert j == {"fifty": True, "double": True, "audience": True}, j
print("OK Joker: Publikumsjoker + Verbrauch-Tracking pro Spieler")

# answer_lockin off -> first moderator click judges directly;
# but after the audience joker the lock-in flow always applies
set_setting(db, "answer_lockin", False)
q, _ = game.show_question(db)
game.buzzer_pressed(db, 3)
res, _ = game.pick_answer(db, q.correct)
assert res is True, res  # direct judgment, no lock-in step
assert game.get_state()["phase"] == "resolved"
print("OK Antwort-Einloggen aus: erster Klick löst direkt auf")

q, _ = game.show_question(db)
game.buzzer_pressed(db, 3)
ok, _ = game.use_joker(db, "audience")
assert ok
game.audience_vote(db, "v1", q.correct)
game.stop_audience_voting(db)
res, _ = game.pick_answer(db, q.correct)
assert res == "locked"  # audience joker forces lock-in even with setting off
correct, _ = game.pick_answer(db, q.correct)
assert correct is True
set_setting(db, "answer_lockin", True)
print("OK Publikumsjoker erzwingt Einloggen trotz deaktivierter Einstellung")

# disabled joker is rejected
set_setting(db, "joker_fifty", False)
game.show_question(db)
game.buzzer_pressed(db, 2)
ok, err = game.use_joker(db, "fifty")
assert not ok and "deaktiviert" in err
set_setting(db, "joker_fifty", True)
print("OK Joker: deaktivierter Joker wird abgelehnt")

# finalists get a fresh set of jokers
for rp2 in db.query(RoundPlayer).filter(RoundPlayer.round_id == r1.id):
    rp2.qualified = "direct"
db.flush()
game._create_next_rounds(db, "final", 1)
fin = db.query(Round).filter(Round.type == "final").first()
assert fin
fids = {rp2.player_id for rp2 in db.query(RoundPlayer).filter(
    RoundPlayer.round_id == fin.id)}
for pid in fids:
    j = game.get_state()["jokers"].get(str(pid), {})
    assert j == {"fifty": False, "double": False, "audience": False}, (pid, j)
print("OK Joker: Finalisten bekommen neue Joker")

# --- question reporting ---
# report the shown question: flagged + hidden, excluded from future picks
game.skip_question(db)
q, _ = game.show_question(db)
assert q
ok, err = game.report_question(db, "current")
assert ok and q.reported
assert game.get_state()["phase"] == "idle"
db.refresh(q)
assert q.reported
# reported question is never picked again
for _ in range(10):
    qx, _sx = game.pick_question(db)
    assert qx.id != q.id
# report the pending preview question -> re-picked
pid = game.get_state()["pending_question_id"]
assert pid
ok, err = game.report_question(db, "next")
assert ok
assert game.get_state()["pending_question_id"] != pid
# reactivate -> eligible again
db.refresh(q)
q.reported = False
db.commit()
print("OK Melden: Frage aus Pool genommen, Reaktivierung möglich")

# --- alternate config: 4 prerounds x 3 players = 12, semis need 6 ---
db.query(RoundPlayer).delete(); db.query(Round).delete()
db.query(Question).update({Question.used: False})
game.get_state().update({"phase": "idle", "round_id": None, "game_started": False})
from app.models import set_setting
set_setting(db, "players_per_round", 3)
set_setting(db, "num_prerounds", 4)
game.create_game(db, [p.id for p in players], shuffle=True)
rounds = db.query(Round).order_by(Round.number).all()
assert len(rounds) == 4
for r in rounds:
    game.start_round(db, r.id)
    game.finish_round(db, r.id)
rounds = db.query(Round).order_by(Round.number).all()
types = [r.type for r in rounds]
# winners(4) direct + playoff for 2 spots among runner-ups
assert "playoff_presemi" in types, types
print("OK Szenario 4x3: Playoff erstellt")

# --- 5x5 = 25 players, semis need 10 -> 10 direct, no playoff ---
db.query(RoundPlayer).delete(); db.query(Round).delete()
set_setting(db, "players_per_round", 5)
set_setting(db, "num_prerounds", 5)
for i in range(13, 26):
    db.add(Player(name=f"Spieler {i}"))
db.commit()
game.create_game(db, [p.id for p in db.query(Player).all()], shuffle=True)
for r in db.query(Round).filter(Round.type == "preround").all():
    game.start_round(db, r.id)
    game.finish_round(db, r.id)
types = [r.type for r in db.query(Round).all()]
assert "playoff_presemi" not in types, types
assert len(db.query(Round).filter(Round.type == "semifinal").all()) == 2
print("OK Szenario 5x5: kein Playoff, direkte Qualifikation")

# --- tiebreak (Stichfrage) at the question cap ---
db.query(RoundPlayer).delete(); db.query(Round).delete()
db.query(Question).update({Question.used: False})
game.get_state().update({"phase": "idle", "round_id": None,
                         "question_id": None, "question_source": None,
                         "pending_question_id": None, "pending_source": None,
                         "buzzed_slot": None, "picked_answer": None,
                         "correct_answer": None, "blocked_slots": [],
                         "game_started": False, "tiebreak_slots": None})
set_setting(db, "players_per_round", 4)
set_setting(db, "num_prerounds", 3)
set_setting(db, "max_questions_enabled", True)
set_setting(db, "max_questions", 1)
game.create_game(db, [p.id for p in db.query(Player).order_by(Player.id).limit(12)],
               shuffle=False)
r1 = db.query(Round).order_by(Round.number).first()
game.start_round(db, r1.id)
# scores 6,4,4,0 -> top 2 advance -> tie at the boundary (4 vs 4)
for rp, sc in zip(db.query(RoundPlayer).filter(
        RoundPlayer.round_id == r1.id).order_by(RoundPlayer.slot), [6, 4, 4, 0]):
    rp.score = sc
db.commit()
q, err = game.show_question(db)  # cap = 1: first question fine
assert q and not err
game.skip_question(db)
# broadcast hook: detects the tie eagerly, before 'Frage zeigen' is clicked
game.eval_tiebreak(db)
assert game.get_state()["tiebreak_slots"] == [2, 3]
assert game.serial_commands_for_phase() == ["5", "9", "B 25"]
q, err = game.show_question(db)  # cap reached, but tie -> Stichfrage
assert q and not err
assert game.get_state()["tiebreak_slots"] == [2, 3]
# non-tied buzzers get the off-mask: slots 1,4,5 -> bits 0,3,4 = 25
assert game.serial_commands_for_phase() == ["5", "9", "B 25"]
assert game.buzzer_pressed(db, 1) == (1, False)  # non-tied may not buzz
assert game.buzzer_pressed(db, 4) == (4, False)
assert game.buzzer_pressed(db, 2)[1]
res, _ = game.pick_answer(db, q.correct)   # lock-in
res, _ = game.pick_answer(db, q.correct)   # judge -> slot2: 6 pts, tie broken
# scores now 6,6,4,0 -> tie for 1st place -> another Stichfrage
q, err = game.show_question(db)
assert q and not err
assert game.get_state()["tiebreak_slots"] == [1, 2]
assert not game.buzzer_pressed(db, 3)[1]
assert game.buzzer_pressed(db, 1)[1]
res, _ = game.pick_answer(db, q.correct)   # lock-in
res, _ = game.pick_answer(db, q.correct)   # slot1: 8 pts -> clear winner
game.eval_tiebreak(db)                     # resolved phase: tie cleared
assert not game.get_state()["tiebreak_slots"]
q, err = game.show_question(db)            # now truly decided
assert q is None and "Limit" in err
assert not game.get_state()["tiebreak_slots"]
game.finish_round(db, r1.id)
# idle (round end / next round start) clears the off-mask -> all green again
assert game.serial_commands_for_phase() == ["5", "9"]
print("OK Stichfrage: Gleichstand an Grenze + Platz 1, dann beenden")

# --- Stichfrage im Finale: Gleichstand um den Sieg ---
# run the 12-player game through to the final (scores irrelevant here)
while True:
    r = db.query(Round).filter(
        Round.status.in_(["pending", "active"])).order_by(Round.number).first()
    if r is None or r.type == "final":
        break
    if r.status != "active":
        game.start_round(db, r.id)
    game.finish_round(db, r.id)
fin = db.query(Round).filter(Round.type == "final").first()
assert fin
db.query(Question).update({Question.used: False})
game.start_round(db, fin.id)
# last two finalists tied at the top -> Stichfrage for the win
rps = (db.query(RoundPlayer).filter(RoundPlayer.round_id == fin.id)
       .order_by(RoundPlayer.slot).all())
for i, rp in enumerate(rps):
    rp.score = 8 if i >= len(rps) - 2 else 4
db.commit()
q, err = game.show_question(db)
assert q and not err
game.skip_question(db)
q, err = game.show_question(db)  # cap reached, tie for the win -> Stichfrage
assert q and not err, err
tied_slots = [rp.slot for rp in rps if rp.score == 8]
assert game.get_state()["tiebreak_slots"] == tied_slots
mask = sum(1 << (s - 1) for s in range(1, 6) if s not in tied_slots)
assert game.serial_commands_for_phase() == ["5", "9", f"B {mask}"]
print("OK Stichfrage im Finale: Gleichstand um den Sieg wird ausgespielt")
set_setting(db, "max_questions_enabled", False)

db.close()
print("\nAlle Tests bestanden.")
