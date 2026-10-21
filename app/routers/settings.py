"""Settings: players, game config, round assignment, bracket preview."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func

from .. import game
from ..auth import require_admin
from ..db import get_db
from ..models import (
    AnswerLog,
    Category,
    CustomQuestion,
    Player,
    Question,
    Round,
    RoundPlayer,
    get_all_settings,
    set_setting,
)
from ..ws import broadcast_state_and_serial

router = APIRouter(prefix="/api/settings", dependencies=[Depends(require_admin)])


class PlayerBody(BaseModel):
    name: str
    active: bool = True


class SettingsBody(BaseModel):
    players_per_round: int | None = None
    num_prerounds: int | None = None
    points_correct: int | None = None
    points_wrong_self: int | None = None
    points_wrong_others: int | None = None
    block_on_wrong: bool | None = None
    sound_target: str | None = None
    answer_reveal: str | None = None
    reveal_delay: float | None = None
    reveal_pause: float | None = None
    seat_colors: list[str] | None = None
    joker_fifty: bool | None = None
    joker_double: bool | None = None
    joker2_kind: str | None = None
    joker2_seconds: int | None = None
    joker_audience: bool | None = None
    answer_lockin: bool | None = None
    max_questions_enabled: bool | None = None
    max_questions: int | None = None
    max_questions_preround: int | None = None
    max_questions_playoff: int | None = None
    max_questions_semifinal: int | None = None
    max_questions_final: int | None = None
    skills_preround: list[int] | None = None
    skills_playoff: list[int] | None = None
    skills_semifinal: list[int] | None = None
    skills_final: list[int] | None = None
    default_pool: str | None = None


# ---------------- players ----------------

@router.get("/players")
def list_players():
    db = next(get_db())
    try:
        return [
            {"id": p.id, "name": p.name, "active": p.active}
            for p in db.query(Player).order_by(Player.id).all()
        ]
    finally:
        db.close()


@router.post("/players")
def add_player(body: PlayerBody):
    db = next(get_db())
    try:
        p = Player(name=body.name.strip(), active=body.active)
        db.add(p)
        db.commit()
        return {"id": p.id, "name": p.name}
    finally:
        db.close()


@router.put("/players/{player_id}")
def update_player(player_id: int, body: PlayerBody):
    db = next(get_db())
    try:
        p = db.get(Player, player_id)
        if not p:
            raise HTTPException(404)
        p.name = body.name.strip()
        p.active = body.active
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.delete("/players/{player_id}")
def delete_player(player_id: int):
    db = next(get_db())
    try:
        p = db.get(Player, player_id)
        if not p:
            raise HTTPException(404)
        db.delete(p)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ---------------- game settings ----------------

@router.get("")
def get_settings():
    db = next(get_db())
    try:
        return get_all_settings(db)
    finally:
        db.close()


@router.put("")
def update_settings(body: SettingsBody):
    db = next(get_db())
    try:
        data = body.model_dump(exclude_none=True)
        if "players_per_round" in data and not 3 <= data["players_per_round"] <= 5:
            raise HTTPException(400, "Spieler pro Runde muss 3-5 sein")
        if "num_prerounds" in data and not 3 <= data["num_prerounds"] <= 5:
            raise HTTPException(400, "Vorrunden muss 3-5 sein")
        if "sound_target" in data and data["sound_target"] not in ("board", "admin", "both"):
            raise HTTPException(400, "Ungültiges Sound-Ziel")
        if "answer_reveal" in data and data["answer_reveal"] not in ("all", "seq"):
            raise HTTPException(400, "Ungültiger Einblend-Modus")
        if "reveal_pause" in data and not 0.3 <= data["reveal_pause"] <= 15:
            raise HTTPException(400, "Pause muss 0.3-15 Sekunden sein")
        if "reveal_delay" in data and not 0 <= data["reveal_delay"] <= 15:
            raise HTTPException(400, "Start-Pause muss 0-15 Sekunden sein")
        if "max_questions" in data and not 1 <= data["max_questions"] <= 200:
            raise HTTPException(400, "Max. Fragen muss 1-200 sein")
        for k in ("max_questions_preround", "max_questions_playoff",
                  "max_questions_semifinal", "max_questions_final"):
            if k in data and not 1 <= data[k] <= 200:
                raise HTTPException(400, "Max. Fragen muss 1-200 sein")
        if "default_pool" in data:
            pool = data["default_pool"]
            if pool != "standard" and (
                not pool.startswith("cat:") or not pool[4:].isdigit()
                or not db.get(Category, int(pool[4:]))
            ):
                raise HTTPException(400, "Ungültiger Standard-Pool")
        if "joker2_kind" in data and data["joker2_kind"] not in (
                "double", "countdown", "sicher", "retry"):
            raise HTTPException(400, "Ungültiger Joker-Typ")
        if "joker2_seconds" in data and not 5 <= data["joker2_seconds"] <= 300:
            raise HTTPException(400, "Countdown muss 5-300 Sekunden sein")
        if "seat_colors" in data:
            import re
            if not data["seat_colors"] or not all(
                re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in data["seat_colors"]
            ):
                raise HTTPException(400, "Platz-Farben müssen #RRGGBB sein")
        for k, v in data.items():
            if k.startswith("skills_") and not all(1 <= s <= 5 for s in v):
                raise HTTPException(400, "Skill-Level müssen 1-5 sein")
            set_setting(db, k, v)
        return get_all_settings(db)
    finally:
        db.close()


@router.get("/plans")
def game_plans(num_players: int):
    """Bracket plan options for a given player count."""
    db = next(get_db())
    try:
        s = get_all_settings(db)
        return {"plans": game.compute_plans(
            num_players, s["players_per_round"], s["num_prerounds"]
        )}
    finally:
        db.close()


class MovePlayerBody(BaseModel):
    round_player_id: int
    to_round_id: int


@router.post("/move-player")
async def move_player(body: MovePlayerBody):
    """Manually move a player to another (pending) round."""
    db = next(get_db())
    try:
        rp = db.get(RoundPlayer, body.round_player_id)
        target = db.get(Round, body.to_round_id)
        if not rp or not target:
            raise HTTPException(404)
        if target.status != "pending":
            raise HTTPException(400, "Zielrunde ist nicht mehr offen")
        used_slots = {
            x.slot for x in db.query(RoundPlayer).filter(
                RoundPlayer.round_id == target.id).all()
        }
        free = [s for s in range(1, 6) if s not in used_slots]
        if not free:
            raise HTTPException(400, "Zielrunde ist voll")
        rp.round_id = target.id
        rp.slot = min(free)
        rp.score = 0
        db.commit()
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True}


@router.post("/reset-questions")
def reset_questions():
    db = next(get_db())
    try:
        db.query(Question).update({Question.used: False, Question.used_round_id: None})
        db.query(CustomQuestion).update(
            {CustomQuestion.used: False, CustomQuestion.used_round_id: None}
        )
        db.commit()
        return {"ok": True}
    finally:
        db.close()


ROUND_TYPE_LABELS = {
    "preround": "Vorrunde",
    "playoff_presemi": "Playoff",
    "semifinal": "Halbfinale",
    "playoff_prefinal": "Playoff",
    "final": "Finale",
}


@router.get("/stats")
def stats():
    """Question-pool distribution + persistent answer statistics."""
    db = next(get_db())
    try:
        # --- standard pool: per skill level + per category string ---
        skills = {}
        cats = {}
        for sk, used, rep, cat in db.query(
                Question.skill, Question.used, Question.reported,
                Question.category).all():
            for r in (skills.setdefault(sk, {"skill": sk, "total": 0,
                                             "used": 0, "free": 0,
                                             "reported": 0}),
                      cats.setdefault(cat or "–", {"name": cat or "–",
                                                   "total": 0, "used": 0,
                                                   "free": 0,
                                                   "reported": 0})):
                r["total"] += 1
                if rep:
                    r["reported"] += 1
                elif used:
                    r["used"] += 1
                else:
                    r["free"] += 1
        categories = [{"custom": False, **r}
                      for _, r in sorted(cats.items())]
        # --- custom/category pools ---
        for cat in db.query(Category).order_by(Category.name).all():
            row = {"name": cat.name, "custom": True, "total": 0,
                   "used": 0, "free": 0, "reported": 0}
            for used, rep in db.query(CustomQuestion.used,
                                      CustomQuestion.reported).filter(
                    CustomQuestion.category_id == cat.id).all():
                row["total"] += 1
                if rep:
                    row["reported"] += 1
                elif used:
                    row["used"] += 1
                else:
                    row["free"] += 1
            categories.append(row)

        # --- answer stats: per player, current game + all-time ---
        seq = game.get_state().get("game_seq") or 0

        def player_stats(*filters):
            rows = (db.query(
                AnswerLog.player_name,
                func.count(AnswerLog.id),
                func.sum(case((AnswerLog.outcome == "correct", 1), else_=0)),
                func.sum(case((AnswerLog.outcome == "wrong", 1), else_=0)),
                func.sum(AnswerLog.points),
                func.count(func.distinct(AnswerLog.game_seq)))
                .filter(AnswerLog.player_name != "", *filters)
                .group_by(AnswerLog.player_name)
                .all())
            out = [{"name": n, "answered": c or 0, "correct": ok or 0,
                    "wrong": w or 0, "points": p or 0, "games": g or 0}
                   for n, c, ok, w, p, g in rows]
            out.sort(key=lambda a: (-a["correct"], a["name"]))
            return out

        events = [{
            "game": e.game_seq,
            "round": f"{ROUND_TYPE_LABELS.get(e.round_type, e.round_type)}"
                     f" {e.round_number}".strip(),
            "player": e.player_name or "–",
            "question": e.question_text,
            "category": e.category,
            "outcome": e.outcome,
            "points": e.points,
            "ts": e.created_at,
        } for e in (db.query(AnswerLog)
                    .order_by(AnswerLog.id.desc())
                    .limit(300)
                    .all())]

        return {
            "game_seq": seq,
            "skills": sorted(skills.values(), key=lambda s: s["skill"]),
            "categories": categories,
            "players_game": player_stats(AnswerLog.game_seq == seq),
            "players_all": player_stats(),
            "events": events,
        }
    finally:
        db.close()


@router.post("/reset-game")
async def reset_game():
    """Delete all rounds and reset game state (players + questions kept)."""
    db = next(get_db())
    try:
        db.query(RoundPlayer).delete()
        db.query(Round).delete()
        game.get_state().update({
            "phase": "idle", "round_id": None, "question_id": None,
            "pending_question_id": None,
            "buzzed_slot": None, "picked_answer": None,
            "correct_answer": None, "blocked_slots": [],
            "game_started": False,
        })
        db.commit()
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True}
