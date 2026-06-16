"""Headless client and load tester.

Each Bot connects, joins matchmaking, then drives random movement inputs while
running the same Predictor the browser client uses — predicting locally and
reconciling against authoritative snapshots. The load tester spawns many bots
concurrently and reports throughput and per-client snapshot rates.

Usage:
    python -m client.loadtest --clients 50 --seconds 10 --url ws://localhost:8765
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import time

import websockets

from server.game import Predictor


class Bot:
    def __init__(self, url: str):
        self.url = url
        self.pid: str | None = None
        self.pred: Predictor | None = None
        self.snapshots = 0
        self.inputs = 0
        self.max_drift = 0.0

    async def run(self, seconds: float) -> None:
        async with websockets.connect(self.url) as ws:
            await ws.send(json.dumps({"type": "join", "name": "bot"}))
            # Wait until matched, learning our id and spawn position.
            matched = False
            while not matched:
                msg = json.loads(await ws.recv())
                if msg["type"] == "queued":
                    self.pid = msg["you"]
                elif msg["type"] == "matched":
                    self.pid = msg["you"]
                    sp = msg["spawns"][self.pid]
                    self.pred = Predictor(self.pid, x=sp["x"], y=sp["y"])
                    matched = True

            deadline = time.monotonic() + seconds
            send_task = asyncio.create_task(self._drive(ws, deadline))
            try:
                while time.monotonic() < deadline:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                    msg = json.loads(raw)
                    if msg["type"] == "state":
                        self.snapshots += 1
                        self._on_state(msg)
            except (asyncio.TimeoutError, websockets.ConnectionClosed):
                pass
            finally:
                send_task.cancel()

    async def _drive(self, ws, deadline: float) -> None:
        # ~20 inputs/sec of random direction.
        try:
            while time.monotonic() < deadline:
                dx, dy = random.uniform(-1, 1), random.uniform(-1, 1)
                inp = self.pred.predict(dx, dy, 0.05)
                await ws.send(json.dumps(inp))
                self.inputs += 1
                await asyncio.sleep(0.05)
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass

    def _on_state(self, msg: dict) -> None:
        for p in msg["players"]:
            if p["id"] == self.pid and self.pred is not None:
                # Record drift before reconciling (how far prediction was off).
                drift = ((self.pred.x - p["x"]) ** 2 + (self.pred.y - p["y"]) ** 2) ** 0.5
                self.max_drift = max(self.max_drift, drift)
                self.pred.reconcile(p["x"], p["y"], p["ack"])


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=20)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--url", default="ws://localhost:8765")
    args = ap.parse_args()

    bots = [Bot(args.url) for _ in range(args.clients)]
    t0 = time.monotonic()
    await asyncio.gather(*(b.run(args.seconds) for b in bots), return_exceptions=True)
    elapsed = time.monotonic() - t0

    total_snap = sum(b.snapshots for b in bots)
    total_in = sum(b.inputs for b in bots)
    connected = sum(1 for b in bots if b.snapshots > 0)
    print(f"clients:           {args.clients} ({connected} reached a match)")
    print(f"duration:          {elapsed:.1f}s")
    print(f"inputs sent:       {total_in} ({total_in/elapsed:.0f}/s)")
    print(f"snapshots recv:    {total_snap} ({total_snap/elapsed:.0f}/s)")
    if connected:
        print(f"snapshots/client:  {total_snap/connected:.0f} ({total_snap/connected/elapsed:.1f}/s)")
        print(f"max pred drift:    {max(b.max_drift for b in bots):.1f}px (reconciled each snapshot)")


if __name__ == "__main__":
    asyncio.run(main())
