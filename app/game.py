"""Game engine: bracket computation, state machine, scoring.

Stages: preround -> playoff_presemi -> semifinal -> playoff_prefinal -> final

Advancement rule (musikquiz style):
- From each finished source round, the top A players qualify directly,
  where A = min(2, spots_in_next_stage // num_source_rounds).
- If direct qualifiers don't fill the next stage, a playoff round is created
  from the best remaining players (ranked by place, then score); its top
  `advance_count` players take the open spots.
"""

import json
import random
import threading

from sqlalchemy.orm import Session

from .models import (
    Category,
    ConfigKV,
    CustomQuestion,
    Player,
    Question,
    Round,
    RoundPlayer,
    get_all_settings,
    get_setting,
)

STAGES = ["preround", "playoff_presemi", "semifinal", "playoff_prefinal", "final"]

ROUND_TYPE_LABELS = {
    "preround": "Vorrunde",
    "playoff_presemi": "Playoff",
    "semifinal": "Halbfinale",
    "playoff_prefinal": "Playoff",
    "final": "Finale",
}

_lock = threading.RLock()

# Live game state (also persisted to config for crash recovery)
_state = {
    "phase": "idle",  # idle | question | buzzed | resolved
    "round_id": None,
    "question_id": None,
    "question_source": None,  # 'q' (standard pool) | 'c' (custom/category)
    "pending_question_id": None,  # previewed "next question" for the moderator
    "pending_source": None,
    "buzzed_slot": None,
    "picked_answer": None,
    "correct_answer": None,
    "blocked_slots": [],
    "game_started": False,
    # jokers: player_id -> {"fifty": bool, "double": bool, "audience": bool} (used)
    "jokers": {},
    "fifty_hidden": [],     # answer indexes (0-3) hidden by the 50:50 joker
    "double_active": False,  # current question counts double
    "audience_voting": False,  # /audience voting open
    "audience_votes": {},   # voter_id -> answer 1-4
    "audience_result": None,  # [pct1..4] after the moderator stops voting
    "audience_pick": None,  # majority answer (1-4) after stop
    "locked_answer": None,  # moderator's first click = lock-in, second = judge
    "tiebreak_slots": None,  # Stichfrage: only these slots may buzz (None = all)
}

_subscribers = []  # ws connection manager is injected


def _persist_state(db: Session):
    row = db.query(ConfigKV).filter(ConfigKV.name == "gamestate").first()
    if row is None:
        row = ConfigKV(name="gamestate")
        db.add(row)
    row.value = json.dumps(_state)
    db.commit()


def load_state(db: Session):
    row = db.query(ConfigKV).filter(ConfigKV.name == "gamestate").first()
    if row and row.value:
        try:
            _state.update(json.loads(row.value))
        except (ValueError, TypeError):
            pass


def get_state() -> dict:
    return dict(_state)


# ---------------------------------------------------------------------------
# Bracket
# ---------------------------------------------------------------------------

