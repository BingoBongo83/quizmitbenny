import json
import logging

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import BASE_DIR, BUZZER_TOKEN, SECRET_KEY
from .db import Base, SessionLocal, engine
from . import game
from .models import AdminUser
from .routers import control, pages, questions, settings
from .ws import handle_buzzer_message, manager, may_buzz

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Quiz")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.include_router(pages.router)
app.include_router(control.router)
app.include_router(settings.router)
app.include_router(questions.router)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        game.load_state(db)
        if db.query(AdminUser).count() == 0:
            logger.warning(
                "Kein Admin-Benutzer vorhanden – anlegen mit: "
                "python -m scripts.admin create <benutzername>"
            )
        if not BUZZER_TOKEN:
            logger.warning(
                "BUZZER_TOKEN nicht gesetzt – Bridge-Verbindungen werden abgelehnt"
            )
    finally:
        db.close()


async def _ws_loop(ws: WebSocket, channel: str, privileged: bool = False):
    await manager.connect(ws, channel, privileged)
    # send initial state
    db = SessionLocal()
    try:
        state = (
            game.admin_state(db) if channel == "admin" else game.board_state(db)
        )
    finally:
        db.close()
    await ws.send_text(json.dumps({"type": "state", "data": state}))
    try:
        while True:
            msg = await ws.receive_text()
            try:
                data = json.loads(msg)
            except ValueError:
                continue
            if channel in ("admin", "buzzer") or (
                channel == "board" and data.get("type") == "buzzer"
                and may_buzz(ws)
            ):
                await handle_buzzer_message(ws, data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("ws error: %s", e)
    finally:
        manager.disconnect(ws)


@app.websocket("/ws/board")
async def ws_board(ws: WebSocket):
    privileged = bool(ws.scope.get("session", {}).get("admin"))
    await _ws_loop(ws, "board", privileged)


@app.get("/api/me")
def me(request: Request):
    return {"admin": bool(request.session.get("admin"))}


@app.websocket("/ws/admin")
async def ws_admin(ws: WebSocket):
    if not ws.scope.get("session", {}).get("admin"):
        await ws.close(code=4401)
        return
    await _ws_loop(ws, "admin")


@app.websocket("/ws/buzzer")
async def ws_buzzer(ws: WebSocket):
    token = ws.query_params.get("token", "")
    if not (token and BUZZER_TOKEN and token == BUZZER_TOKEN):
        await ws.close(code=4401)
        return
    await _ws_loop(ws, "buzzer")
