from __future__ import annotations

import unittest

from tests.support import PROJECT_ROOT  # noqa: F401 - establishes source path

from beta_earth.application.selection import (
    RelativeSelector,
    Scope,
    parse_selection,
)


class SelectionGrammarTests(unittest.TestCase):
    def test_numeric_and_word_ordinals_are_zero_based_internally(self) -> None:
        self.assertEqual(1, parse_selection("second blade").ordinal)
        self.assertEqual(11, parse_selection("blade #12").ordinal)
        self.assertEqual("blade", parse_selection("12th blade").terms)

    def test_scope_qualifiers_are_removed_from_search_terms(self) -> None:
        carried = parse_selection("my transit token")
        self.assertEqual(Scope.INVENTORY, carried.scope)
        self.assertEqual("transit token", carried.terms)
        nearby = parse_selection("token here")
        self.assertEqual(Scope.ROOM, nearby.scope)
        self.assertEqual("token", nearby.terms)

    def test_all_except_is_a_bounded_multi_selection_shape(self) -> None:
        query = parse_selection("all except field coat")
        self.assertTrue(query.all_matches)
        self.assertEqual("", query.terms)
        self.assertEqual("field coat", query.exclusion)
        self.assertEqual("", parse_selection("all except").exclusion)

    def test_relative_and_pronoun_selectors_are_explicit(self) -> None:
        self.assertEqual(
            RelativeSelector.NEXT,
            parse_selection("next mite").relative,
        )
        self.assertTrue(parse_selection("it").pronoun)


if __name__ == "__main__":
    unittest.main()