def compute_bracket_preview(num_players: int, players_per_round: int, num_prerounds: int):
    """Return a textual/structural preview of the tournament."""
    n = players_per_round
    p = num_prerounds
    base, extra = divmod(num_players, p)
    preround_sizes = [base + (1 if i < extra else 0) for i in range(p)]
    preview = {
        "prerounds": preround_sizes,
        "semifinals": [n, n],
        "final": n,
        "warnings": [],
        "playoffs": [],
    }
    if any(sz < n for sz in preround_sizes):
        preview["warnings"].append(
            f"Nicht alle Vorrunden sind voll ({'/'.join(map(str, preround_sizes))} "
            f"statt {n}) – ggf. Vorrunden-Anzahl oder Spielerzahl anpassen"
        )
    if any(sz < 2 for sz in preround_sizes):
        preview["warnings"].append(
            "Mindestens eine Vorrunde hätte nur 1 Spieler – unsinnig!"
        )
    spots_semi = 2 * n
    direct_per_round = min(2, spots_semi // p) if p else 2
    direct = direct_per_round * p
    if direct < spots_semi:
        preview["playoffs"].append(
            f"Playoff vor Halbfinale: beste {min(n, num_players - direct)} "
            f"verbleibenden Spieler um {spots_semi - direct} Plätze"
        )
    elif direct > spots_semi:
        preview["warnings"].append(
            f"{direct} Direktqualifikanten für {spots_semi} Halbfinal-Plätze – "
            "Playoff der Zweitplatzierten wird erstellt"
        )
    # semifinal -> final
    a2 = min(2, n // 2)
    if 2 * a2 < n:
        preview["playoffs"].append(
            f"Playoff vor Finale: {n - 2 * a2} Finalplatz/Plätze per Playoff"
        )
    return preview


def create_game(db: Session, player_ids: list[int], shuffle: bool = True):
    """Create prerounds and assign players. player_ids = chosen players."""
    with _lock:
        db.query(RoundPlayer).delete()
        db.query(Round).delete()
        db.query(Question).update({Question.used: False, Question.used_round_id: None})
        db.query(CustomQuestion).update(
            {CustomQuestion.used: False, CustomQuestion.used_round_id: None}
        )
        s = get_all_settings(db)
        n = s["players_per_round"]
        p = s["num_prerounds"]

        ids = list(player_ids)
        if shuffle:
            random.shuffle(ids)
        base, extra = divmod(len(ids), p)
        idx = 0
        number = 1
        for i in range(p):
            size = base + (1 if i < extra else 0)
            rnd = Round(number=number, type="preround", status="pending",
                        skill_levels=s["skills_preround"])
            db.add(rnd)
            db.flush()
            for slot in range(size):
                db.add(RoundPlayer(round_id=rnd.id, player_id=ids[idx],
                                   slot=slot + 1, score=0))
                idx += 1
            number += 1

        _state.update({
            "phase": "idle", "round_id": None, "question_id": None,
            "question_source": None,
            "pending_question_id": None, "pending_source": None,
            "buzzed_slot": None, "picked_answer": None,
            "correct_answer": None, "blocked_slots": [],
            "game_started": True,
            "jokers": {str(pid): {"fifty": False, "double": False,
                                 "audience": False} for pid in ids},
            "fifty_hidden": [], "double_active": False,
            "audience_voting": False, "audience_votes": {},
            "audience_result": None, "audience_pick": None,
            "locked_answer": None, "tiebreak_slots": None,
        })
        db.commit()
        _persist_state(db)
        # rounds stay pending – the moderator starts the quiz explicitly


def _ranked_players(db: Session, round_id: int):
    """RoundPlayers of a round sorted by score desc, then slot asc."""
    return (
        db.query(RoundPlayer)
        .filter(RoundPlayer.round_id == round_id)
        .order_by(RoundPlayer.score.desc(), RoundPlayer.slot.asc())
        .all()
    )


def _stage_rounds(db: Session, stage: str):
    return (
        db.query(Round)
        .filter(Round.type == stage)
        .order_by(Round.number)
        .all()
    )


def _next_number(db: Session) -> int:
    last = db.query(Round).order_by(Round.number.desc()).first()
    return (last.number + 1) if last else 1


def _create_round(db, rtype, players, number=None):
    s = get_all_settings(db)
    skill_key = {
        "semifinal": "skills_semifinal",
        "final": "skills_final",
        "playoff_presemi": "skills_playoff",
        "playoff_prefinal": "skills_playoff",
        "preround": "skills_preround",
    }.get(rtype, "skills_preround")
    rnd = Round(number=number or _next_number(db), type=rtype, status="pending",
                skill_levels=s.get(skill_key, [1, 2, 3, 4, 5]))
    db.add(rnd)
    db.flush()
    for i, rp in enumerate(players):
        db.add(RoundPlayer(round_id=rnd.id, player_id=rp.player_id,
                           slot=i + 1, score=0))
    return rnd


def finish_round(db: Session, round_id: int):
    """Rank players, mark qualifiers, create playoff/next-stage rounds."""
    with _lock:
        rnd = db.get(Round, round_id)
        if not rnd or rnd.status == "finished":
            return
        ranked = _ranked_players(db, round_id)
        for i, rp in enumerate(ranked):
            rp.place = i + 1
        rnd.status = "finished"
        db.flush()

        _state.update({
            "phase": "idle", "question_id": None, "question_source": None,
            "pending_question_id": None, "pending_source": None,
            "buzzed_slot": None,
            "picked_answer": None, "correct_answer": None,
            "blocked_slots": [], "round_id": None,
            "fifty_hidden": [], "double_active": False,
            "audience_voting": False, "audience_votes": {},
            "audience_result": None, "audience_pick": None,
            "locked_answer": None, "tiebreak_slots": None,
        })

        if rnd.type == "final":
            db.commit()
            _persist_state(db)
            return

        _advance_stage(db, rnd.type)
        db.commit()
        _persist_state(db)


def _advance_stage(db: Session, finished_stage: str):
    s = get_all_settings(db)
    n = s["players_per_round"]

    if finished_stage == "preround":
        prerounds = _stage_rounds(db, "preround")
        if any(r.status != "finished" for r in prerounds):
            return
        spots = 2 * n
        a = min(2, spots // len(prerounds)) if prerounds else 2
        _fill_stage(db, prerounds, a, spots, "semifinal", 2, "playoff_presemi")

    elif finished_stage == "playoff_presemi":
        playoff = (
            db.query(Round)
            .filter(Round.type == "playoff_presemi", Round.status == "finished")
            .order_by(Round.number.desc())
            .first()
        )
        advancers = _ranked_players(db, playoff.id)[: _read_playoff_advances(db, playoff.id)]
        for rp in advancers:
            rp.qualified = "playoff"
        _create_next_rounds(db, "semifinal", 2)

    elif finished_stage == "semifinal":
        semis = _stage_rounds(db, "semifinal")
        if len(semis) < 2 or any(r.status != "finished" for r in semis):
            return
        spots = n
        a = min(2, spots // len(semis))
        _fill_stage(db, semis, a, spots, "final", 1, "playoff_prefinal")

    elif finished_stage == "playoff_prefinal":
        playoff = (
            db.query(Round)
            .filter(Round.type == "playoff_prefinal", Round.status == "finished")
            .order_by(Round.number.desc())
            .first()
        )
        advancers = _ranked_players(db, playoff.id)[: _read_playoff_advances(db, playoff.id)]
        for rp in advancers:
            rp.qualified = "playoff"
        _create_next_rounds(db, "final", 1)


def _fill_stage(db, source_rounds, direct_per_round, spots, next_type,
                num_next, playoff_type):
    qualifiers = []
    pool = []
    for r in source_rounds:
        ranked = _ranked_players(db, r.id)
        qualifiers.extend(ranked[:direct_per_round])
        pool.extend(ranked[direct_per_round:])

    for rp in qualifiers:
        rp.qualified = "direct"

    remaining = spots - len(qualifiers)
    if remaining <= 0:
        _mark_out_except(db, source_rounds, {rp.id for rp in qualifiers})
        _create_next_rounds(db, next_type, num_next)
        return

    pool.sort(key=lambda rp: (rp.place or 99, -rp.score))
    n = get_setting(db, "players_per_round", 4)
    participants = pool[: max(min(n, len(pool)), remaining)]
    if len(participants) <= remaining:
        for rp in participants:
            rp.qualified = "playoff"
        _mark_out_except(
            db, source_rounds,
            {rp.id for rp in qualifiers} | {rp.id for rp in participants},
        )
        _create_next_rounds(db, next_type, num_next)
        return

    playoff = _create_round(db, playoff_type, participants)
    _store_playoff_advances(db, playoff.id, remaining)
    _mark_out_except(
        db, source_rounds,
        {rp.id for rp in qualifiers} | {rp.id for rp in participants},
    )


def _mark_out_except(db, source_rounds, keep_ids):
    keep_ids = set(keep_ids)
    for r in source_rounds:
        for rp in db.query(RoundPlayer).filter(RoundPlayer.round_id == r.id).all():
            if rp.id not in keep_ids:
                rp.qualified = "out"


def _store_playoff_advances(db, playoff_id, count):
    row = db.query(ConfigKV).filter(
        ConfigKV.name == f"playoff_advances:{playoff_id}").first()
    if row is None:
        row = ConfigKV(name=f"playoff_advances:{playoff_id}")
        db.add(row)
    row.value = str(count)


def _read_playoff_advances(db, playoff_id) -> int:
    row = db.query(ConfigKV).filter(
        ConfigKV.name == f"playoff_advances:{playoff_id}").first()
    return int(row.value) if row and row.value else 0


def _create_next_rounds(db, rtype, num_rounds):
    """Create next-stage rounds from all rows marked qualified='direct'/'playoff'.
    Those rows get marked 'placed'."""
    db.flush()  # autoflush is off – make qualified marks visible
    pending = (
        db.query(RoundPlayer)
        .filter(RoundPlayer.qualified.in_(["direct", "playoff"]))
        .all()
    )
    players = list(pending)
    random.shuffle(players)
    for rp in players:
        rp.qualified = "placed"
    base, extra = divmod(len(players), num_rounds)
    idx = 0
    for i in range(num_rounds):
        size = base + (1 if i < extra else 0)
        _create_round(db, rtype, players[idx: idx + size])
        idx += size
    if rtype == "final":
        # finalists get a fresh set of jokers
        for rp in players:
            _state["jokers"][str(rp.player_id)] = {
                "fifty": False, "double": False, "audience": False}


# ---------------------------------------------------------------------------
# Question flow
# ---------------------------------------------------------------------------

def start_round(db: Session, round_id: int):
    with _lock:
        rnd = db.get(Round, round_id)
        if not rnd:
            return None
        rnd.status = "active"
        _state.update({"round_id": round_id, "phase": "idle",
                       "question_id": None, "question_source": None,
                       "pending_question_id": None, "pending_source": None,
                       "buzzed_slot": None,
                       "picked_answer": None, "correct_answer": None,
                       "blocked_slots": [],
                       "fifty_hidden": [], "double_active": False,
                       "audience_voting": False, "audience_votes": {},
                       "audience_result": None, "audience_pick": None,
                       "locked_answer": None, "tiebreak_slots": None})
        db.commit()
        _ensure_pending_question(db)
        _persist_state(db)
        return rnd


def _model_for(source: str):
    return CustomQuestion if source == "c" else Question


def get_question(db: Session):
    """The currently shown question object (source-aware), or None."""
    if not _state["question_id"]:
        return None
    return db.get(_model_for(_state.get("question_source")), _state["question_id"])


def pick_question(db: Session):
    """Choose a random unused question from the current round's pool.
    Returns (question_obj, source) with source 'q'|'c', or (None, None)."""
    round_id = _state["round_id"]
    rnd = db.get(Round, round_id) if round_id else None
    skills = rnd.skill_levels if rnd and rnd.skill_levels else [1, 2, 3, 4, 5]
    pool = (rnd.question_pool if rnd else "standard") or "standard"

    if pool.startswith("cat:"):
        try:
            cat_id = int(pool[4:])
        except ValueError:
            cat_id = -1
        qs = (
            db.query(CustomQuestion)
            .filter(CustomQuestion.used == False,
                    CustomQuestion.reported == False,
                    CustomQuestion.skill.in_(skills),
                    CustomQuestion.category_id == cat_id)
            .all()
        )
        if not qs:
            # category exhausted -> any unused custom question
            qs = db.query(CustomQuestion).filter(
                CustomQuestion.used == False,
                CustomQuestion.reported == False).all()
        if not qs:
            # last resort: standard pool
            qs = db.query(Question).filter(
                Question.used == False, Question.reported == False).all()
            return (random.choice(qs), "q") if qs else (None, None)
        return random.choice(qs), "c"

    qs = (
        db.query(Question)
        .filter(Question.used == False, Question.reported == False,
                Question.skill.in_(skills))
        .all()
    )
    if not qs:
        qs = db.query(Question).filter(
            Question.used == False, Question.reported == False).all()
    if not qs:
        return None, None
    return random.choice(qs), "q"


def _ensure_pending_question(db: Session):
    """Pre-pick the 'next question' so the moderator sees a preview."""
    if _state["pending_question_id"] is None:
        q, src = pick_question(db)
        _state["pending_question_id"] = q.id if q else None
        _state["pending_source"] = src


def questions_asked(db: Session, round_id: int) -> int:
    """Questions already shown in this round (standard + custom pools)."""
    n = db.query(Question).filter(Question.used_round_id == round_id).count()
    n += db.query(CustomQuestion).filter(
        CustomQuestion.used_round_id == round_id).count()
    return n


def max_questions(db: Session) -> int | None:
    """Configured per-round question cap, or None when disabled."""
    if get_setting(db, "max_questions_enabled"):
        return get_setting(db, "max_questions") or 20
    return None


def _advance_count(db: Session, rnd: Round) -> int:
    """How many players advance directly from this round (0 = final)."""
    s = get_all_settings(db)
    n = s["players_per_round"]
    if rnd.type == "preround":
        prs = _stage_rounds(db, "preround")
        return min(2, (2 * n) // len(prs)) if prs else 2
    if rnd.type == "semifinal":
        semis = _stage_rounds(db, "semifinal")
        return min(2, n // len(semis)) if semis else 2
    if rnd.type in ("playoff_presemi", "playoff_prefinal"):
        return _read_playoff_advances(db, rnd.id)
    return 0


def tiebreak_players(db: Session, rnd: Round) -> list:
    """RoundPlayers tied exactly at the advancement boundary.
    In the final the boundary is first place (tie for the win).
    Empty = round outcome is decided; non-empty = 'Stichfrage' needed."""
    a = _advance_count(db, rnd)
    if rnd.type == "final":
        a = 1  # nothing to qualify for, but a tie for the win needs a Stichfrage
    if not a:
        return []
    ranked = _ranked_players(db, rnd.id)
    if len(ranked) <= a or ranked[a - 1].score != ranked[a].score:
        return []
    boundary = ranked[a - 1].score
    return [rp for rp in ranked if rp.score == boundary]


def show_question(db: Session):
    """Moderator: show next question on the board and arm buzzers."""
    with _lock:
        rnd = db.get(Round, _state["round_id"]) if _state["round_id"] else None
        cap = max_questions(db)
        _state["tiebreak_slots"] = None
        if rnd and cap and questions_asked(db, rnd.id) >= cap:
            tied = tiebreak_players(db, rnd)
            if not tied:
                return None, (f"Fragen-Limit erreicht ({cap}) – "
                              "bitte die Runde beenden.")
            # Stichfrage: keep playing, but only the tied players may buzz
            _state["tiebreak_slots"] = [rp.slot for rp in tied]
        q, src = None, None
        if _state["pending_question_id"]:
            src = _state.get("pending_source") or "q"
            q = db.get(_model_for(src), _state["pending_question_id"])
            if q and q.used:
                q, src = None, None
        if q is None:
            q, src = pick_question(db)
        if q is None:
            return None, "Keine Fragen mehr im Pool für diese Runde."
        q.used = True
        q.used_round_id = _state["round_id"]
        _state.update({
            "phase": "question",
            "question_id": q.id,
            "question_source": src,
            "pending_question_id": None,
            "pending_source": None,
            "buzzed_slot": None,
            "picked_answer": None,
            "correct_answer": None,
            "fifty_hidden": [], "double_active": False,
            "audience_voting": False, "audience_votes": {},
            "audience_result": None, "audience_pick": None,
            "locked_answer": None,
        })
        db.commit()
        _ensure_pending_question(db)  # preview the next one
        db.commit()
        _persist_state(db)
        return q, None


def skip_question(db: Session):
    """Discard current question without scoring; stay/return to idle.
    The pending preview question moves up as the next one."""
    with _lock:
        _state.update({
            "phase": "idle", "question_id": None, "question_source": None,
            "buzzed_slot": None, "picked_answer": None, "correct_answer": None,
            "fifty_hidden": [], "double_active": False,
            "audience_voting": False, "audience_votes": {},
            "audience_result": None, "audience_pick": None,
            "locked_answer": None,
        })
        _ensure_pending_question(db)
        _persist_state(db)


def report_question(db: Session, which: str = "current"):
    """Moderator flags a question as broken -> excluded from all pools.
    'current': the shown question is reported and hidden (no scoring).
    'next': the previewed pending question is reported and re-picked."""
    with _lock:
        if which == "next":
            qid = _state["pending_question_id"]
            if not qid:
                return False, "Keine Vorschau-Frage"
            q = db.get(_model_for(_state.get("pending_source") or "q"), qid)
            if q:
                q.reported = True
            _state["pending_question_id"] = None
            _state["pending_source"] = None
            db.commit()
            _ensure_pending_question(db)
            db.commit()
            _persist_state(db)
            return True, None
        if not _state["question_id"]:
            return False, "Keine aktive Frage"
        q = db.get(_model_for(_state.get("question_source") or "q"),
                   _state["question_id"])
        if q:
            q.reported = True
        _state.update({
            "phase": "idle", "question_id": None, "question_source": None,
            "buzzed_slot": None, "picked_answer": None, "correct_answer": None,
            "fifty_hidden": [], "double_active": False,
            "audience_voting": False, "audience_votes": {},
            "audience_result": None, "audience_pick": None,
            "locked_answer": None,
        })
        db.commit()
        _persist_state(db)
        return True, None


def use_joker(db: Session, kind: str):
    """Buzzed player uses a joker. kind: fifty|double|audience.
    Returns (ok, error_message)."""
    with _lock:
        if _state["phase"] != "buzzed":
            return False, "Joker nur nach Buzzern möglich"
        s = get_all_settings(db)
        if not s.get(f"joker_{kind}", False):
            return False, "Dieser Joker ist deaktiviert"
        rps = _active_round_players(db)
        rp = next((r for r in rps if r.slot == _state["buzzed_slot"]), None)
        if not rp:
            return False, "Kein gebuzzerter Spieler"
        jk = _state["jokers"].setdefault(
            str(rp.player_id), {"fifty": False, "double": False, "audience": False})
        if jk.get(kind):
            return False, "Joker bereits verbraucht"
        jk[kind] = True
        if kind == "fifty":
            q = get_question(db)
            if q is None:
                return False, "Keine aktive Frage"
            wrong = [i for i in range(1, 5) if i != q.correct]
            _state["fifty_hidden"] = random.sample(wrong, 2)
        elif kind == "double":
            _state["double_active"] = True
        elif kind == "audience":
            _state["audience_voting"] = True
            _state["audience_votes"] = {}
            _state["audience_result"] = None
            _state["audience_pick"] = None
        _persist_state(db)
        return True, None


def audience_vote(db: Session, voter: str, answer_idx: int):
    """Audience member votes on /audience while voting is open.
    Recasting is allowed – last vote counts."""
    with _lock:
        if not _state["audience_voting"]:
            return False
        if not voter or not 1 <= answer_idx <= 4:
            return False
        _state["audience_votes"][str(voter)] = answer_idx
        _persist_state(db)
        return True


def stop_audience_voting(db: Session):
    """Moderator ends the vote -> percentages go on the board."""
    with _lock:
        if not _state["audience_voting"]:
            return False
        votes = _state["audience_votes"]
        total = len(votes)
        counts = [sum(1 for v in votes.values() if v == i) for i in range(1, 5)]
        _state["audience_result"] = (
            [round(100 * c / total) for c in counts] if total else [0, 0, 0, 0])
        _state["audience_pick"] = (
            counts.index(max(counts)) + 1 if total else None)
        _state["audience_voting"] = False
        _persist_state(db)
        return True


def _active_round_players(db):
    rid = _state["round_id"]
    if not rid:
        return []
    return _ranked_players(db, rid)


def buzzer_pressed(db: Session, buzzer_num: int):
    """buzzer_num is 1-based slot (as sent by Arduino). Returns (slot, ok)."""
    with _lock:
        if _state["phase"] != "question":
            return None, False
        tb = _state.get("tiebreak_slots")
        if tb and buzzer_num not in tb:
            return buzzer_num, False
        if buzzer_num in _state["blocked_slots"]:
            return buzzer_num, False
        players = _active_round_players(db)
        slots = {rp.slot for rp in players}
        if buzzer_num not in slots:
            return buzzer_num, False
        _state["phase"] = "buzzed"
        _state["buzzed_slot"] = buzzer_num
        _persist_state(db)
        return buzzer_num, True


def pick_answer(db: Session, answer_idx: int):
    """Moderator clicks the answer the buzzed player chose.
    First click locks it in ("eingeloggt"), second click on the same
    answer judges it. Other answers are ignored while one is locked.
    Returns (outcome, question) with outcome in {"locked", True, False, None}."""
    with _lock:
        if _state["phase"] != "buzzed":
            return None, None
        q = get_question(db)
        if not q:
            return None, None
        locked = _state["locked_answer"]
        s = get_all_settings(db)
        audience_used = (_state["audience_voting"]
                         or _state["audience_result"] is not None
                         or _state["audience_pick"] is not None)
        if locked is None:
            if s["answer_lockin"] or audience_used:
                _state["locked_answer"] = answer_idx
                _persist_state(db)
                return "locked", q
        elif answer_idx != locked:
            return "locked", q
        _state["locked_answer"] = None
        slot = _state["buzzed_slot"]
        correct = (answer_idx == q.correct)

        mult = 2 if _state["double_active"] else 1
        tie = _state.get("tiebreak_slots")
        rps = _active_round_players(db)
        for rp in rps:
            if tie and rp.slot not in tie:
                continue  # Stichfrage: only tied players score
            if rp.slot == slot:
                rp.score += s["points_correct"] * mult if correct else s["points_wrong_self"]
            elif not correct:
                rp.score += s["points_wrong_others"]

        if not correct and s["block_on_wrong"]:
            _state["blocked_slots"] = [slot]
        else:
            _state["blocked_slots"] = []

        _state.update({
            "phase": "resolved",
            "picked_answer": answer_idx,
            "correct_answer": q.correct,
        })
        db.commit()
        _persist_state(db)
        return correct, q


def unshow_question(db: Session):
    """Hide the current question, keep scores (back to idle)."""
    with _lock:
        _state.update({
            "phase": "idle", "question_source": None, "buzzed_slot": None,
            "picked_answer": None, "correct_answer": None,
            "fifty_hidden": [], "double_active": False,
            "audience_voting": False, "audience_votes": {},
            "audience_result": None, "audience_pick": None,
            "locked_answer": None,
        })
        _persist_state(db)


# ---------------------------------------------------------------------------
# Serial command routing
# ---------------------------------------------------------------------------

_serial_socket = None  # websocket that owns the serial port (bridge or admin)


def set_serial_socket(ws):
    global _serial_socket
    _serial_socket = ws


def get_serial_socket():
    return _serial_socket


def serial_commands_for_phase():
    """Arduino commands for current phase change.

    Blocked buzzers get their 0-based slot digit sent on every relevant phase
    (like musikquiz does) so the strip keeps idling red, not only when a
    question is armed."""
    phase = _state["phase"]
    blocked = _state["blocked_slots"][0] if _state["blocked_slots"] else None
    block_cmd = str(blocked - 1) if blocked else None
    # Stichfrage: 'B <mask>' fades all non-participating buzzers out (~3 s)
    # (new firmware command – harmless no-op on the musikquiz sketch)
    off_cmd = None
    tb = _state.get("tiebreak_slots")
    if tb:
        off_mask = sum(1 << (s - 1) for s in range(1, 6) if s not in tb)
        off_cmd = f"B {off_mask}"
    if phase == "question":
        cmds = ["5"]  # reset
        cmds.append(block_cmd or "9")  # 0-based block | all active
        if off_cmd:
            cmds.append(off_cmd)
        return cmds
    if phase == "resolved":
        picked, correct = _state["picked_answer"], _state["correct_answer"]
        cmds = ["G" if picked == correct else "R"]
        if block_cmd:
            cmds.append(block_cmd)
        return cmds
    if phase == "idle":
        cmds = ["5"]
        if block_cmd:
            cmds.append(block_cmd)
        if off_cmd:
            cmds.append(off_cmd)
        return cmds
    return []


def board_state(db: Session) -> dict:
    """Public state snapshot for scoreboard clients."""
    round_id = _state["round_id"]
    rnd = db.get(Round, round_id) if round_id else None
    players = []
    if rnd:
        rps = (
            db.query(RoundPlayer)
            .filter(RoundPlayer.round_id == rnd.id)
            .order_by(RoundPlayer.slot)
            .all()
        )
        players = [{
            "slot": rp.slot,
            "player_id": rp.player_id,
            "name": db.get(Player, rp.player_id).name if db.get(Player, rp.player_id) else "?",
            "score": rp.score,
            "place": rp.place,
            "qualified": rp.qualified,
            "blocked": rp.slot in _state["blocked_slots"],
            "buzzed": rp.slot == _state["buzzed_slot"],
            "jokers": _state["jokers"].get(
                str(rp.player_id),
                {"fifty": False, "double": False, "audience": False}),
        } for rp in rps]
    question = None
    if _state["question_id"] and _state["phase"] != "idle":
        q = get_question(db)
        if q:
            question = q.to_dict(reveal=_state["phase"] == "resolved")
            if _state.get("question_source") == "c":
                cat = db.get(Category, q.category_id)
                question["category"] = cat.name if cat else ""
            if _state["phase"] == "resolved":
                question["picked"] = _state["picked_answer"]
    pool_label = ""
    if rnd:
        pool = rnd.question_pool or "standard"
        if pool.startswith("cat:"):
            cat = db.get(Category, int(pool[4:])) if pool[4:].isdigit() else None
            pool_label = f"Kategorie: {cat.name}" if cat else "Kategorie"
        else:
            pool_label = "Standard-Pool"
    quiz_waiting = (
        _state["game_started"] and rnd is None and
        not db.query(Round).filter(Round.status != "pending").count()
    )
    return {
        "phase": _state["phase"],
        "quiz_waiting": quiz_waiting,
        "round": {
            "id": rnd.id, "number": rnd.number,
            "type": rnd.type, "type_label": ROUND_TYPE_LABELS.get(rnd.type, rnd.type),
            "status": rnd.status,
            "question_pool": rnd.question_pool or "standard",
            "pool_label": pool_label,
            "questions_asked": questions_asked(db, rnd.id),
            "questions_max": max_questions(db),
            "tiebreak": bool(_state.get("tiebreak_slots")),
            "tiebreak_slots": _state.get("tiebreak_slots") or [],
        } if rnd else None,
        "players": players,
        "question": question,
        "buzzed_slot": _state["buzzed_slot"],
        "game_started": _state["game_started"],
        "fifty_hidden": _state["fifty_hidden"],
        "double_active": _state["double_active"],
        "audience_voting": _state["audience_voting"],
        "audience_votes": len(_state["audience_votes"]),
        "audience_result": _state["audience_result"],
        "audience_pick": _state["audience_pick"],
        "locked_answer": _state["locked_answer"],
        "jokers_enabled": [
            k for k in ("fifty", "double", "audience")
            if get_setting(db, f"joker_{k}")],
        "sound_target": get_setting(db, "sound_target", "board"),
        "seat_colors": get_setting(db, "seat_colors"),
    }


def admin_state(db: Session) -> dict:
    """Extended state for moderator panel."""
    st = board_state(db)
    # let admin see the current question's correct answer
    if st["question"]:
        q = get_question(db)
        st["question"]["correct"] = q.correct if q else None
    # next-question preview for the moderator
    st["next_question"] = None
    if _state["pending_question_id"]:
        pq = db.get(_model_for(_state.get("pending_source")),
                    _state["pending_question_id"])
        if pq:
            st["next_question"] = pq.to_dict(reveal=True)
    rounds = db.query(Round).order_by(Round.number).all()
    st["rounds"] = [{
        "id": r.id, "number": r.number, "type": r.type,
        "type_label": ROUND_TYPE_LABELS.get(r.type, r.type),
        "status": r.status,
        "skill_levels": r.skill_levels,
        "question_pool": r.question_pool or "standard",
        "players": [{
            "slot": rp.slot, "name": db.get(Player, rp.player_id).name,
            "score": rp.score, "place": rp.place, "qualified": rp.qualified,
        } for rp in db.query(RoundPlayer).filter(
            RoundPlayer.round_id == r.id).order_by(RoundPlayer.slot).all()],
    } for r in rounds]
    # previous (last used) questions from both pools
    last_q = (db.query(Question)
              .filter(Question.used == True)
              .order_by(Question.used_round_id.desc(), Question.id.desc())
              .limit(5).all())
    last_c = (db.query(CustomQuestion)
              .filter(CustomQuestion.used == True)
              .order_by(CustomQuestion.used_round_id.desc(),
                        CustomQuestion.id.desc())
              .limit(5).all())
    merged = sorted(
        last_q + list(last_c),
        key=lambda q: (q.used_round_id or 0, q.id), reverse=True,
    )[:5]
    prev = []
    for q in merged:
        d = q.to_dict(reveal=True)
        if isinstance(q, CustomQuestion):
            cat = db.get(Category, q.category_id)
            d["category"] = cat.name if cat else ""
        prev.append(d)
    st["previous_questions"] = prev
    return st
