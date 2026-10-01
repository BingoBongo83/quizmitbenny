"""Test: buzzer message on /ws/board only accepted with admin session."""
import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://localhost:8765"
WS = "ws://localhost:8765"
PW = "changeme"


async def main():
    async with httpx.AsyncClient(base_url=BASE) as client:
        await client.post("/login", data={"password": PW})
        st = (await client.get("/api/game/state")).json()
        # ensure a running round + shown question
        pending = [r for r in st["rounds"] if r["status"] == "pending"]
        if not st["round"] and pending:
            await client.post(f"/api/game/round/{pending[0]['id']}/start")
        if st["phase"] in ("idle", "resolved"):
            await client.post("/api/game/question/show")
        await asyncio.sleep(0.2)
        st = (await client.get("/api/game/state")).json()
        assert st["phase"] == "question", st["phase"]

        # anonymous board ws -> buzz ignored
        async with websockets.connect(f"{WS}/ws/board") as ws:
            await ws.recv()  # initial state
            await ws.send(json.dumps({"type": "buzzer", "buzzer": 1}))
            await asyncio.sleep(0.4)
        st = (await client.get("/api/game/state")).json()
        assert st["phase"] == "question", "anonymous buzz was accepted!"
        print("OK anonymous board buzz ignored")

        # admin board ws -> buzz accepted
        cookie = client.cookies.get("session")
        async with websockets.connect(
            f"{WS}/ws/board",
            additional_headers={"Cookie": f"session={cookie}"},
        ) as ws:
            await ws.recv()
            await ws.send(json.dumps({"type": "buzzer", "buzzer": 1}))
            await asyncio.sleep(0.4)
        st = (await client.get("/api/game/state")).json()
        assert st["phase"] == "buzzed" and st["buzzed_slot"] == 1, st["phase"]
        print("OK admin board buzz accepted (slot 1)")


asyncio.run(main())
