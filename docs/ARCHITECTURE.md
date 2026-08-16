# Architecture

Beta Earth separates game authority from presentation so the browser interface cannot invent or bypass rules.

## Runtime flow

1. `BetaEarthSovereignty.bat` resolves a supported Python runtime and forwards to `BetaEarthSovereignty.py`.
2. Startup checks confirm release identity, required files, writable project-local paths, SQLite, and loopback availability.
3. The content loader validates the compiled catalogs and constructs the domain engine.
4. The application service owns command dispatch, scheduling, state transitions, and persistence.
5. The HUD server binds to `127.0.0.1` on an OS-assigned port and exposes a narrow token-protected API.
6. The browser renders authoritative `battlefield` and `foundation` projections; it does not calculate outcomes.

## Main boundaries

- `src/beta_earth/domain/` — rules, state, combat, progression, and migrations
- `src/beta_earth/application/` — commands, scheduling, services, and results
- `src/beta_earth/infrastructure/` — content loading, SQLite, startup checks, and diagnostics
- `src/beta_earth/presentation/` — terminal and loopback HUD adapters
- `content/` — compiled, validated gameplay catalogs
- `hud/` — accessible local browser interface and first-party media
- `tests/` — representative public verification suite

## Reliability controls

- Atomic state replacement and revision checks protect saves from partial writes and competing revisions.
- Explicit state migrations preserve old schemas and reject unsupported future schemas.
- Runtime folders, logs, reports, backups, exports, and temporary files stay beneath the extracted project root by default.
- The HTTP surface validates Host and origin assumptions, restricts request sizes, serves a strict content-security policy, and never binds beyond loopback.
- Informational commands are reading-safe. Consequential actions advance shared battlefield time and generate state receipts.
