"""Local buzzer bridge: Arduino serial <-> remote quiz server via WebSocket.

Runs on the moderator's laptop. Forwards buzzer presses to the server and
writes server commands back to the Arduino.

Usage:
    pip install pyserial websockets
    python -m scripts.buzzer_bridge --port /dev/cu.usbserial-XXXX \
        --server wss://quiz.example.com --token <BUZZER_TOKEN or admin pw>
"""

import argparse
import asyncio
import logging

import serial
import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("buzzer_bridge")

CMDS = {"5", "9", "0", "1", "2", "3", "4", "G", "R", "S", "P"}


async def serial_to_ws(ser, ws):
    loop = asyncio.get_event_loop()
    buf = b""
    while True:
        data = await loop.run_in_executor(None, ser.read, 1)
        if not data:
            continue
        buf += data
        if data == b"\n":
            line = buf.decode("utf-8", "ignore").strip()
            buf = b""
            if line.isdigit():
                # Arduino sends 0-based buzzer index; server slots are 1-based
                log.info("buzzer pressed: %s", line)
                await ws.send(f'{{"type":"buzzer","buzzer":{int(line) + 1}}}')


async def ws_to_serial(ser, ws):
    loop = asyncio.get_event_loop()
    async for msg in ws:
        import json
        try:
            data = json.loads(msg)
        except ValueError:
            continue
        if data.get("type") == "serial_cmd":
            for cmd in data.get("cmds", []):
                if cmd in CMDS or cmd.startswith(("M ", "S ")):
                    await loop.run_in_executor(
                        None, ser.write, (cmd + "\n").encode()
                    )
                    log.info("-> arduino: %s", cmd)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True, help="serial port, e.g. /dev/cu.usbserial-XXX")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--server", required=True, help="e.g. wss://quiz.example.com or ws://localhost:8000")
    ap.add_argument("--token", required=True, help="QUIZ_BUZZER_TOKEN or admin password")
    args = ap.parse_args()

    url = f"{args.server.rstrip('/')}/ws/buzzer?token={args.token}"
    while True:
        try:
            ser = serial.Serial(args.port, args.baud, timeout=1)
            log.info("serial open: %s @%d", args.port, args.baud)
            async with websockets.connect(url) as ws:
                log.info("connected: %s", url)
                await ws.send('{"type":"serial_ready"}')
                await asyncio.gather(serial_to_ws(ser, ws), ws_to_serial(ser, ws))
        except Exception as e:
            log.warning("connection lost: %s – retry in 3s", e)
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
