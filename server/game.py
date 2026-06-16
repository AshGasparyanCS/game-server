"""Authoritative game simulation and the matching client-side predictor.

This module is deliberately free of any networking or asyncio: it's the pure
game logic, shared in spirit by both server (authoritative) and client
(prediction). Keeping it pure makes the interesting parts — physics,
input ordering, and prediction/reconciliation — directly unit-testable.

The game is a simple top-down arena: each player is a point that moves with a
normalized direction vector at a fixed speed. The server is authoritative; the
client predicts locally and reconciles against server snapshots.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

ARENA_W = 800.0
ARENA_H = 600.0
SPEED = 220.0  # pixels per second


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def step(x: float, y: float, dx: float, dy: float, dt: float) -> tuple[float, float]:
    """Apply one movement input. Diagonal movement is normalized so it isn't
    faster than orthogonal movement. Result is clamped to the arena."""
    mag = math.hypot(dx, dy)
    if mag > 1.0:
        dx, dy = dx / mag, dy / mag
    nx = _clamp(x + dx * SPEED * dt, 0.0, ARENA_W)
    ny = _clamp(y + dy * SPEED * dt, 0.0, ARENA_H)
    return nx, ny


@dataclass
class PlayerState:
    id: str
    x: float
    y: float
    last_seq: int = 0  # highest input sequence the server has processed


class World:
    """The authoritative server-side world."""

    def __init__(self) -> None:
        self.players: dict[str, PlayerState] = {}
        self.tick: int = 0

    def add_player(self, pid: str) -> PlayerState:
        p = PlayerState(id=pid, x=ARENA_W / 2, y=ARENA_H / 2)
        self.players[pid] = p
        return p

    def remove_player(self, pid: str) -> None:
        self.players.pop(pid, None)

    def apply_input(self, pid: str, seq: int, dx: float, dy: float, dt: float) -> None:
        p = self.players.get(pid)
        if p is None:
            return
        p.x, p.y = step(p.x, p.y, dx, dy, dt)
        p.last_seq = max(p.last_seq, seq)

    def snapshot(self) -> dict:
        return {
            "type": "state",
            "tick": self.tick,
            "players": [
                {"id": p.id, "x": round(p.x, 2), "y": round(p.y, 2), "ack": p.last_seq}
                for p in self.players.values()
            ],
        }


@dataclass
class Predictor:
    """Client-side prediction + server reconciliation for the local player.

    The client applies each input immediately (prediction) and keeps the inputs
    it hasn't yet seen acknowledged. When an authoritative snapshot arrives, it
    snaps to the server position and replays the still-unacknowledged inputs on
    top — so the local player stays responsive without drifting from the server.
    """

    pid: str
    x: float = ARENA_W / 2
    y: float = ARENA_H / 2
    seq: int = 0
    pending: list[tuple[int, float, float, float]] = field(default_factory=list)

    def predict(self, dx: float, dy: float, dt: float) -> dict:
        self.seq += 1
        self.pending.append((self.seq, dx, dy, dt))
        self.x, self.y = step(self.x, self.y, dx, dy, dt)
        return {"type": "input", "seq": self.seq, "dx": dx, "dy": dy, "dt": dt}

    def reconcile(self, auth_x: float, auth_y: float, ack: int) -> None:
        # Drop inputs the server has already processed, snap to authority, replay.
        self.pending = [i for i in self.pending if i[0] > ack]
        self.x, self.y = auth_x, auth_y
        for _, dx, dy, dt in self.pending:
            self.x, self.y = step(self.x, self.y, dx, dy, dt)
