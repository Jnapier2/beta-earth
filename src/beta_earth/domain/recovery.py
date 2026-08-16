"""Bounded injury progression rules independent of transport and storage."""

from __future__ import annotations

from dataclasses import dataclass

from beta_earth.domain.model import CharacterState


BLEED_PULSE_SECONDS = 10
MAX_OFFLINE_BLEED_PULSES = 6
REST_PULSE_SECONDS = 15
REST_HEALTH_PER_PULSE = 2
MAX_OFFLINE_REST_PULSES = 10
LIMB_LOCATIONS = ("left arm", "right arm", "left leg", "right leg")


@dataclass(frozen=True, slots=True)
class BleedingPulse:
    pulses: int = 0
    damage: int = 0
    rate: int = 0
    checkpoint_changed: bool = False


@dataclass(frozen=True, slots=True)
class ImpactCondition:
    stun_seconds: int = 0
    knocked_down: bool = False


@dataclass(frozen=True, slots=True)
class RestPulse:
    pulses: int = 0
    healed: int = 0
    checkpoint_changed: bool = False


def active_bleeding(character: CharacterState) -> int:
    """Return the character's aggregate bounded bleeding rate."""
    return sum(wound.bleeding for wound in character.wounds)


def disabled_limbs(character: CharacterState) -> tuple[str, ...]:
    """Derive disabled limbs from untreated severity-five wounds."""
    disabled = {
        wound.location
        for wound in character.wounds
        if wound.location in LIMB_LOCATIONS and wound.severity >= 5
    }
    return tuple(location for location in LIMB_LOCATIONS if location in disabled)


def pulse_bleeding(character: CharacterState, now: float) -> BleedingPulse:
    """Apply elapsed bleeding pulses, capped to one minute after an absence."""
    rate = active_bleeding(character)
    if rate <= 0:
        return BleedingPulse()
    if character.condition_pulse_at <= 0:
        character.condition_pulse_at = now
        return BleedingPulse(rate=rate, checkpoint_changed=True)

    elapsed = max(0.0, now - character.condition_pulse_at)
    available = int(elapsed // BLEED_PULSE_SECONDS)
    pulses = min(available, MAX_OFFLINE_BLEED_PULSES)
    if pulses <= 0:
        return BleedingPulse(rate=rate)

    damage = rate * pulses
    character.health -= damage
    if available > MAX_OFFLINE_BLEED_PULSES:
        # Discard the excess backlog so repeated commands cannot replay it.
        character.condition_pulse_at = now - (elapsed % BLEED_PULSE_SECONDS)
    else:
        character.condition_pulse_at += pulses * BLEED_PULSE_SECONDS
    return BleedingPulse(
        pulses=pulses,
        damage=damage,
        rate=rate,
        checkpoint_changed=True,
    )


def apply_impact_condition(
    character: CharacterState,
    *,
    severity: int,
    location: str | None,
    now: float,
) -> ImpactCondition:
    """Apply bounded severe-impact control effects to a character."""
    if severity < 4:
        return ImpactCondition()
    stun_seconds = 5 if severity == 4 else 7
    character.stunned_until = max(character.stunned_until, now + stun_seconds)
    knocked_down = severity >= 5 or (
        severity >= 4 and location is not None and "leg" in location
    )
    if knocked_down:
        character.prone = True
    return ImpactCondition(stun_seconds=stun_seconds, knocked_down=knocked_down)


def pulse_rest(character: CharacterState, now: float) -> RestPulse:
    """Apply bounded out-of-combat health recovery while deliberately resting."""
    if not character.resting or active_bleeding(character) > 0:
        return RestPulse()
    if character.rest_pulse_at <= 0:
        character.rest_pulse_at = now
        return RestPulse(checkpoint_changed=True)
    elapsed = max(0.0, now - character.rest_pulse_at)
    available = int(elapsed // REST_PULSE_SECONDS)
    pulses = min(available, MAX_OFFLINE_REST_PULSES)
    if pulses <= 0:
        return RestPulse()
    before = character.health
    character.health = min(
        character.max_health,
        character.health + pulses * REST_HEALTH_PER_PULSE,
    )
    if available > MAX_OFFLINE_REST_PULSES:
        character.rest_pulse_at = now - (elapsed % REST_PULSE_SECONDS)
    else:
        character.rest_pulse_at += pulses * REST_PULSE_SECONDS
    return RestPulse(
        pulses=pulses,
        healed=character.health - before,
        checkpoint_changed=True,
    )
