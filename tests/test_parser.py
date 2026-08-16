from __future__ import annotations

import unittest

from tests.support import PROJECT_ROOT  # noqa: F401 - establishes source path

from beta_earth.application.parser import CommandParseError, CommandParser
from beta_earth.domain.actions import RecoveryClass


class CommandParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = CommandParser()

    def test_exact_command(self) -> None:
        parsed = self.parser.parse("look")
        self.assertEqual("look", parsed.name)
        self.assertFalse(parsed.hard)
        self.assertEqual(RecoveryClass.SOFT, parsed.recovery)

    def test_direction_shortcut_becomes_go_intent(self) -> None:
        parsed = self.parser.parse("ne")
        self.assertEqual("go", parsed.name)
        self.assertEqual(("northeast",), parsed.args)
        self.assertTrue(parsed.hard)
        self.assertEqual(RecoveryClass.HARD, parsed.recovery)

    def test_explicit_alias_wins_over_prefixes(self) -> None:
        self.assertEqual("go", self.parser.parse("e").name)
        self.assertEqual("inventory", self.parser.parse("i").name)

    def test_unambiguous_prefix(self) -> None:
        self.assertEqual("attack", self.parser.parse("att mite").name)

    def test_quote_shortcut_preserves_message(self) -> None:
        parsed = self.parser.parse("' hello there")
        self.assertEqual("say", parsed.name)
        self.assertEqual(("hello there",), parsed.args)

    def test_quoted_argument(self) -> None:
        parsed = self.parser.parse('say "hello there"')
        self.assertEqual(("hello there",), parsed.args)

    def test_contraction_does_not_begin_a_quote(self) -> None:
        parsed = self.parser.parse("say I'm ready")
        self.assertEqual(("I'm", "ready"), parsed.args)

    def test_unknown_command_has_discovery_hint(self) -> None:
        with self.assertRaisesRegex(CommandParseError, "Try HELP"):
            self.parser.parse("flibbertigibbet")

    def test_empty_command_is_helpful(self) -> None:
        with self.assertRaisesRegex(CommandParseError, "HELP"):
            self.parser.parse("   ")

    def test_unclosed_quote_is_reported(self) -> None:
        with self.assertRaisesRegex(CommandParseError, "could not parse"):
            self.parser.parse('say "unfinished')

    def test_story_commands_have_truthful_recovery_classes_and_aliases(self) -> None:
        self.assertEqual("talk", self.parser.parse("converse sol").name)
        self.assertEqual("choose", self.parser.parse("decide share").name)
        self.assertEqual("interact", self.parser.parse("activate relay").name)
        self.assertEqual("quest", self.parser.parse("objective").name)
        self.assertEqual(RecoveryClass.SOFT, self.parser.parse("talk sol").recovery)
        self.assertEqual(RecoveryClass.SOFT, self.parser.parse("choose refuse").recovery)
        self.assertEqual(RecoveryClass.HARD, self.parser.parse("interact relay").recovery)
        self.assertEqual(RecoveryClass.SOFT, self.parser.parse("quest records").recovery)


if __name__ == "__main__":
    unittest.main()
