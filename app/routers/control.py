"""Game-control endpoints (moderator actions)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import game
from ..auth import require_admin
from ..db import get_db
from ..ws import broadcast_state_and_serial

router = APIRouter(prefix="/api/game", dependencies=[Depends(require_admin)])


class CreateGameBody(BaseModel):
    player_ids: list[int]
    shuffle: bool = True


class AnswerBody(BaseModel):
    answer: int  # 1-4


@router.post("/create")
async def create_game(body: CreateGameBody, request: Request):
    db = next(get_db())
    try:
        game.create_game(db, body.player_ids, body.shuffle)
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
        if err:
            raise HTTPException(400, err)
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True, "question": q.to_dict(reveal=True) if q else None}


@router.post("/question/skip")
async def skip_question():
    db = next(get_db())
    try:
        game.skip_question(db)
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


@router.post("/answer")
async def pick_answer(body: AnswerBody):
    db = next(get_db())
    try:
        correct, q = game.pick_answer(db, body.answer)
        if correct is None:
            raise HTTPException(400, "Kein gebuzzerter Spieler / keine Frage aktiv")
    finally:
        db.close()
    await broadcast_state_and_serial()
    return {"ok": True, "correct": correct}


@router.get("/state")
async def state():
    db = next(get_db())
    try:
        return game.admin_state(db)
    finally:
        db.close()
