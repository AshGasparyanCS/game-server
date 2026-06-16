import asyncio
import json

import websockets

from server.net import GameServer


async def _recv_until(ws, mtype, timeout=3):
    async with asyncio.timeout(timeout):
        while True:
            msg = json.loads(await ws.recv())
            if msg["type"] == mtype:
                return msg


async def test_two_clients_match_and_move():
    gs = GameServer(match_size=2)
    async with websockets.serve(gs.handler, "127.0.0.1", 8799):
        async with websockets.connect("ws://127.0.0.1:8799") as a, \
                   websockets.connect("ws://127.0.0.1:8799") as b:
            await a.send(json.dumps({"type": "join", "name": "a"}))
            await b.send(json.dumps({"type": "join", "name": "b"}))

            ma = await _recv_until(a, "matched")
            mb = await _recv_until(b, "matched")
            assert ma["room"] == mb["room"]
            assert set(ma["players"]) == set(mb["players"])

            # Player A drives right repeatedly; the authoritative x must increase.
            start = ma["spawns"][ma["you"]]["x"]
            for seq in range(1, 11):
                await a.send(json.dumps({"type": "input", "seq": seq, "dx": 1, "dy": 0, "dt": 0.05}))
                await asyncio.sleep(0.02)

            # Read snapshots until A's authoritative position has advanced.
            moved = False
            async with asyncio.timeout(3):
                while not moved:
                    state = json.loads(await a.recv())
                    if state["type"] != "state":
                        continue
                    me = next(p for p in state["players"] if p["id"] == ma["you"])
                    if me["x"] > start + 1 and me["ack"] >= 1:
                        moved = True
            assert moved


async def test_matchmaking_pairs_in_order():
    gs = GameServer(match_size=2)
    async with websockets.serve(gs.handler, "127.0.0.1", 8798):
        conns = [await websockets.connect("ws://127.0.0.1:8798") for _ in range(4)]
        try:
            for i, c in enumerate(conns):
                await c.send(json.dumps({"type": "join", "name": f"p{i}"}))
            matched = [await _recv_until(c, "matched") for c in conns]
            rooms = {m["room"] for m in matched}
            # 4 players -> exactly 2 rooms of 2.
            assert len(rooms) == 2
        finally:
            for c in conns:
                await c.close()
