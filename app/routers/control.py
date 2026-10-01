"""Game-control endpoints (moderator actions)."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import game
from ..auth import require_admin
from ..db import get_db
from ..ws import broadcast_state_and_serial

router = APIRouter(prefix="/api/game", dependencies=[Depends(require_admin)])


class CreateGameBody(BaseModel):
    player_ids: list[int] | None = None
    shuffle: bool = True


class AnswerBody(BaseModel):
    answer: int  # 1-4


class PoolBody(BaseModel):
    pool: str  # 'standard' | 'cat:<id>'


class JokerBody(BaseModel):
    kind: str  # 'fifty' | 'double' | 'audience'


class ReportBody(BaseModel):
    which: str = "current"  # 'current' | 'next'


@router.post("/create")
async def create_game(body: CreateGameBody, request: Request):
    from ..models import Player, get_all_settings
    db = next(get_db())
    try:
        ids = body.player_ids
        if not ids:
            # auto-pick: first N active players (list order), N = ppr * prerounds
            s = get_all_settings(db)
            need = s["players_per_round"] * s["num_prerounds"]
            ids = [
                p.id
                for p in db.query(Player)
                .filter(Player.active)
                .order_by(Player.id)
                .limit(need)
            ]
        if not ids:
            raise HTTPException(400, "Keine aktiven Spieler vorhanden")
        game.create_game(db, ids, body.shuffle)
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True}


@router.post("/round/{round_id}/start")
async def start_round(round_id: int):
    db = next(get_db())
    try:
        rnd = game.start_round(db, round_id)
        if not rnd:
            raise HTTPException(404, "Runde nicht gefunden")
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True}


@router.put("/round/{round_id}/pool")
async def set_round_pool(round_id: int, body: PoolBody):
    """Set which question pool a round draws from."""
    from ..models import Category, Round
    db = next(get_db())
    try:
        rnd = db.get(Round, round_id)
        if not rnd:
            raise HTTPException(404, "Runde nicht gefunden")
        if rnd.status == "finished":
            raise HTTPException(400, "Runde ist bereits beendet")
        pool = body.pool
        if pool.startswith("cat:"):
            if not pool[4:].isdigit() or not db.get(Category, int(pool[4:])):
                raise HTTPException(400, "Unbekannte Kategorie")
        elif pool != "standard":
            raise HTTPException(400, "Ungültiger Pool")
        rnd.question_pool = pool
        db.commit()
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True}


@router.post("/round/{round_id}/finish")
async def finish_round(round_id: int):
    db = next(get_db())
    try:
        game.finish_round(db, round_id)
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True}


@router.post("/question/show")
async def show_question():
    db = next(get_db())
    try:
        q, err = game.show_question(db)
        qd = q.to_dict(reveal=True) if q else None
        if err:
            raise HTTPException(400, err)
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True, "question": qd}


@router.post("/question/skip")
async def skip_question():
    db = next(get_db())
    try:
        game.skip_question(db)
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True}


@router.post("/question/report")
async def report_question(body: ReportBody):
    """Flag the current or next-preview question as broken (excluded from pools)."""
    db = next(get_db())
    try:
        ok, err = game.report_question(db, body.which)
        if not ok:
            raise HTTPException(400, err)
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True}


@router.post("/question/hide")
async def hide_question():
    db = next(get_db())
    try:
        game.unshow_question(db)
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True}


async def _auto_hide_resolved(question_id: int, delay: float = 3.0):
    """Hide a resolved question from the board after `delay` seconds."""
    await asyncio.sleep(delay)
    st = game.get_state()
    if st["phase"] != "resolved" or st["question_id"] != question_id:
        return  # moderator already moved on
    db = next(get_db())
    try:
        game.unshow_question(db)
    finally:
        db.close()
    await broadcast_state_and_serial()


@router.post("/joker")
async def use_joker(body: JokerBody):
    if body.kind not in ("fifty", "double", "audience"):
        raise HTTPException(400, "Unbekannter Joker")
    db = next(get_db())
    try:
        ok, err = game.use_joker(db, body.kind)
        if not ok:
            raise HTTPException(400, err)
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True}


@router.post("/audience/stop")
async def audience_stop():
    """Moderator ends the audience vote -> percentages go on the board."""
    db = next(get_db())
    try:
        if not game.stop_audience_voting(db):
            raise HTTPException(400, "Publikums-Abstimmung läuft nicht")
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True}


@router.post("/answer")
async def pick_answer(body: AnswerBody):
    db = next(get_db())
    try:
        outcome, q = game.pick_answer(db, body.answer)
        if outcome is None:
            raise HTTPException(400, "Kein gebuzzerter Spieler / keine Frage aktiv")
        qid = q.id if q and outcome != "locked" else None
    finally:
        db.close()
    await broadcast_state_and_serial()
    if qid is not None:
        asyncio.get_event_loop().create_task(_auto_hide_resolved(qid))
    return {"ok": True, "outcome": outcome}


@router.get("/state")
async def state():
    db = next(get_db())
    try:
        return game.admin_state(db)
    finally:
        db.close()
