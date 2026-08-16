from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT, load_test_catalog

from beta_earth.infrastructure.content_loader import ContentError, load_catalog


class ContentTests(unittest.TestCase):
    @staticmethod
    def copy_content(root: Path) -> None:
        for name in (
            "world.json",
            "items.json",
            "creatures.json",
            "training.json",
            "courses.json",
            "classes.json",
            "character_creation.json",
            "npcs.json",
            "dialogue.json",
            "quests.json",
            "rewards.json",
            "economy.json",
            "onboarding.json",
            "journey_11_20.json",
            "foundation_activation.json",
        ):
            shutil.copy2(PROJECT_ROOT / "content" / name, root / name)

    def test_catalog_loads_expected_vertical_slice(self) -> None:
        catalog = load_test_catalog()
        self.assertEqual("0.51.1", catalog.version)
        self.assertEqual(118, len(catalog.rooms))
        self.assertEqual(35, len(catalog.items))
        self.assertEqual(32, len(catalog.creatures))
        self.assertIn(catalog.start_room, catalog.rooms)
        self.assertEqual(
            (
                "0.1.0",
                "0.2.0",
                "0.3.0",
                "0.4.0",
                "0.5.0",
                "0.6.0",
                "0.7.0",
                "0.8.0",
                "0.9.0",
                "0.10.0",
                "0.11.0",
                "0.12.0",
                "0.13.0",
                "0.13.1",
                "0.13.2",
                "0.14.0",
                "0.15.0",
                "0.15.1",
                "0.15.2",
                "0.16.0",
                "0.17.0",
                "0.18.0",
                "0.19.0",
                "0.20.0",
                "0.21.0",
                "0.22.0",
                "0.23.0",
                "0.24.0",
                "0.25.0",
                "0.26.0",
                "0.27.0",
                "0.28.0",
                "0.29.0",
                "0.30.0",
                "0.31.0",
                "0.32.0",
                "0.33.0",
                "0.34.0",
                "0.35.0",
                "0.36.0",
                "0.37.0",
                "0.38.0",
                "0.39.0",
                "0.40.0",
                "0.41.0",
                "0.42.0",
                "0.43.0",
                "0.43.1",
                "0.43.2",
                "0.43.3",
                "0.44.0",
                "0.44.1",
                "0.45.0",
                "0.46.0",
                "0.46.1",
                "0.47.2",
                "0.48.2",
                "0.49.0",
                "0.50.0",
                "0.51.0",
            ),
            catalog.additive_from,
        )
        token_spawn = catalog.rooms["intake_concourse"].items[0]
        self.assertEqual("spawn:item:intake-transit-token", token_spawn.id)
        self.assertEqual("transit_token", token_spawn.item_id)
        salvage_spawn = catalog.rooms["salvage_row"].items[0]
        self.assertEqual("spawn:item:salvage-blank-credit-chip", salvage_spawn.id)
        self.assertEqual("blank_credit_chip", salvage_spawn.item_id)
        calibration_spawn = catalog.rooms["calibration_cell"].creatures[0]
        self.assertEqual("spawn:creature:calibration-frame", calibration_spawn.id)
        self.assertTrue(catalog.creatures["calibration_frame"].nonlethal)
        self.assertEqual("light", catalog.items["calibration_knife"].weapon_profile)
        self.assertEqual("heavy", catalog.items["weighted_test_rig"].armor_profile)
        self.assertEqual(40, catalog.items["field_coat"].max_durability)
        self.assertEqual("field_composite", catalog.items["field_coat"].repair_family)
        self.assertEqual(
            12,
            catalog.items["composite_repair_strip"].repair_value,
        )
        self.assertEqual(
            ("repair_bench", "training_station"),
            catalog.rooms["intake_concourse"].facilities,
        )
        self.assertEqual(
            {"physical": 6, "mental": 4},
            dict(catalog.progression.starter_points),
        )
        self.assertEqual(
            {"physical": 2, "mental": 1},
            dict(catalog.progression.milestone_points),
        )
        self.assertEqual(3, catalog.progression.early_refunds)
        self.assertEqual(5, catalog.progression.early_refund_level_limit)
        self.assertEqual(
            {"strength", "agility", "perception", "combat"},
            set(catalog.progression.options),
        )
        self.assertEqual("generalist", catalog.progression.default_profile)
        self.assertEqual(
            {"generalist", "kinetic", "recon", "combat"},
            set(catalog.progression.profiles),
        )
        self.assertEqual(
            -1,
            catalog.progression.profiles["recon"].cost_modifiers[
                "perception"
            ],
        )
        self.assertEqual("heavy", catalog.creatures["tunnel_scavenger"].attack_profile)
        self.assertEqual(2, catalog.items["service_blade"].bulk)
        self.assertEqual(4, catalog.items["scavenged_maul"].bulk)
        course = catalog.courses["uf_readiness"]
        self.assertEqual("intake_concourse", course.start_room)
        self.assertEqual("training_station", course.facility)
        self.assertEqual(
            {"physical": 1, "mental": 1},
            dict(course.reward_points),
        )
        self.assertEqual(4, len(course.steps))
        self.assertEqual(("BE-FEAT-0363",), course.source_features)
        self.assertEqual(12, catalog.creation.budget)
        self.assertEqual(7, len(catalog.creation.factions))
        self.assertEqual(15, len(catalog.creation.classes))
        self.assertEqual(8, len(catalog.creation.packages))
        self.assertEqual(5, len(catalog.creation.tutorial.steps))
        self.assertEqual(
            "security_uf",
            catalog.creation.classes["soldier"].faction_id,
        )
        self.assertEqual(
            "generalist",
            catalog.creation.classes["soldier"].recommended_package_id,
        )
        self.assertEqual(35, len(catalog.story.npcs))
        self.assertEqual(70, len(catalog.story.quests))
        self.assertEqual(262, len(catalog.story.records))
        self.assertEqual(80, len(catalog.story.rewards))
        self.assertEqual(20, catalog.journeyman_experience.target_level)
        self.assertEqual(120, catalog.journeyman_experience.target_minutes)
        self.assertEqual(12, len(catalog.journeyman_experience.competencies))
        self.assertEqual("heavy", catalog.creatures["route_sentinel"].armor_profile)
        self.assertEqual(24, catalog.items["adaptive_repair_weave"].repair_value)
        self.assertEqual("flooded_platform", catalog.rooms["tram_cut"].exits["east"])
        self.assertEqual("second_breath", catalog.story.starting_quest_id)
        self.assertEqual("wake", catalog.story.starting_stage_id)
        self.assertEqual("sealed clinic medicine case", catalog.items["clinic_case"].name)
        self.assertEqual(3, len(catalog.economy.vendors))
        self.assertEqual(6, len(catalog.economy.recipes))
        self.assertEqual(4, len(catalog.economy.mercenaries))
        self.assertEqual(2, len(catalog.creation.classes["soldier"].ability_branches))
        instinct = next(
            action
            for stage in catalog.story.quests["borrowed_medicine"].stages
            for action in stage.actions
            if action.id == "recover_class_instinct"
        )
        self.assertEqual(
            set(catalog.creation.classes),
            set(instinct.class_variants),
        )

    def test_all_exits_resolve(self) -> None:
        catalog = load_test_catalog()
        for room in catalog.rooms.values():
            for destination in room.exits.values():
                self.assertIn(destination, catalog.rooms)

    def test_invalid_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_content(root)
            world_path = root / "world.json"
            document = json.loads(world_path.read_text(encoding="utf-8"))
            document["rooms"][0]["exits"]["out"] = "missing_room"
            world_path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContentError, "missing_room"):
                load_catalog(root)

    def test_duplicate_item_nouns_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_content(root)
            path = root / "items.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["items"][0]["nouns"].append(
                document["items"][0]["nouns"][0].upper()
            )
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContentError, "duplicate nouns"):
                load_catalog(root)

    def test_duplicate_story_action_nouns_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_content(root)
            path = root / "quests.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            action = document["quests"][0]["stages"][0]["actions"][0]
            action["nouns"].append(action["nouns"][0].upper())
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContentError, "duplicate nouns"):
                load_catalog(root)

    def test_non_integer_numeric_fields_are_rejected(self) -> None:
        cases = (
            ("items.json", "items", 0, "roundtime", "3"),
            ("creatures.json", "creatures", 0, "max_health", True),
        )
        for filename, collection, index, field, value in cases:
            with self.subTest(filename=filename, field=field, value=value):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.copy_content(root)
                    path = root / filename
                    document = json.loads(path.read_text(encoding="utf-8"))
                    document[collection][index][field] = value
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaisesRegex(ContentError, "must be an integer"):
                        load_catalog(root)

    def test_non_boolean_nonlethal_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_content(root)
            path = root / "creatures.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["creatures"][0]["nonlethal"] = 1
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContentError, "must be a boolean"):
                load_catalog(root)

    def test_invalid_combat_profile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_content(root)
            path = root / "items.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["items"][0]["weapon_profile"] = "impossible"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContentError, "weapon_profile must be one of"):
                load_catalog(root)

    def test_invalid_item_bulk_is_rejected(self) -> None:
        for value in (True, -1, 21):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.copy_content(root)
                    path = root / "items.json"
                    document = json.loads(path.read_text(encoding="utf-8"))
                    document["items"][0]["bulk"] = value
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ContentError):
                        load_catalog(root)

    def test_invalid_item_durability_is_rejected(self) -> None:
        for value in (True, -1, 10_001):
            with self.subTest(value=value):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.copy_content(root)
                    path = root / "items.json"
                    document = json.loads(path.read_text(encoding="utf-8"))
                    document["items"][1]["durability"] = value
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ContentError):
                        load_catalog(root)

    def test_invalid_repair_metadata_is_rejected(self) -> None:
        cases = (
            ("repair_family", None),
            ("repair_value", True),
            ("repair_value", 10_001),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.copy_content(root)
                    path = root / "items.json"
                    document = json.loads(path.read_text(encoding="utf-8"))
                    document["items"][1][field] = value
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ContentError):
                        load_catalog(root)

    def test_duplicate_or_invalid_facility_is_rejected(self) -> None:
        for facilities in (
            ["repair_bench", "repair_bench"],
            ["Repair Bench"],
        ):
            with self.subTest(facilities=facilities):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.copy_content(root)
                    path = root / "world.json"
                    document = json.loads(path.read_text(encoding="utf-8"))
                    document["rooms"][0]["facilities"] = facilities
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ContentError):
                        load_catalog(root)

    def test_nonpositive_required_numeric_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_content(root)
            path = root / "creatures.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["creatures"][0]["max_health"] = 0
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContentError, "max_health must be between 1"):
                load_catalog(root)

    def test_duplicate_search_reveal_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_content(root)
            path = root / "world.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            reveal = next(room["search"] for room in document["rooms"] if "search" in room)
            document["rooms"][0]["search"] = dict(reveal)
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContentError, "duplicate search reveal id"):
                load_catalog(root)

    def test_duplicate_authored_spawn_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_content(root)
            path = root / "world.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["rooms"][2]["items"] = [dict(document["rooms"][0]["items"][0])]
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContentError, "duplicate authored item spawn id"):
                load_catalog(root)

    def test_additive_source_must_be_unique_and_older(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_content(root)
            path = root / "world.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["additive_from"] = [document["content_version"]]
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContentError, "must be older"):
                load_catalog(root)

    def test_catalog_mappings_are_immutable(self) -> None:
        catalog = load_test_catalog()
        with self.assertRaises(TypeError):
            catalog.rooms["new"] = catalog.rooms[catalog.start_room]  # type: ignore[index]
        with self.assertRaises(TypeError):
            catalog.progression.options["new"] = catalog.progression.options[
                "strength"
            ]  # type: ignore[index]
        with self.assertRaises(TypeError):
            catalog.courses["new"] = catalog.courses["uf_readiness"]  # type: ignore[index]
        with self.assertRaises(TypeError):
            catalog.courses["uf_readiness"].reward_points["physical"] = 4  # type: ignore[index]
        with self.assertRaises(TypeError):
            catalog.creation.classes["new"] = catalog.creation.classes["soldier"]  # type: ignore[index]
        with self.assertRaises(TypeError):
            catalog.creation.packages["generalist"].attributes["strength"] = 16  # type: ignore[index]

    def test_character_creation_requires_equal_bounded_packages(self) -> None:
        cases = (
            ("strength", 17, "between"),
            ("combat_skill", 4, "spends"),
        )
        for attribute, value, message in cases:
            with self.subTest(attribute=attribute, value=value):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.copy_content(root)
                    path = root / "character_creation.json"
                    document = json.loads(path.read_text(encoding="utf-8"))
                    document["packages"][0]["attributes"][attribute] = value
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaisesRegex(ContentError, message):
                        load_catalog(root)

    def test_character_class_cross_references_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_content(root)
            path = root / "classes.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["classes"][0]["training_profile_id"] = "unknown"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContentError, "unknown training profile"):
                load_catalog(root)

    def test_invalid_training_metadata_is_rejected(self) -> None:
        cases = (
            ("starter_points", "physical", True),
            ("milestone_points", "mental", -1),
            ("early_refunds", None, 101),
            ("early_refund_level_limit", None, 0),
            ("options", 0, ("cost", 0)),
            ("options", 0, ("max_rank", 101)),
            ("options", 0, ("attribute", "charisma")),
            ("options", 0, ("pool", "social")),
        )
        for section, key, value in cases:
            with self.subTest(section=section, key=key, value=value):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.copy_content(root)
                    path = root / "training.json"
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if section == "options":
                        assert isinstance(key, int)
                        field, invalid = value
                        document[section][key][field] = invalid
                    elif key is None:
                        document[section] = value
                    else:
                        assert isinstance(key, str)
                        document[section][key] = value
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ContentError):
                        load_catalog(root)

    def test_duplicate_training_identifiers_and_nouns_are_rejected(self) -> None:
        mutations = (
            lambda document: document["options"].append(
                dict(document["options"][0])
            ),
            lambda document: document["options"][0].update(
                {"nouns": ["power", "power"]}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.copy_content(root)
                    path = root / "training.json"
                    document = json.loads(path.read_text(encoding="utf-8"))
                    mutate(document)
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ContentError):
                        load_catalog(root)

    def test_invalid_training_profile_metadata_is_rejected(self) -> None:
        mutations = (
            lambda document: document.update(
                {"default_profile": "missing"}
            ),
            lambda document: document["profiles"][0]["cost_modifiers"].pop(
                "strength"
            ),
            lambda document: document["profiles"][0][
                "cost_modifiers"
            ].update({"strength": True}),
            lambda document: document["profiles"][1][
                "cost_modifiers"
            ].update({"strength": -99}),
            lambda document: document["profiles"][0].update(
                {"nouns": ["general", "general"]}
            ),
            lambda document: document["profiles"].append(
                dict(document["profiles"][0])
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.copy_content(root)
                    path = root / "training.json"
                    document = json.loads(path.read_text(encoding="utf-8"))
                    mutate(document)
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ContentError):
                        load_catalog(root)

    def test_invalid_course_metadata_is_rejected(self) -> None:
        mutations = (
            lambda document: document["courses"][0].update(
                {"reward_points": {"physical": 1}}
            ),
            lambda document: document["courses"][0]["steps"][0].update(
                {"event_kind": "not dotted"}
            ),
            lambda document: document["courses"][0]["steps"][0].update(
                {"event_filters": {"target": ["not", "scalar"]}}
            ),
            lambda document: document["courses"][0].update(
                {"start_room": "missing"}
            ),
            lambda document: document["courses"][0].update(
                {"facility": "missing_station"}
            ),
            lambda document: document["courses"][0]["steps"].append(
                dict(document["courses"][0]["steps"][0])
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    self.copy_content(root)
                    path = root / "courses.json"
                    document = json.loads(path.read_text(encoding="utf-8"))
                    mutate(document)
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ContentError):
                        load_catalog(root)


if __name__ == "__main__":
    unittest.main()
