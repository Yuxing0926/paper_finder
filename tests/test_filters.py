"""Offline unit tests for pf.filters."""

from __future__ import annotations

import unittest

from pf.filters import (
    auto_groups,
    is_pv_related,
    matches_any,
    matches_groups,
    normalize,
)


class TestNormalize(unittest.TestCase):
    def test_lowercase_and_fold_separators(self):
        self.assertEqual(normalize("SnO2:F Thin-Film"), "sno2 f thin film")

    def test_collapses_punctuation_runs(self):
        self.assertEqual(normalize("Al-doped  ZnO /// (AZO)"), "al doped zno azo")

    def test_empty(self):
        self.assertEqual(normalize(""), "")


class TestTermMatching(unittest.TestCase):
    def test_short_acronym_does_not_match_inside_word(self):
        # "ito" appears inside "composition" but is not a token.
        self.assertFalse(matches_groups("Composition of thin films", [["ito"]]))

    def test_short_acronym_matches_as_token(self):
        self.assertTrue(matches_groups("ITO films for solar cells", [["ito"]]))

    def test_prefix_term(self):
        self.assertTrue(matches_groups("Highly conductive films", [["conduc*"]]))
        self.assertTrue(matches_groups("High conductivity oxide", [["conduc*"]]))
        self.assertFalse(matches_groups("Unrelated topic entirely", [["conduc*"]]))

    def test_multiword_term(self):
        self.assertTrue(matches_groups("Indium tin oxide films", [["indium tin oxide"]]))
        self.assertFalse(matches_groups("Indium and tin oxide", [["indium tin oxide"]]))

    def test_sno2_f_matches_fluorine_doped_tin_oxide(self):
        self.assertTrue(matches_groups("SnO2:F coated glass", [["sno2 f"]]))


class TestGroups(unittest.TestCase):
    def test_groups_are_anded(self):
        groups = [["transparent*"], ["conduc*", "electrode*"]]
        self.assertTrue(matches_groups("Transparent conductive oxide", groups))
        self.assertTrue(matches_groups("Transparent electrode stack", groups))
        self.assertFalse(matches_groups("Transparent glass", groups))

    def test_terms_inside_group_are_ored(self):
        groups = [["transparent*", "tco"], ["conduc*"]]
        self.assertTrue(matches_groups("TCO conductivity", groups))

    def test_empty_groups_match_everything(self):
        self.assertTrue(matches_groups("anything at all", []))

    def test_tco_filter_keeps_paper_without_oxide_word(self):
        # Regression: requiring an "oxide" group dropped 149 PV papers.
        groups = [["transparent*", "tco", "ito"], ["conduc*", "electrode*"]]
        title = "Transparent Conductive Nanofiber Paper for Foldable Solar Cells"
        self.assertTrue(matches_groups(title, groups))

    def test_tco_filter_rejects_ionic_conductor(self):
        groups = [["transparent*", "tco", "ito"], ["conduc*", "electrode*"]]
        title = "Proton Conducting Solid Oxide Fuel Cell Degradation"
        self.assertFalse(matches_groups(title, groups))


class TestPvFlag(unittest.TestCase):
    def test_detects_solar(self):
        self.assertTrue(is_pv_related("ITO for solar cells", "", ["solar", "cell"]))

    def test_detects_in_abstract_only(self):
        self.assertTrue(
            is_pv_related("TCO films", "used as front contact in perovskite devices",
                          ["perovskit*"])
        )

    def test_neutral_title_is_not_flagged(self):
        self.assertFalse(is_pv_related("TCO for antistatic coatings", "", ["solar", "pv"]))

    def test_matches_any_helper(self):
        self.assertTrue(matches_any("Photovoltaic applications", ["photovolta*"]))
        self.assertFalse(matches_any("Photography", ["photovolta*"]))


class TestAutoGroups(unittest.TestCase):
    def test_splits_into_single_word_groups(self):
        self.assertEqual(
            auto_groups("transparent conductive oxide"),
            [["transparent"], ["conductive"], ["oxide"]],
        )

    def test_drops_stopwords(self):
        self.assertEqual(auto_groups("the effect of doping"), [["effect"], ["doping"]])


if __name__ == "__main__":
    unittest.main()