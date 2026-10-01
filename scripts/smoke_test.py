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
correct, _ = game.pick_answer(db, 2)
assert correct is False
st = game.board_state(db)
scores = {p["slot"]: p["score"] for p in st["players"]}
assert scores[2] == 0 and scores[1] == 2, scores
assert game.get_state()["blocked_slots"] == [2]
assert game.serial_commands_for_phase() == ["R"]
print("OK falsche Antwort: andere +2, Slot 2 gesperrt, cmd=R")

# next question: blocked slot 2 can't buzz
game.show_question(db)
assert game.serial_commands_for_phase() == ["5", "1"]  # block slot2 -> index 1
slot, ok = game.buzzer_pressed(db, 2)
assert not ok
slot, ok = game.buzzer_pressed(db, 1)
assert ok
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

db.close()
print("\nAlle Tests bestanden.")
