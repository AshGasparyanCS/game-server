# arena: a real-time multiplayer game server

An authoritative WebSocket game server for a top-down arena, with client-side prediction and server reconciliation, a matchmaking queue, and a headless load tester. Built on Python `asyncio` and `websockets`.

The interesting parts (the simulation, and the prediction/reconciliation) live in a pure, network-free module (`server/game.py`), so they're directly unit-testable instead of buried behind sockets.

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

- **The server is authoritative.** The server's `World` is the single source of truth. Clients send inputs, the server applies them on its own tick and broadcasts snapshots. A client can't move itself, it can only ask to move.
- **Matchmaking.** Players queue up on join, and once `MATCH_SIZE` of them are waiting, a `Room` is formed and its loop starts. 4 players turns into two 2-player rooms, in order.
- **Fixed-rate loop.** Each room ticks at 30 Hz: drain each player's queued inputs in sequence order, advance the world, broadcast one snapshot to everyone.

## Client prediction and reconciliation

Waiting for a full server round-trip before you move would feel laggy, so the client predicts:

1. **Predict.** On each input, the client applies it locally right away and appends it to a `pending` list tagged with a sequence number.
2. **Send.** The same input also goes to the server.
3. **Reconcile.** Each authoritative snapshot includes `ack`, the highest input sequence the server has processed for that player. The client drops the acked inputs from `pending`, snaps its position to the server's, and **replays whatever's still pending** on top. The local player stays responsive and never drifts away from the server.

`test_reconciliation_replays_unacked_inputs` and `test_predictor_matches_server_when_fully_acked` check exactly this: once every input is acked, the predicted position matches the authoritative position down to floating-point error.

## Run it

```bash
pip install -r requirements.txt
python run.py 8765                # ws://localhost:8765
```

Open `client/index.html` in two browser tabs (serve it or just open the file). They get matched into a room, you move with **WASD**, and you watch the other player move in real time off the authoritative snapshots.

## Load test

```bash
python -m client.loadtest --clients 60 --seconds 6 --url ws://localhost:8765
```

Measured in the build sandbox (**single CPU core**), 60 concurrent clients across 30 matches:

```
clients:           60 (60 reached a match)
inputs sent:       7080 (1162/s)
snapshots recv:    10860 (1783/s)
snapshots/client:  181 (29.7/s)        # holds the full 30 Hz tick per client
max pred drift:    11px (reconciled each snapshot)
```

The server holds the full 30 Hz broadcast to every client under load on a single core, and prediction drift stays tiny and gets corrected every snapshot.

## Tests

```bash
pytest -q     # 8 tests
```

| File | Checks |
|------|--------|
| `test_game.py` | Physics (speed, diagonal normalization, arena clamping), world ack tracking, and prediction/reconciliation convergence. |
| `test_integration.py` | Two real WebSocket clients get matched, player A's movement shows up in the authoritative snapshots, and 4 players form exactly two rooms. |

## Layout

```
server/game.py   pure simulation + Predictor (authoritative + client logic)
server/net.py    matchmaking, room game loop, websockets server
client/index.html browser canvas client (prediction + reconciliation)
client/loadtest.py headless bot + concurrent load tester
run.py           start the server
tests/           game + integration tests
```

## Design notes and limitations

- Snapshots send full state, which is fine for a small arena with a few players per room. At larger scale you'd add delta compression and area-of-interest filtering.
- No lag compensation or interpolation buffer for *remote* players yet, so they're drawn at their last snapshot position. An interpolation buffer (render ~100 ms in the past) would smooth them out. The local player already feels instant thanks to prediction.
- Matchmaking is FIFO with a fixed match size. Skill-based matching would slot into the `Matchmaker` without touching the room or the simulation.
