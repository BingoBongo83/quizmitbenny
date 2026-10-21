"""Game engine: bracket computation, state machine, scoring.

Stages: preround -> playoff_presemi -> semifinal -> playoff_prefinal -> final
A game may start at preround, semifinal or final, depending on the plan
chosen for the player count (see compute_plans).

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
import time

from sqlalchemy.orm import Session

from .models import (
    AnswerLog,
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
    "manual_blocked": [],   # slots blocked by the moderator (cleared per round)
    "deactivated_slots": [],  # slots powered off by the moderator (persistent)
    "question_shown_at": None,  # unix ts when current question went on the board
    "game_seq": 0,  # increments per create_game – tags AnswerLog entries
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


def _log_answer(db: Session, q, src: str, outcome: str,
                rp: RoundPlayer | None = None, points: int = 0):
    """Persist one answer event for statistics (survives game resets)."""
    rnd = db.get(Round, _state["round_id"]) if _state["round_id"] else None
    cat = ""
    if q is not None:
        if src == "c":
            c = db.get(Category, q.category_id)
            cat = c.name if c else ""
        else:
            cat = q.category or ""
    pname = ""
    if rp is not None:
        p = db.get(Player, rp.player_id)
        pname = p.name if p else ""
    db.add(AnswerLog(
        game_seq=_state.get("game_seq") or 0,
        round_number=rnd.number if rnd else 0,
        round_type=rnd.type if rnd else "",
        question_source=src,
        question_id=q.id if q else None,
        question_text=(q.text or "")[:500] if q else "",
        category=cat,
        player_id=rp.player_id if rp else None,
        player_name=pname,
        slot=rp.slot if rp else None,
        outcome=outcome,
        points=points,
        created_at=int(time.time()),
    ))


# ---------------------------------------------------------------------------
# Bracket
# ---------------------------------------------------------------------------

def _even_split(total: int, parts: int) -> list[int]:
    """Sizes as equal as possible, extras go to the earlier rounds."""
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def compute_plans(num_players: int, players_per_round: int, max_prerounds: int):
    """All playable bracket plans for `num_players`, best first.

    Plan codes: 'final' | 'semifinal' | 'preround:<k>'.
    Rules: every stage must eliminate at least one player, round sizes stay
    within players_per_round (n), and prerounds average >= 3 players.
    - m <= n      -> final only
    - m <= 2n     -> 2 semifinals -> final
    - m > 2n      -> k prerounds -> 2 semifinals -> final,
                     k in [ceil(m/n), min(max_prerounds, m//3)]
    """
    n = players_per_round
    m = num_players
    plans = []
    if m >= 2 and n >= 2:
        if m <= n:
            plans.append({
                "code": "final",
                "label": "Nur Finale",
                "stages": [{"type": "final", "label": "Finale",
                            "sizes": [m]}],
                "playoffs": [], "warnings": [],
            })
        elif m <= 2 * n:
            sizes = _even_split(m, 2)
            a2 = min(2, n // 2)
            warnings = ([
                "Halbfinale mit sehr wenigen Spielern – dort kommen alle direkt weiter"
            ] if any(sz <= a2 for sz in sizes) else [])
            playoffs = []
            if 2 * a2 < n:
                playoffs.append(
                    f"Playoff vor Finale: {n - 2 * a2} Finalplatz/Plätze "
                    "per Playoff")
            plans.append({
                "code": "semifinal",
                "label": "2 Halbfinale + Finale",
                "stages": [{"type": "semifinal", "label": "Halbfinale",
                            "sizes": sizes},
                           {"type": "final", "label": "Finale",
                            "sizes": [n]}],
                "playoffs": playoffs, "warnings": warnings,
            })
        else:
            lo = -(-m // n)  # smallest k so rounds stay <= n players
            hi = min(max_prerounds, m // 3)  # avg >= 3 per preround
            spots_semi = 2 * n
            a2 = min(2, n // 2)
            for k in range(lo, hi + 1):
                sizes = _even_split(m, k)
                playoffs, warnings = [], []
                dpr = min(2, spots_semi // k)
                direct = dpr * k
                if direct < spots_semi:
                    playoffs.append(
                        f"Playoff vor Halbfinale: beste "
                        f"{min(n, m - direct)} verbleibenden Spieler um "
                        f"{spots_semi - direct} Plätze")
                elif direct > spots_semi:
                    warnings.append(
                        f"{direct} Direktqualifikanten für {spots_semi} "
                        "Halbfinal-Plätze – Playoff der Zweitplatzierten")
                if 2 * a2 < n:
                    playoffs.append(
                        f"Playoff vor Finale: {n - 2 * a2} "
                        "Finalplatz/Plätze per Playoff")
                plans.append({
                    "code": f"preround:{k}",
                    "label": f"{k} Vorrunden + Halbfinale + Finale",
                    "stages": [
                        {"type": "preround", "label": "Vorrunde",
                         "sizes": sizes},
                        {"type": "semifinal", "label": "Halbfinale",
                         "sizes": [n, n]},
                        {"type": "final", "label": "Finale",
                         "sizes": [n]},
                    ],
                    "playoffs": playoffs, "warnings": warnings,
                })
    if plans:
        plans[0]["recommended"] = True
    return plans


def create_game(db: Session, player_ids: list[int], plan: str | None = None,
                shuffle: bool = True):
    """Create the first stage's rounds and assign players.

    plan: 'final' | 'semifinal' | 'preround:<k>' | None (= recommended plan
    for the player count). Raises ValueError when no plan fits.
    """
    with _lock:
        db.query(RoundPlayer).delete()
        db.query(Round).delete()
        db.query(Question).update({Question.used: False, Question.used_round_id: None})
        db.query(CustomQuestion).update(
            {CustomQuestion.used: False, CustomQuestion.used_round_id: None}
        )
        s = get_all_settings(db)
        n = s["players_per_round"]

        ids = list(player_ids)
        if shuffle:
            random.shuffle(ids)

        plans = compute_plans(len(ids), n, s["num_prerounds"])
        if not plans:
            raise ValueError(
                f"Kein Spielplan für {len(ids)} Spieler möglich "
                f"(max. {n * s['num_prerounds']})")
        chosen = next((p for p in plans if p["code"] == plan), None)
        if chosen is None:
            if plan is not None:
                raise ValueError(
                    f"Spielplan '{plan}' passt nicht zur Spielerzahl")
            chosen = plans[0]

        first = chosen["stages"][0]
        idx = 0
        for number, size in enumerate(first["sizes"], start=1):
            _add_round(db, first["type"], number, ids[idx: idx + size], s)
            idx += size

        _state.update({
            "phase": "idle", "round_id": None, "question_id": None,
            "question_source": None,
            "pending_question_id": None, "pending_source": None,
            "buzzed_slot": None, "picked_answer": None,
            "correct_answer": None, "blocked_slots": [],
            "manual_blocked": [],
            "game_started": True,
            "jokers": {str(pid): {"fifty": False, "double": False,
                                 "audience": False} for pid in ids},
            "fifty_hidden": [], "double_active": False,
            "audience_voting": False, "audience_votes": {},
            "audience_result": None, "audience_pick": None,
            "locked_answer": None, "tiebreak_slots": None,
            "game_seq": (_state.get("game_seq") or 0) + 1,
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


_ROUND_SKILL_KEYS = {
    "semifinal": "skills_semifinal",
    "final": "skills_final",
    "playoff_presemi": "skills_playoff",
    "playoff_prefinal": "skills_playoff",
    "preround": "skills_preround",
}


def _add_round(db, rtype, number, player_ids, settings):
    """Create a round from raw player ids (game setup)."""
    rnd = Round(number=number, type=rtype, status="pending",
                skill_levels=settings.get(
                    _ROUND_SKILL_KEYS.get(rtype, "skills_preround"),
                    [1, 2, 3, 4, 5]),
                question_pool=settings.get("default_pool") or "standard")
    db.add(rnd)
    db.flush()
    for slot, pid in enumerate(player_ids, start=1):
        db.add(RoundPlayer(round_id=rnd.id, player_id=pid,
                           slot=slot, score=0))
    return rnd


def _create_round(db, rtype, players, number=None):
    """Create a round from RoundPlayer rows (stage advancement)."""
    s = get_all_settings(db)
    rnd = Round(number=number or _next_number(db), type=rtype, status="pending",
                skill_levels=s.get(
                    _ROUND_SKILL_KEYS.get(rtype, "skills_preround"),
                    [1, 2, 3, 4, 5]),
                question_pool=s.get("default_pool") or "standard")
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
            "manual_blocked": [],
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


def _seeded_buckets(players, num_rounds):
    """Split qualifying players over num_rounds so players from the same
    source round land in different target rounds whenever possible
    (seeding, not a flat shuffle). Buckets stay size-balanced."""
    groups = {}
    for rp in players:
        groups.setdefault(rp.round_id, []).append(rp)
    gs = list(groups.values())
    random.shuffle(gs)
    gs.sort(key=len, reverse=True)  # big groups first – hardest to split
    buckets = [[] for _ in range(num_rounds)]
    for g in gs:
        g.sort(key=lambda rp: (rp.place or 99, -rp.score))  # rank order
        for rp in g:
            free = [i for i in range(num_rounds)
                    if all(x.round_id != rp.round_id for x in buckets[i])]
            cands = free if free else range(num_rounds)
            i = min(cands, key=lambda i: len(buckets[i]))
            buckets[i].append(rp)
    return buckets


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
    for rp in players:
        rp.qualified = "placed"
    for bucket in _seeded_buckets(players, num_rounds):
        random.shuffle(bucket)  # seeding decides WHO, not the buzzer seat
        _create_round(db, rtype, bucket)
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
                       "blocked_slots": [], "manual_blocked": [],
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


def max_questions(db: Session, rnd: Round | None = None) -> int | None:
    """Question cap for this round's stage, or None when the feature is off.
    Per-stage setting falls back to the global max_questions value."""
    if not get_setting(db, "max_questions_enabled"):
        return None
    base = get_setting(db, "max_questions") or 20
    if rnd is None:
        return base
    stage = "playoff" if rnd.type.startswith("playoff") else rnd.type
    return get_setting(db, f"max_questions_{stage}") or base


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
    """RoundPlayers tied at a boundary that matters: first place
    (round winner – in the final: the win itself) or the advancement
    cut. Empty = outcome decided; non-empty = 'Stichfrage' needed."""
    ranked = _ranked_players(db, rnd.id)
    if len(ranked) < 2:
        return []
    if ranked[0].score == ranked[1].score:
        return [rp for rp in ranked if rp.score == ranked[0].score]
    a = _advance_count(db, rnd)
    if a and len(ranked) > a and ranked[a - 1].score == ranked[a].score:
        boundary = ranked[a - 1].score
        return [rp for rp in ranked if rp.score == boundary]
    return []


def eval_tiebreak(db: Session) -> None:
    """Eagerly detect a Stichfrage once the question cap is reached, so the
    UI/buzzers know before 'Frage zeigen' is pressed. Runs while no question
    is being played (idle/resolved); scoring during buzzed is untouched."""
    with _lock:
        if _state["phase"] not in ("idle", "resolved"):
            return
        rnd = db.get(Round, _state["round_id"]) if _state["round_id"] else None
        if not rnd or rnd.status != "active":
            _state["tiebreak_slots"] = None
            return
        cap = max_questions(db, rnd)
        tied = (cap and questions_asked(db, rnd.id) >= cap
                and tiebreak_players(db, rnd)) or []
        new = [rp.slot for rp in tied] or None
        if new != _state.get("tiebreak_slots"):
            _state["tiebreak_slots"] = new
            _persist_state(db)


def show_question(db: Session):
    """Moderator: show next question on the board and arm buzzers."""
    with _lock:
        rnd = db.get(Round, _state["round_id"]) if _state["round_id"] else None
        cap = max_questions(db, rnd)
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
            "question_shown_at": time.time(),
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
        q = get_question(db)
        if q:
            _log_answer(db, q, _state.get("question_source") or "q",
                        "skipped")
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


def report_question(db: Session, which: str = "current", qid: int | None = None):
    """Moderator flags a question as broken -> excluded from all pools.
    'current': the shown question is reported and hidden (no scoring).
    'next': the previewed pending question is reported and re-picked.
    qid pins the report to the question the moderator actually saw –
    if it no longer matches (state moved on in the meantime) we refuse
    instead of flagging a different question."""
    with _lock:
        if which == "next":
            if not _state["pending_question_id"]:
                return False, "Keine Vorschau-Frage"
            if qid is not None and _state["pending_question_id"] != qid:
                return False, "Vorschau hat sich geändert – bitte neu versuchen"
            qid = _state["pending_question_id"]
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
        if qid is not None and _state["question_id"] != qid:
            return False, "Aktive Frage hat sich geändert – bitte neu versuchen"
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
        if (buzzer_num in _state["blocked_slots"]
                or buzzer_num in _state["manual_blocked"]
                or buzzer_num in (_state.get("deactivated_slots") or [])):
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
        answerer = next((r for r in rps if r.slot == slot), None)
        pts = (s["points_correct"] * mult if correct
               else s["points_wrong_self"])
        _log_answer(db, q, _state.get("question_source") or "q",
                    "correct" if correct else "wrong", answerer, pts)
        db.commit()
        _persist_state(db)
        return correct, q


def buzzer_admin(db: Session, action: str, slot: int):
    """Moderator buzzer controls. action: reset|block|unblock|deactivate|activate"""
    with _lock:
        if action == "block":
            if slot not in _state["manual_blocked"]:
                _state["manual_blocked"].append(slot)
        elif action == "unblock":
            _state["manual_blocked"] = [
                s for s in _state["manual_blocked"] if s != slot]
            _state["blocked_slots"] = [
                s for s in _state["blocked_slots"] if s != slot]
        elif action == "deactivate":
            deact = set(_state.get("deactivated_slots") or [])
            deact.add(slot)
            _state["deactivated_slots"] = sorted(deact)
        elif action == "activate":
            _state["deactivated_slots"] = [
                s for s in (_state.get("deactivated_slots") or []) if s != slot]
        # 'reset': no state change – broadcast re-sends the arm commands
        _persist_state(db)


def unshow_question(db: Session):
    """Hide the current question, keep scores (back to idle)."""
    with _lock:
        _state.update({
            "phase": "idle", "question_id": None, "question_source": None,
            "buzzed_slot": None,
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
    blocked = sorted(set(_state["blocked_slots"])
                     | set(_state.get("manual_blocked") or []))
    block_cmds = [str(s - 1) for s in blocked]  # 0-based slot digits
    # 'B <mask>' fades buzzers out (~3 s): non-participating in a Stichfrage
    # plus moderator-deactivated slots (new firmware command – harmless
    # no-op on the musikquiz sketch)
    off = set(_state.get("deactivated_slots") or [])
    tb = _state.get("tiebreak_slots")
    if tb:
        off |= {s for s in range(1, 6) if s not in tb}
    off_cmd = f"B {sum(1 << (s - 1) for s in off)}" if off else None
    if phase == "question":
        cmds = ["5"]  # reset
        cmds += block_cmds or ["9"]  # 0-based blocks | all active
        if off_cmd:
            cmds.append(off_cmd)
        return cmds
    if phase == "resolved":
        picked, correct = _state["picked_answer"], _state["correct_answer"]
        cmds = ["G" if picked == correct else "R"]
        cmds += block_cmds
        if off_cmd:
            cmds.append(off_cmd)
        return cmds
    if phase == "idle":
        # "9" on every idle (round start, hide, skip, finish) lifts the
        # off-mask too, so buzzers fade back to green right away
        cmds = ["5"] + (block_cmds or ["9"])
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
            "blocked": rp.slot in _state["blocked_slots"]
                       or rp.slot in _state["manual_blocked"],
            "deactivated": rp.slot in (_state.get("deactivated_slots") or []),
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
            "questions_max": max_questions(db, rnd),
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
        "answer_reveal": get_setting(db, "answer_reveal", "all"),
        "reveal_delay": get_setting(db, "reveal_delay", 0),
        "reveal_pause": get_setting(db, "reveal_pause", 2),
        "question_shown_at": _state["question_shown_at"],
        "server_now": time.time(),
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
    # effective buzzer status for the moderator's buzzer panel
    st["blocked_slots"] = sorted(
        set(_state["blocked_slots"]) | set(_state.get("manual_blocked") or []))
    st["deactivated_slots"] = _state.get("deactivated_slots") or []
    return st
