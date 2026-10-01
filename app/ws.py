"""WebSocket connection manager.

Channels:
  - board:  public scoreboard clients (state broadcasts)
  - admin:  moderator panels (state broadcasts + serial commands for WebSerial)
  - buzzer: serial bridge clients (send buzzer events, receive serial commands)
"""

import json
import logging

from fastapi import WebSocket

from .db import SessionLocal
from . import game

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.board: set[WebSocket] = set()
        self.admin: set[WebSocket] = set()
        self.buzzer: set[WebSocket] = set()

    async def connect(self, ws: WebSocket, channel: str):
        await ws.accept()
        getattr(self, channel).add(ws)
        if channel in ("admin", "buzzer"):
            # first serial-capable client becomes the serial owner
            if game.get_serial_socket() is None and channel in ("admin", "buzzer"):
                pass  # ownership set on 'serial_ready' message

    def disconnect(self, ws: WebSocket):
        self.board.discard(ws)
        self.admin.discard(ws)
        self.buzzer.discard(ws)
        if game.get_serial_socket() is ws:
            game.set_serial_socket(None)

    async def broadcast(self, channel: str, message: dict):
        dead = []
        data = json.dumps(message)
        for ws in getattr(self, channel):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_state(self):
        db = SessionLocal()
        try:
            board_msg = {"type": "state", "data": game.board_state(db)}
            admin_msg = {"type": "state", "data": game.admin_state(db)}
        finally:
            db.close()
        await self.broadcast("board", board_msg)
        await self.broadcast("admin", admin_msg)

    async def send_serial(self, cmds: list[str]):
        """Send Arduino commands to the serial-owning client."""
        ws = game.get_serial_socket()
        if ws is None:
            return
        try:
            await ws.send_text(json.dumps({"type": "serial_cmd", "cmds": cmds}))
        except Exception:
            self.disconnect(ws)


manager = ConnectionManager()


async def handle_buzzer_message(ws: WebSocket, data: dict):
    """Incoming messages on ws channels."""
    t = data.get("type")
    if t == "serial_ready":
        game.set_serial_socket(ws)
        logger.info("Serial owner registered (%s)", "buzzer" if ws in manager.buzzer else "admin")
        return
    if t == "serial_off":
        if game.get_serial_socket() is ws:
            game.set_serial_socket(None)
        return
    if t == "buzzer":
        num = data.get("buzzer")
        try:
            num = int(num)
        except (TypeError, ValueError):
            return
        db = SessionLocal()
        try:
            slot, ok = game.buzzer_pressed(db, num)
        finally:
            db.close()
        if ok:
            await broadcast_state_and_serial()
        return


async def broadcast_state_and_serial():
    """After any state change: broadcast new state + flush Arduino commands."""
    cmds = game.serial_commands_for_phase()
    await manager.send_serial(cmds)
    await manager.broadcast_state()
