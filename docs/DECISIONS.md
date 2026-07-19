# Engineering Decisions

## Guided mission

Mission progress is derived from durable facts such as visited rooms and granted flags instead of a second mutable progress counter. Quest definitions live in a strictly validated JSON catalog, and the tracer can promote only a command already available through the application's canonical action surface.

## Bounded economy

The current economy intentionally contains two items and one offer. Stable IDs, cross-catalog validation, optimistic revision checks, and a single atomic transaction prevent duplicate rewards, partial purchases, ambiguous prices, and repeated one-time barter execution.

## Read-only DID readiness

The DID readiness view derives its status from existing flags and inventory. It adds no mutable equipment state, equip or unequip commands, combat modifiers, or save migration. This exposes player-facing progression while keeping the current vertical slice testable and bounded.

## Local-first delivery

The program uses an OS-assigned loopback port, folder-scoped instance ownership, project-relative paths, and no third-party runtime packages. These choices favor portable review, deterministic testing, and clear ownership of local state over hosted or multiplayer features.
