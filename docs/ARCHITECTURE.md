# Architecture

Beta Earth uses a layered architecture with dependencies pointing inward:

```text
presentation  --\
infrastructure -> application -> domain
bootstrap     --/
```

## Layers

- `domain/` contains typed game state, quest and economy records, identity rules, and pure action derivation.
- `application/` authorizes commands, coordinates transactions, and exposes abstract persistence and content ports.
- `infrastructure/` implements strict JSON catalog loading, atomic player persistence, runtime-path validation, and process ownership.
- `presentation/` maps snapshots to JSON and serves the local browser interface.
- `bootstrap.py` is the composition root and the only layer that wires concrete adapters together.

## Key invariants

1. The application service exposes one ordered current-action collection. The API, HUD buttons, shortcuts, mission tracer, and barter UI cannot invent separate commands.
2. State-changing requests require an expected revision. Stale commands fail instead of being replayed or silently merged.
3. Catalogs validate their shape, bounds, references, reserved commands, and reachable mission steps before use.
4. Player saves are written atomically. Supported legacy schemas migrate in one direction with a backup and journal; unsupported or inconsistent data fails explicitly.
5. The HTTP adapter accepts loopback traffic only, bounds request size and concurrency, and treats browser state as a view of application state rather than a second game engine.
6. Runtime directories must resolve inside the project. Symlink or reparse-point redirection is rejected before state is written.

## Runtime flow

```text
Browser HUD
    -> loopback HTTP adapter
    -> GameService authorization and transaction
    -> validated catalogs + revisioned player repository
    -> immutable snapshot
    -> view-model projection
    -> Browser HUD
```

The default port is `0`, so the operating system selects a free loopback port atomically. Instance ownership is scoped to the project folder, allowing separate copies to run without sharing state or locks.

