"""Experience absorption and recovery timing rules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Mapping

from beta_earth.domain.content import (
    ProgressionDefinition,
    TrainingOptionDefinition,
    TrainingProfileDefinition,
)
from beta_earth.domain.model import (
    CharacterState,
    ExperienceState,
    TrainingState,
)


PULSE_SECONDS = 15
PULSE_ABSORPTION = 8
MAX_OFFLINE_PULSES = 240
INSIGHT_PER_LEVEL = 100
BASE_TRAINABLE_ATTRIBUTES = {
    "strength": 12,
    "agility": 12,
    "perception": 10,
    "combat_skill": 5,
}


@dataclass(frozen=True, slots=True)
class TrainingAward:
    milestones: int
    physical_points: int
    mental_points: int
    level_before: int
    level_after: int


@dataclass(frozen=True, slots=True)
class TrainingChange:
    option_id: str
    rank_before: int
    rank_after: int
    pool: str
    points_before: int
    points_after: int
    attribute: str
    attribute_before: int
    attribute_after: int
    refunded: bool


@dataclass(frozen=True, slots=True)
class ProfileChange:
    profile_before: str
    profile_after: str
    changes_remaining: int


def initialize_training(
    progression: ProgressionDefinition,
) -> TrainingState:
    return TrainingState(
        physical_points=progression.starter_points["physical"],
        mental_points=progression.starter_points["mental"],
        early_refunds_remaining=progression.early_refunds,
        profile_id=progression.default_profile,
        profile_changes_remaining=1,
        profile_locked=False,
    )


def award_training_milestones(
    character: CharacterState,
    progression: ProgressionDefinition,
) -> TrainingAward | None:
    """Grant each absorbed-insight milestone once and advance level."""

    target = character.experience.absorbed // INSIGHT_PER_LEVEL
    training = character.training
    if target <= training.last_awarded_milestone:
        return None
    milestones = target - training.last_awarded_milestone
    physical = milestones * progression.milestone_points["physical"]
    mental = milestones * progression.milestone_points["mental"]
    level_before = character.level
    training.physical_points += physical
    training.mental_points += mental
    training.last_awarded_milestone = target
    character.level = target + 1
    return TrainingAward(
        milestones=milestones,
        physical_points=physical,
        mental_points=mental,
        level_before=level_before,
        level_after=character.level,
    )


def buy_training_rank(
    character: CharacterState,
    option: TrainingOptionDefinition,
    profile: TrainingProfileDefinition,
) -> TrainingChange:
    training = character.training
    rank_before = training.ranks.get(option.id, 0)
    if rank_before >= option.max_rank:
        raise ValueError(f"{option.name} is already at its preview rank cap")
    points_field = f"{option.pool}_points"
    points_before = int(getattr(training, points_field))
    cost = effective_training_cost(option, profile)
    if points_before < cost:
        raise ValueError(
            f"{option.name} costs {cost} {option.pool} points on "
            f"{profile.name}; "
            f"only {points_before} remain"
        )
    attribute_before = int(getattr(character, option.attribute))
    points_after = points_before - cost
    rank_after = rank_before + 1
    setattr(training, points_field, points_after)
    training.ranks[option.id] = rank_after
    setattr(
        character,
        option.attribute,
        attribute_before + option.gain_per_rank,
    )
    training.profile_locked = True
    training.profile_changes_remaining = 0
    return TrainingChange(
        option_id=option.id,
        rank_before=rank_before,
        rank_after=rank_after,
        pool=option.pool,
        points_before=points_before,
        points_after=points_after,
        attribute=option.attribute,
        attribute_before=attribute_before,
        attribute_after=int(getattr(character, option.attribute)),
        refunded=False,
    )


def refund_training_rank(
    character: CharacterState,
    option: TrainingOptionDefinition,
    progression: ProgressionDefinition,
    profile: TrainingProfileDefinition,
) -> TrainingChange:
    training = character.training
    rank_before = training.ranks.get(option.id, 0)
    if character.level > progression.early_refund_level_limit:
        raise ValueError(
            "early retraining is closed at your current level"
        )
    if training.early_refunds_remaining <= 0:
        raise ValueError("no early retraining refunds remain")
    if rank_before <= 0:
        raise ValueError(f"you have no {option.name} rank to refund")
    points_field = f"{option.pool}_points"
    points_before = int(getattr(training, points_field))
    attribute_before = int(getattr(character, option.attribute))
    rank_after = rank_before - 1
    points_after = points_before + effective_training_cost(option, profile)
    setattr(training, points_field, points_after)
    if rank_after:
        training.ranks[option.id] = rank_after
    else:
        training.ranks.pop(option.id, None)
    setattr(
        character,
        option.attribute,
        attribute_before - option.gain_per_rank,
    )
    training.early_refunds_remaining -= 1
    return TrainingChange(
        option_id=option.id,
        rank_before=rank_before,
        rank_after=rank_after,
        pool=option.pool,
        points_before=points_before,
        points_after=points_after,
        attribute=option.attribute,
        attribute_before=attribute_before,
        attribute_after=int(getattr(character, option.attribute)),
        refunded=True,
    )


def effective_training_cost(
    option: TrainingOptionDefinition,
    profile: TrainingProfileDefinition,
) -> int:
    return option.cost + profile.cost_modifiers[option.id]


def choose_training_profile(
    character: CharacterState,
    profile: TrainingProfileDefinition,
) -> ProfileChange:
    training = character.training
    before = training.profile_id
    if training.profile_locked or training.ranks:
        raise ValueError("your training path is locked after the first rank")
    if training.profile_changes_remaining <= 0:
        raise ValueError("no training-path changes remain")
    if profile.id == before:
        raise ValueError(f"{profile.name} is already your active path")
    training.profile_id = profile.id
    training.profile_changes_remaining -= 1
    return ProfileChange(
        profile_before=before,
        profile_after=profile.id,
        changes_remaining=training.profile_changes_remaining,
    )


def expected_trainable_attributes(
    training: TrainingState,
    options: Mapping[str, TrainingOptionDefinition],
    *,
    base_attributes: Mapping[str, int] | None = None,
) -> dict[str, int]:
    expected = dict(
        BASE_TRAINABLE_ATTRIBUTES
        if base_attributes is None
        else base_attributes
    )
    if set(expected) != set(BASE_TRAINABLE_ATTRIBUTES):
        raise ValueError(
            "base attributes must define every trainable attribute exactly once"
        )
    if any(type(value) is not int or value < 0 for value in expected.values()):
        raise ValueError("base attributes must be non-negative integers")
    for option_id, rank in training.ranks.items():
        option = options.get(option_id)
        if option is not None:
            expected[option.attribute] += rank * option.gain_per_rank
    return expected


def pulse_experience(experience: ExperienceState, now: float) -> int:
    """Move earned field insight into learned experience at fixed pulses."""
    if experience.field_pool <= 0:
        return 0
    if experience.last_pulse_at <= 0:
        experience.last_pulse_at = now
        return 0
    elapsed = max(0.0, now - experience.last_pulse_at)
    available_pulses = int(elapsed // PULSE_SECONDS)
    pulses = min(available_pulses, MAX_OFFLINE_PULSES)
    if pulses <= 0:
        return 0
    capacity = pulses * PULSE_ABSORPTION
    absorbed = min(experience.field_pool, capacity)
    experience.field_pool -= absorbed
    experience.absorbed += absorbed
    if available_pulses > MAX_OFFLINE_PULSES:
        # The cap is a one-time offline allowance, not a backlog that can be
        # drained by issuing multiple commands immediately after login.
        experience.last_pulse_at = now - (elapsed % PULSE_SECONDS)
    else:
        experience.last_pulse_at += pulses * PULSE_SECONDS
    return absorbed


def award_field_insight(
    experience: ExperienceState,
    amount: int,
    now: float,
) -> None:
    """Add earned insight and start a fresh pulse window after an empty pool."""
    if amount < 0:
        raise ValueError("field insight award cannot be negative")
    if amount == 0:
        return
    if experience.field_pool == 0:
        experience.last_pulse_at = now
    experience.field_pool += amount


def roundtime_remaining(roundtime_until: float, now: float) -> int:
    return max(0, math.ceil(roundtime_until - now))
