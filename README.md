# Beta Earth

[![CI](https://github.com/Jnapier2/beta-earth/actions/workflows/ci.yml/badge.svg)](https://github.com/Jnapier2/beta-earth/actions/workflows/ci.yml)

[Portfolio](https://jerry-napier-portfolio.netlify.app/) · [GitHub profile](https://github.com/Jnapier2)

Beta Earth is a working, local-first browser RPG vertical slice built with Python's standard library. A layered engine, validated catalogs, revision-safe saves, and a loopback-only HTTP interface keep behavior testable and local state recoverable without third-party runtime dependencies.

![Beta Earth gameplay interface](assets/beta-earth-gameplay.jpg)

Version `0.4.11` includes a six-room Sprawl 15 scenario, character setup, Caroline's guided route mission, wallet and inventory state, a barter flow, and a read-only equipment-readiness preview.

## Release lineage

- **Current public source authority:** `0.4.11`, plus later portability and CI hardening on `main`.
- **Newer verified final awaiting source transfer:** **Beta Earth: Sovereignty `0.5.0`**.
- **Recorded v0.5.0 evidence:** 81 tests plus CRC, rights, extraction, and release checks; recorded release SHA-256 `ea30ceb8a16566f0bcc20035360eba7bdeb8c8395e044d909ed99a2395e8f97b`.
- **Promotion status:** blocked until the exact v0.5.0 archive is recovered, checksum-matched, audited, tested, and imported through [issue #5](https://github.com/Jnapier2/beta-earth/issues/5).

The repository is intentionally not relabeled as v0.5.0 while those source bytes are unavailable. This makes the newer release visible without presenting an older tree as code it does not contain.

## Architecture and gameplay

- **Layered design:** domain rules and models stay independent of application, infrastructure, and presentation adapters.
- **One command surface:** the HUD, API authorization, keyboard shortcuts, mission tracer, and barter actions share the same ordered set of current actions, preventing an interface from offering behavior the engine has not authorized.
- **Defensive persistence:** JSON saves use strict validation, atomic replacement, revision checks, bounded migrations, and migration backups.
- **Local security boundary:** the bundled server binds only to `127.0.0.1`, rejects non-loopback host/origin inputs, limits request size and concurrency, and serves restrictive security headers.
- **Portable runtime:** content and runtime paths are project-relative; an OS-assigned port avoids scanning or displacing other applications.
- **Accessible UI:** the HUD includes keyboard navigation, visible focus, reduced-motion support, forced-color support, responsive layouts, and minimum target sizing.

## Run locally

Requirements: Python 3.11, 3.12, or 3.13. No package installation is required.

On Windows, run:

```powershell
.\START_BETA_EARTH.bat
```

On any supported Python environment, run:

```bash
python run_beta_earth.py
```

The program opens a browser to an OS-assigned loopback address. Press `Ctrl+C` in the terminal to stop it. To validate startup without opening a browser or keeping the server running, use either entry point:

```powershell
.\START_BETA_EARTH.bat --dry-run --no-browser
```

```bash
python run_beta_earth.py --dry-run --no-browser
```

Runtime state is written only to ignored project-local folders such as `state/`, `logs/`, and `temp/`.

## Test

```bash
python -m unittest discover -s tests -t . -v
```

The suite covers domain validation, application transactions, migrations and persistence, loopback HTTP behavior, mission and economy flows, UI contracts, and startup resilience. Hosted CI runs the suite and startup dry-run on Windows and Ubuntu across Python 3.11-3.13. This is source-portability evidence, not a claim that every named physical computer has completed acceptance testing.

## Project map

| Path | Purpose |
|---|---|
| `src/beta_earth/domain/` | Typed models and pure game rules |
| `src/beta_earth/application/` | Use cases, command authorization, and ports |
| `src/beta_earth/infrastructure/` | Catalog loading, persistence, runtime paths, and instance ownership |
| `src/beta_earth/presentation/` | HTTP adapter and view-model projection |
| `data/` | Validated world, quest, and economy catalogs with JSON schemas |
| `static/` | Local browser HUD |
| `tests/` | Unit and integration tests |

Additional design context is available in [Architecture](docs/ARCHITECTURE.md), [Engineering decisions](docs/DECISIONS.md), and [Compatibility](docs/COMPATIBILITY.md).

## Scope and status

This is a working single-player vertical slice, not a hosted service or a complete game. Multiplayer, internet hosting, combat, dynamic markets, and mutable equipment are outside the current scope. Save schema `4.0` remains the current format.

Created by Jerry R. Napier. Copyright © 2026 Gateway Information Group LLC. All rights reserved. See [LICENSE.md](LICENSE.md) for terms.
