"""Settings: players, game config, round assignment, bracket preview."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import game
from ..auth import require_admin
from ..db import get_db
from ..models import (
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
    seat_colors: list[str] | None = None
    joker_fifty: bool | None = None
    joker_double: bool | None = None
    joker_audience: bool | None = None
    answer_lockin: bool | None = None
    skills_preround: list[int] | None = None
    skills_playoff: list[int] | None = None
    skills_semifinal: list[int] | None = None
    skills_final: list[int] | None = None


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


@router.get("/preview")
def bracket_preview(num_players: int):
    db = next(get_db())
    try:
        s = get_all_settings(db)
        return game.compute_bracket_preview(
            num_players, s["players_per_round"], s["num_prerounds"]
        )
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
