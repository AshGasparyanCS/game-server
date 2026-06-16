import math

from server.game import ARENA_H, ARENA_W, SPEED, Predictor, World, step


def test_step_moves_at_speed():
    x, y = step(100, 100, 1, 0, 1.0)
    assert math.isclose(x, 100 + SPEED)
    assert y == 100


def test_diagonal_is_normalized():
    # Moving (1,1) for 1s should cover SPEED total distance, not SPEED*sqrt(2).
    x, y = step(100, 100, 1, 1, 1.0)
    dist = math.hypot(x - 100, y - 100)
    assert math.isclose(dist, SPEED, rel_tol=1e-6)


def test_clamped_to_arena():
    x, y = step(ARENA_W - 1, ARENA_H - 1, 1, 1, 10.0)
    assert x == ARENA_W and y == ARENA_H
    x, y = step(1, 1, -1, -1, 10.0)
    assert x == 0 and y == 0


def test_world_apply_and_ack():
    w = World()
    w.add_player("p1")
    w.apply_input("p1", 5, 1, 0, 1.0)
    assert w.players["p1"].last_seq == 5
    snap = w.snapshot()
    me = next(p for p in snap["players"] if p["id"] == "p1")
    assert me["ack"] == 5
    assert me["x"] > ARENA_W / 2


def test_predictor_matches_server_when_fully_acked():
    """After the server processes every input and the client reconciles with a
    full ack, the client's predicted position equals the server's exactly."""
    w = World()
    w.add_player("me")
    pred = Predictor("me", x=ARENA_W / 2, y=ARENA_H / 2)

    inputs = [(0.3, -0.2), (1.0, 0.0), (-0.5, 0.5), (0.1, 0.9), (-1.0, -1.0)]
    last_seq = 0
    for dx, dy in inputs:
        msg = pred.predict(dx, dy, 0.05)
        w.apply_input("me", msg["seq"], dx, dy, 0.05)
        last_seq = msg["seq"]

    sp = w.players["me"]
    pred.reconcile(sp.x, sp.y, last_seq)
    assert math.isclose(pred.x, sp.x, abs_tol=1e-9)
    assert math.isclose(pred.y, sp.y, abs_tol=1e-9)
    assert pred.pending == []  # nothing left unacknowledged


def test_reconciliation_replays_unacked_inputs():
    """If the server has only processed some inputs, reconciling must keep the
    still-pending inputs and replay them on top of the authoritative state."""
    w = World()
    w.add_player("me")
    pred = Predictor("me", x=ARENA_W / 2, y=ARENA_H / 2)

    # Client predicts 5 inputs; server has only processed the first 2.
    msgs = [pred.predict(1.0, 0.0, 0.05) for _ in range(5)]
    for m in msgs[:2]:
        w.apply_input("me", m["seq"], 1.0, 0.0, 0.05)

    sp = w.players["me"]
    pred.reconcile(sp.x, sp.y, ack=2)
    # 3 inputs remain pending and were replayed.
    assert len(pred.pending) == 3
    # Final predicted x = server x after 2 inputs + 3 more replayed steps.
    expected_x = sp.x + 3 * SPEED * 0.05
    assert math.isclose(pred.x, expected_x, rel_tol=1e-6)
