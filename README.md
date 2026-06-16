# arena — a real-time multiplayer game server

An authoritative WebSocket game server for a top-down arena, with client-side
prediction and server reconciliation, a matchmaking queue, and a headless load
tester. Built on Python `asyncio` + `websockets`.

The interesting parts — the simulation, and prediction/reconciliation — live in a
pure, network-free module (`server/game.py`) so they're directly unit-tested.

## Architecture

```
  browser / bot                          server (asyncio)
  ┌───────────────┐   join              ┌──────────────────────────────┐
  │ Predictor     │ ───────────────────►│ Matchmaker (queue)           │
  │  predict()    │   input{seq,dx,dy}  │   ── forms Rooms of N players │
  │  reconcile()  │ ───────────────────►│ Room                         │
  │               │                     │  ┌────────────────────────┐  │
  │  render @60fps│ ◄───────────────────│  │ game loop @30Hz:        │  │
  └───────────────┘   state{tick,       │  │  drain input queues     │  │
                       players,ack}      │  │  advance World (authz)  │  │
                                         │  │  broadcast snapshot     │  │
                                         │  └────────────────────────┘  │
                                         └──────────────────────────────┘
```

- **Authoritative server.** The server's `World` is the single source of truth.
  Clients send inputs; the server applies them on its own tick and broadcasts
  snapshots. A client can't move itself — it can only *ask* to move.
- **Matchmaking.** Players queue on join; once `MATCH_SIZE` are waiting, a `Room`
  is formed and its loop starts. 4 players → two 2-player rooms, in order.
- **Fixed-rate loop.** Each room ticks at 30 Hz: drain each player's queued
  inputs in sequence order, advance the world, broadcast one snapshot to all.

## Client prediction & reconciliation

Waiting for a server round-trip before moving would feel laggy, so the client
predicts:

1. **Predict** — on each input, the client applies it locally *immediately* and
   appends it to a `pending` list, tagged with a sequence number.
2. **Send** — the same input goes to the server.
3. **Reconcile** — each authoritative snapshot includes `ack`, the highest input
   sequence the server has processed for that player. The client drops acked
   inputs from `pending`, snaps its position to the server's, and **replays the
   still-pending inputs** on top. The local player stays responsive and never
   drifts from the server.

`test_reconciliation_replays_unacked_inputs` and
`test_predictor_matches_server_when_fully_acked` verify exactly this: once all
inputs are acked, predicted position equals authoritative position to within
floating-point error.

## Run it

```bash
pip install -r requirements.txt
python run.py 8765                # ws://localhost:8765
```

Open `client/index.html` in two browser tabs (serve it or open as a file).
They'll be matched into a room; move with **WASD** and watch the other player,
driven by authoritative snapshots, move in real time.

## Load test

```bash
python -m client.loadtest --clients 60 --seconds 6 --url ws://localhost:8765
```

Measured in the build sandbox (**single CPU core**), 60 concurrent clients in 30
matches:

```
clients:           60 (60 reached a match)
inputs sent:       7080 (1162/s)
snapshots recv:    10860 (1783/s)
snapshots/client:  181 (29.7/s)        # holds the full 30 Hz tick per client
max pred drift:    11px (reconciled each snapshot)
```

The server sustains the full 30 Hz broadcast to every client under load on one
core, and prediction drift stays tiny and is corrected every snapshot.

## Tests

```bash
pytest -q     # 8 tests
```

| File | Checks |
|------|--------|
| `test_game.py` | Physics (speed, diagonal normalization, arena clamping), world ack tracking, and prediction/reconciliation convergence. |
| `test_integration.py` | Two real WebSocket clients are matched, A's movement is reflected in authoritative snapshots, and 4 players form exactly two rooms. |

## Layout

```
server/game.py   pure simulation + Predictor (authoritative + client logic)
server/net.py    matchmaking, room game loop, websockets server
client/index.html browser canvas client (prediction + reconciliation)
client/loadtest.py headless bot + concurrent load tester
run.py           start the server
tests/           game + integration tests
```

## Design notes & limitations

- Snapshots send full state (fine for a small arena and few players per room).
  At larger scale you'd add delta compression and area-of-interest filtering.
- No lag compensation / interpolation buffer for *remote* players yet — they're
  drawn at their last snapshot position; an interpolation buffer (render ~100 ms
  in the past) would smooth them. The local player already feels instant via
  prediction.
- Matchmaking is FIFO with a fixed match size; skill-based matching would slot in
  at the `Matchmaker` without touching the room or simulation.
