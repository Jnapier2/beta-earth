"""Immutable definitions loaded from versioned content packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SearchReveal:
    id: str
    text: str
    item_id: str | None = None
    flag: str | None = None


@dataclass(frozen=True, slots=True)
class ItemSpawnDefinition:
    id: str
    item_id: str


@dataclass(frozen=True, slots=True)
class CreatureSpawnDefinition:
    id: str
    creature_id: str


@dataclass(frozen=True, slots=True)
class RoomDefinition:
    id: str
    title: str
    description: str
    exits: Mapping[str, str]
    items: tuple[ItemSpawnDefinition, ...] = ()
    creatures: tuple[CreatureSpawnDefinition, ...] = ()
    details: Mapping[str, str] = field(default_factory=dict)
    search: SearchReveal | None = None
    facilities: tuple[str, ...] = ()
    layer: str = "foundation"
    world_body: str = "unspecified"
    source_features: tuple[str, ...] = ()
    story_overlays: Mapping[str, str] = field(default_factory=dict)
    exit_requirements: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    hazard_name: str | None = None
    hazard_text: str = ""
    hazard_damage: int = 0
    hazard_roundtime: int = 0
    hazard_mitigation_items: tuple[str, ...] = ()
    hazard_mitigation_classes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "exits", MappingProxyType(dict(self.exits)))
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))
        object.__setattr__(
            self,
            "story_overlays",
            MappingProxyType(dict(self.story_overlays)),
        )
        object.__setattr__(
            self,
            "exit_requirements",
            MappingProxyType(
                {key: tuple(value) for key, value in self.exit_requirements.items()}
            ),
        )


@dataclass(frozen=True, slots=True)
class ItemDefinition:
    id: str
    name: str
    description: str
    nouns: tuple[str, ...]
    slot: str | None = None
    attack_bonus: int = 0
    defense_bonus: int = 0
    damage_min: int = 1
    damage_max: int = 2
    roundtime: int = 3
    armor: int = 0
    weapon_profile: str = "unarmed"
    armor_profile: str = "none"
    bulk: int = 1
    max_durability: int = 0
    repair_family: str | None = None
    repair_value: int = 0
    base_value: int = 0
    tradeable: bool = False
    salvage_yields: Mapping[str, int] = field(default_factory=dict)
    source_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "salvage_yields",
            MappingProxyType(dict(self.salvage_yields)),
        )


@dataclass(frozen=True, slots=True)
class CreatureDefinition:
    id: str
    name: str
    description: str
    nouns: tuple[str, ...]
    level: int
    max_health: int
    offense: int
    defense: int
    armor: int
    armor_profile: str
    attack_profile: str
    damage_min: int
    damage_max: int
    xp_reward: int
    loot: tuple[str, ...] = ()
    nonlethal: bool = False
    combat_role: str = "skirmisher"
    support_power: int = 0
    behavior_profile: str = "skirmisher"
    action_interval: int = 4
    credit_reward: int = 0
    source_features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TrainingOptionDefinition:
    id: str
    name: str
    description: str
    nouns: tuple[str, ...]
    pool: str
    cost: int
    max_rank: int
    attribute: str
    gain_per_rank: int
    source_features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TrainingProfileDefinition:
    id: str
    name: str
    description: str
    nouns: tuple[str, ...]
    cost_modifiers: Mapping[str, int]
    source_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "cost_modifiers",
            MappingProxyType(dict(self.cost_modifiers)),
        )


@dataclass(frozen=True, slots=True)
class CourseStepDefinition:
    id: str
    description: str
    event_kind: str
    event_filters: Mapping[str, str | int | bool]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_filters",
            MappingProxyType(dict(self.event_filters)),
        )


@dataclass(frozen=True, slots=True)
class CourseDefinition:
    id: str
    name: str
    description: str
    nouns: tuple[str, ...]
    start_room: str
    facility: str
    reward_points: Mapping[str, int]
    steps: tuple[CourseStepDefinition, ...]
    source_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reward_points",
            MappingProxyType(dict(self.reward_points)),
        )


@dataclass(frozen=True, slots=True)
class FactionRouteDefinition:
    id: str
    name: str
    route_label: str
    hq_label: str
    source_features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AbilityPassiveDefinition:
    name: str
    summary: str
    kind: str
    power: int


@dataclass(frozen=True, slots=True)
class AbilityFollowUpDefinition:
    name: str
    summary: str
    kind: str
    power: int
    window_seconds: int
    roundtime: int


@dataclass(frozen=True, slots=True)
class AbilityUpgradeDefinition:
    id: str
    name: str
    summary: str
    power_bonus: int
    follow_up_power_bonus: int
    cooldown_delta: int
    commitment_roundtime_delta: int
    follow_up_window_bonus: int


@dataclass(frozen=True, slots=True)
class AbilityBranchDefinition:
    id: str
    name: str
    summary: str
    kind: str
    power: int
    cooldown: int
    passive: AbilityPassiveDefinition
    follow_up: AbilityFollowUpDefinition
    mastery_uses_required: int
    upgrade_options: Mapping[str, AbilityUpgradeDefinition]
    commitment_roundtime: int
    counterplay: str
    nouns: tuple[str, ...] = ()
    source_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "upgrade_options",
            MappingProxyType(dict(self.upgrade_options)),
        )


@dataclass(frozen=True, slots=True)
class CharacterClassDefinition:
    id: str
    name: str
    faction_id: str
    role: str
    difficulty: str
    summary: str
    tradeoff: str
    training_profile_id: str
    recommended_package_id: str
    technique_name: str
    technique_summary: str
    technique_kind: str
    passive_name: str
    passive_summary: str
    exploration_name: str
    exploration_summary: str
    ability_branches: Mapping[str, AbilityBranchDefinition]
    source_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "ability_branches",
            MappingProxyType(dict(self.ability_branches)),
        )


@dataclass(frozen=True, slots=True)
class CreationAttributeDefinition:
    id: str
    name: str
    abbreviation: str
    minimum: int
    maximum: int
    weight: int
    summary: str
    effects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CreationPackageDefinition:
    id: str
    name: str
    summary: str
    attributes: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "attributes",
            MappingProxyType(dict(self.attributes)),
        )


@dataclass(frozen=True, slots=True)
class TutorialStepDefinition:
    id: str
    description: str
    why: str
    suggested_command: str
    event_kind: str
    event_filters: Mapping[str, str | int | bool]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_filters",
            MappingProxyType(dict(self.event_filters)),
        )


@dataclass(frozen=True, slots=True)
class TutorialDefinition:
    id: str
    title: str
    description: str
    steps: tuple[TutorialStepDefinition, ...]


@dataclass(frozen=True, slots=True)
class CharacterCreationDefinition:
    budget: int
    attributes: Mapping[str, CreationAttributeDefinition]
    packages: Mapping[str, CreationPackageDefinition]
    tutorial: TutorialDefinition
    factions: Mapping[str, FactionRouteDefinition]
    classes: Mapping[str, CharacterClassDefinition]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "attributes",
            MappingProxyType(dict(self.attributes)),
        )
        object.__setattr__(
            self,
            "packages",
            MappingProxyType(dict(self.packages)),
        )
        object.__setattr__(
            self,
            "factions",
            MappingProxyType(dict(self.factions)),
        )
        object.__setattr__(
            self,
            "classes",
            MappingProxyType(dict(self.classes)),
        )


@dataclass(frozen=True, slots=True)
class NpcDefinition:
    id: str
    name: str
    description: str
    nouns: tuple[str, ...]
    room_id: str
    relationship_label: str
    ambient_text: str = ""
    requires_flags: tuple[str, ...] = ()
    forbidden_flags: tuple[str, ...] = ()
    schedule_rooms: Mapping[str, str] = field(default_factory=dict)
    source_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "schedule_rooms",
            MappingProxyType(dict(self.schedule_rooms)),
        )


@dataclass(frozen=True, slots=True)
class DialogueDefinition:
    id: str
    npc_id: str
    title: str
    text: str
    choice_ids: tuple[str, ...] = ()
    source_features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SovereigntyRecordDefinition:
    id: str
    label: str
    description: str


@dataclass(frozen=True, slots=True)
class StoryRewardDefinition:
    id: str
    title: str
    field_insight: int
    physical_points: int
    mental_points: int
    credits: int = 0
    grants_ability_point: bool = False
    items: tuple[str, ...] = ()
    source_features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StoryClassVariantDefinition:
    label: str
    summary: str
    result_text: str


@dataclass(frozen=True, slots=True)
class StoryActionDefinition:
    id: str
    verb: str
    nouns: tuple[str, ...]
    label: str
    summary: str
    approach: str
    result_text: str
    requires_dialogue_id: str | None
    requires_room_id: str | None
    requires_items: tuple[str, ...]
    consumes_items: tuple[str, ...]
    requires_flags: tuple[str, ...]
    requires_records: tuple[str, ...]
    sets_flags: tuple[str, ...]
    clears_flags: tuple[str, ...]
    records: tuple[str, ...]
    relationship_changes: Mapping[str, int]
    reward_id: str | None
    next_quest_id: str | None
    next_stage_id: str | None
    complete_quest: bool
    checkpoint_id: str | None
    route_interest: bool
    route_handoff: bool
    allow_under_pressure: bool
    class_variants: Mapping[str, StoryClassVariantDefinition]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relationship_changes",
            MappingProxyType(dict(self.relationship_changes)),
        )
        object.__setattr__(
            self,
            "class_variants",
            MappingProxyType(dict(self.class_variants)),
        )


@dataclass(frozen=True, slots=True)
class StoryEventTransitionDefinition:
    event_kind: str
    event_filters: Mapping[str, str | int | bool | None]
    next_quest_id: str
    next_stage_id: str
    result_text: str
    sets_flags: tuple[str, ...] = ()
    records: tuple[str, ...] = ()
    despawn_creatures: tuple[str, ...] = ()
    reward_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_filters",
            MappingProxyType(dict(self.event_filters)),
        )


@dataclass(frozen=True, slots=True)
class StoryStageDefinition:
    id: str
    title: str
    objective: str
    directive: str
    why: str
    room_hint: str
    target_room_id: str | None
    suggested_command: str
    progress_index: int
    progress_total: int
    dialogues: Mapping[str, str]
    event_transitions: tuple[StoryEventTransitionDefinition, ...]
    actions: tuple[StoryActionDefinition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dialogues",
            MappingProxyType(dict(self.dialogues)),
        )


@dataclass(frozen=True, slots=True)
class StoryQuestDefinition:
    id: str
    title: str
    arc_title: str
    summary: str
    source_features: tuple[str, ...]
    stages: tuple[StoryStageDefinition, ...]


@dataclass(frozen=True, slots=True)
class StoryDefinition:
    starting_quest_id: str
    starting_stage_id: str
    npcs: Mapping[str, NpcDefinition]
    dialogues: Mapping[str, DialogueDefinition]
    records: Mapping[str, SovereigntyRecordDefinition]
    rewards: Mapping[str, StoryRewardDefinition]
    quests: Mapping[str, StoryQuestDefinition]

    def __post_init__(self) -> None:
        object.__setattr__(self, "npcs", MappingProxyType(dict(self.npcs)))
        object.__setattr__(
            self,
            "dialogues",
            MappingProxyType(dict(self.dialogues)),
        )
        object.__setattr__(
            self,
            "records",
            MappingProxyType(dict(self.records)),
        )
        object.__setattr__(
            self,
            "rewards",
            MappingProxyType(dict(self.rewards)),
        )
        object.__setattr__(self, "quests", MappingProxyType(dict(self.quests)))


@dataclass(frozen=True, slots=True)
class ProgressionDefinition:
    starter_points: Mapping[str, int]
    milestone_points: Mapping[str, int]
    early_refunds: int
    early_refund_level_limit: int
    default_profile: str
    options: Mapping[str, TrainingOptionDefinition]
    profiles: Mapping[str, TrainingProfileDefinition]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "starter_points",
            MappingProxyType(dict(self.starter_points)),
        )
        object.__setattr__(
            self,
            "milestone_points",
            MappingProxyType(dict(self.milestone_points)),
        )
        object.__setattr__(
            self,
            "options",
            MappingProxyType(dict(self.options)),
        )
        object.__setattr__(
            self,
            "profiles",
            MappingProxyType(dict(self.profiles)),
        )


@dataclass(frozen=True, slots=True)
class VendorDefinition:
    id: str
    name: str
    room_id: str
    inventory: Mapping[str, int]
    sell_rate_percent: int = 40

    def __post_init__(self) -> None:
        object.__setattr__(self, "inventory", MappingProxyType(dict(self.inventory)))


@dataclass(frozen=True, slots=True)
class RecipeDefinition:
    id: str
    name: str
    nouns: tuple[str, ...]
    facility: str
    inputs: Mapping[str, int]
    outputs: Mapping[str, int]
    credit_cost: int = 0
    source_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", MappingProxyType(dict(self.inputs)))
        object.__setattr__(self, "outputs", MappingProxyType(dict(self.outputs)))


@dataclass(frozen=True, slots=True)
class MercenaryDefinition:
    id: str
    name: str
    role: str
    summary: str
    hire_room_id: str
    cost: int
    assist_kind: str
    power: int
    story_bound: bool = False
    dismissible: bool = True
    hidden_from_hire: bool = False
    base_health: int = 0
    attack_power: int = 0
    source_features: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EconomyDefinition:
    vendors: Mapping[str, VendorDefinition]
    recipes: Mapping[str, RecipeDefinition]
    mercenaries: Mapping[str, MercenaryDefinition]

    def __post_init__(self) -> None:
        object.__setattr__(self, "vendors", MappingProxyType(dict(self.vendors)))
        object.__setattr__(self, "recipes", MappingProxyType(dict(self.recipes)))
        object.__setattr__(self, "mercenaries", MappingProxyType(dict(self.mercenaries)))


@dataclass(frozen=True, slots=True)
class BeginnerChapterDefinition:
    id: str
    title: str
    summary: str
    minutes: int
    quest_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BeginnerCompetencyDefinition:
    id: str
    label: str
    description: str
    required_quests: tuple[str, ...] = ()
    required_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BeginnerClassAssignmentDefinition:
    class_id: str
    title: str
    objective: str
    practice_command: str


@dataclass(frozen=True, slots=True)
class BeginnerDifficultyBandDefinition:
    id: str
    label: str
    summary: str
    minimum_level: int
    maximum_level: int
    enemy_offense_modifier: int = 0
    enemy_defense_modifier: int = 0
    enemy_armor_modifier: int = 0
    enemy_damage_min_modifier: int = 0
    enemy_damage_max_modifier: int = 0
    player_roundtime_modifier: int = 0


@dataclass(frozen=True, slots=True)
class BeginnerInjuryDefinition:
    id: str
    label: str
    location: str
    summary: str
    onset_text: str
    recovery_text: str
    trigger_level: int
    clear_level: int
    severity_by_level: Mapping[int, int]
    recovery_item_id: str
    onset_health_percent: int
    checkpoint_health_percent: int
    rehabilitation_health_percent: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "severity_by_level",
            MappingProxyType(dict(self.severity_by_level)),
        )


@dataclass(frozen=True, slots=True)
class BeginnerDifficultyCurveDefinition:
    level_checkpoints: Mapping[str, int]
    bands: tuple[BeginnerDifficultyBandDefinition, ...]
    injury: BeginnerInjuryDefinition

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "level_checkpoints",
            MappingProxyType(dict(self.level_checkpoints)),
        )


@dataclass(frozen=True, slots=True)
class BeginnerExperienceDefinition:
    id: str
    title: str
    summary: str
    target_minutes: int
    target_level: int
    starter_room_ids: tuple[str, ...]
    chapters: tuple[BeginnerChapterDefinition, ...]
    competencies: tuple[BeginnerCompetencyDefinition, ...]
    class_assignments: Mapping[str, BeginnerClassAssignmentDefinition]
    difficulty_curve: BeginnerDifficultyCurveDefinition

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "class_assignments",
            MappingProxyType(dict(self.class_assignments)),
        )


@dataclass(frozen=True, slots=True)
class StandingBandDefinition:
    minimum: int
    maximum: int
    label: str


@dataclass(frozen=True, slots=True)
class FoundationFactionImpactDefinition:
    record_id: str
    faction_id: str
    public_delta: int = 0
    covert_delta: int = 0
    access_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FoundationTerritoryImpactDefinition:
    record_id: str
    local_trust_delta: int = 0
    supply_delta: int = 0
    defense_delta: int = 0
    prosperity_delta: int = 0
    tension_delta: int = 0
    visibility_delta: int = 0
    caravan_route_ids: tuple[str, ...] = ()
    world_modifiers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TerritorySeedDefinition:
    id: str
    title: str
    owner_id: str | None
    level: int
    population: int
    supply: int
    defense: int
    prosperity: int
    tension: int
    visibility: int
    canon_status: str
    source_authority: str
    interpretation_note: str


@dataclass(frozen=True, slots=True)
class TerritoryMaintenanceDefinition:
    id: str
    name: str
    summary: str
    roundtime: int
    cooldown_turns: int
    supply_delta: int = 0
    defense_delta: int = 0
    prosperity_delta: int = 0
    tension_delta: int = 0
    visibility_delta: int = 0


@dataclass(frozen=True, slots=True)
class FactionPledgeDefinition:
    faction_id: str
    recruit_title: str
    pledge_statement: str
    minimum_public_standing: int
    required_access_flag: str
    membership_flag: str = "member"


@dataclass(frozen=True, slots=True)
class CivicPlanDefinition:
    id: str
    name: str
    summary: str
    roundtime: int
    field_insight: int
    local_trust_delta: int = 0
    allegiance_standing_delta: int = 0
    supply_delta: int = 0
    defense_delta: int = 0
    prosperity_delta: int = 0
    tension_delta: int = 0
    visibility_delta: int = 0


@dataclass(frozen=True, slots=True)
class CivicMissionDefinition:
    id: str
    title: str
    summary: str
    canon_status: str
    source_authority: str
    interpretation_note: str
    completion_modifier: str
    plans: Mapping[str, CivicPlanDefinition]

    def __post_init__(self) -> None:
        object.__setattr__(self, "plans", MappingProxyType(dict(self.plans)))


@dataclass(frozen=True, slots=True)
class FoundationActivationDefinition:
    id: str
    title: str
    territory_seed: TerritorySeedDefinition
    standing_bands: tuple[StandingBandDefinition, ...]
    faction_impacts: Mapping[str, FoundationFactionImpactDefinition]
    territory_impacts: Mapping[str, FoundationTerritoryImpactDefinition]
    maintenance_actions: Mapping[str, TerritoryMaintenanceDefinition]
    pledge_routes: Mapping[str, FactionPledgeDefinition]
    civic_mission: CivicMissionDefinition
    party_member_limit: int = 6
    mercenary_limit: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "faction_impacts", MappingProxyType(dict(self.faction_impacts)))
        object.__setattr__(self, "territory_impacts", MappingProxyType(dict(self.territory_impacts)))
        object.__setattr__(self, "maintenance_actions", MappingProxyType(dict(self.maintenance_actions)))
        object.__setattr__(self, "pledge_routes", MappingProxyType(dict(self.pledge_routes)))


@dataclass(frozen=True, slots=True)
class ContentCatalog:
    version: str
    start_room: str
    rooms: Mapping[str, RoomDefinition]
    items: Mapping[str, ItemDefinition]
    creatures: Mapping[str, CreatureDefinition]
    progression: ProgressionDefinition
    courses: Mapping[str, CourseDefinition]
    creation: CharacterCreationDefinition
    story: StoryDefinition
    economy: EconomyDefinition
    beginner_experience: BeginnerExperienceDefinition
    journeyman_experience: BeginnerExperienceDefinition
    foundation_activation: FoundationActivationDefinition
    additive_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "rooms", MappingProxyType(dict(self.rooms)))
        object.__setattr__(self, "items", MappingProxyType(dict(self.items)))
        object.__setattr__(self, "creatures", MappingProxyType(dict(self.creatures)))
        object.__setattr__(self, "courses", MappingProxyType(dict(self.courses)))
