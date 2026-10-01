"""Dev tool: simulate buzzer presses without hardware.

    python -m scripts.simulate_buzzer --server ws://localhost:8000 --token pw

Type a buzzer number (1-5) + Enter to simulate a press.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets

from app.config import BUZZER_TOKEN


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="ws://localhost:8000")
    ap.add_argument("--token", default=BUZZER_TOKEN)
    args = ap.parse_args()
    url = f"{args.server.rstrip('/')}/ws/buzzer?token={args.token}"

    async with websockets.connect(url) as ws:
        print(f"connected to {url}")
        print("Buzzernummer eingeben (1-5) + Enter. 'q' beendet.")
        loop = asyncio.get_event_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            line = line.strip()
            if line == "q":
                break
            if line.isdigit():
                await ws.send(json.dumps({"type": "buzzer", "buzzer": int(line)}))
                print(f"-> buzzer {line}")


if __name__ == "__main__":
    asyncio.run(main())
