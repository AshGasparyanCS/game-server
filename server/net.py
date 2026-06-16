"""Networking: matchmaking queue, per-match room with a fixed-rate game loop,
and the websockets server that ties players to rooms.

Flow: a client connects and sends {join}. It waits in the matchmaking queue;
once MATCH_SIZE players are queued, a Room is created, each player is told
{matched, ...}, and the room's loop begins ticking — draining queued inputs,
advancing the authoritative world, and broadcasting snapshots at TICK_RATE.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque

import websockets

from .game import ARENA_H, ARENA_W, World

TICK_RATE = 30  # snapshots per second
MATCH_SIZE = 2  # players per match


class Connection:
    """One websocket client."""

    def __init__(self, ws):
        self.ws = ws
        self.pid = uuid.uuid4().hex[:8]
        self.name = "anon"
        self.inbox: deque[dict] = deque()  # queued inputs awaiting the next tick
        self.room: "Room | None" = None

    async def send(self, msg: dict) -> None:
        try:
            await self.ws.send(json.dumps(msg))
        except Exception:  # noqa: BLE001
            pass


class Room:
    """A single match: an authoritative world plus its connected players."""

    def __init__(self, room_id: str, conns: list[Connection]):
        self.id = room_id
        self.conns = conns
        self.world = World()
        for c in conns:
            c.room = self
            self.world.add_player(c.pid)
        self._task: asyncio.Task | None = None
        self.running = False

    async def start(self) -> None:
        spawns = {p.id: {"x": p.x, "y": p.y} for p in self.world.players.values()}
        for c in self.conns:
            await c.send({
                "type": "matched",
                "room": self.id,
                "you": c.pid,
                "arena": {"w": ARENA_W, "h": ARENA_H},
                "players": [c2.pid for c2 in self.conns],
                "spawns": spawns,
            })
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        dt = 1.0 / TICK_RATE
        next_t = time.monotonic()
        while self.running and self.conns:
            # Drain each player's queued inputs in order, then advance the tick.
            for c in self.conns:
                while c.inbox:
                    inp = c.inbox.popleft()
                    self.world.apply_input(
                        c.pid, inp["seq"], inp["dx"], inp["dy"], inp.get("dt", dt)
                    )
            self.world.tick += 1
            snap = self.world.snapshot()
            await asyncio.gather(*(c.send(snap) for c in self.conns))
            next_t += dt
            await asyncio.sleep(max(0.0, next_t - time.monotonic()))

    def remove(self, conn: Connection) -> None:
        if conn in self.conns:
            self.conns.remove(conn)
            self.world.remove_player(conn.pid)
        if not self.conns:
            self.running = False


class Matchmaker:
    """Holds waiting players and forms rooms once enough have queued."""

    def __init__(self, match_size: int = MATCH_SIZE):
        self.match_size = match_size
        self.queue: list[Connection] = []
        self.rooms: dict[str, Room] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, conn: Connection) -> None:
        async with self._lock:
            self.queue.append(conn)
            if len(self.queue) >= self.match_size:
                group = self.queue[: self.match_size]
                self.queue = self.queue[self.match_size :]
                room = Room(uuid.uuid4().hex[:8], group)
                self.rooms[room.id] = room
                await room.start()

    async def drop(self, conn: Connection) -> None:
        async with self._lock:
            if conn in self.queue:
                self.queue.remove(conn)
        if conn.room:
            conn.room.remove(conn)
            if not conn.room.conns:
                self.rooms.pop(conn.room.id, None)


class GameServer:
    def __init__(self, match_size: int = MATCH_SIZE):
        self.mm = Matchmaker(match_size)

    async def handler(self, ws) -> None:
        conn = Connection(ws)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                t = msg.get("type")
                if t == "join":
                    conn.name = str(msg.get("name", "anon"))[:24]
                    await conn.send({"type": "queued", "you": conn.pid})
                    await self.mm.enqueue(conn)
                elif t == "input" and conn.room is not None:
                    # Queue the input; the room loop applies it on the next tick.
                    conn.inbox.append({
                        "seq": int(msg.get("seq", 0)),
                        "dx": float(msg.get("dx", 0.0)),
                        "dy": float(msg.get("dy", 0.0)),
                        "dt": float(msg.get("dt", 1.0 / TICK_RATE)),
                    })
        finally:
            await self.mm.drop(conn)

    async def serve(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        async with websockets.serve(self.handler, host, port, ping_interval=20):
            await asyncio.Future()  # run forever
