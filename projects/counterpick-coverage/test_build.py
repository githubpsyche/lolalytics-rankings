# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "parsel>=1.10,<2",
#   "requests>=2.32,<3",
# ]
# ///

"""Hand-checkable tests for the counterpick-coverage calculation."""

from __future__ import annotations

import argparse
import importlib.util
import math
import unittest
from pathlib import Path
from typing import Any


BUILD_PATH = Path(__file__).with_name("build.py")
SPEC = importlib.util.spec_from_file_location("counterpick_coverage_build", BUILD_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {BUILD_PATH}")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def matchup(
    win_rate: float,
    games: int = 100,
    *,
    all_champs_win_rate: float = 50.0,
    delta_2: float | None = None,
) -> dict[str, Any]:
    delta_1 = win_rate - all_champs_win_rate
    return {
        "opponent_name": "fixture opponent",
        "win_rate": win_rate,
        "games": games,
        "all_champs_win_rate": all_champs_win_rate,
        "delta_1": delta_1,
        "delta_2": delta_1 if delta_2 is None else delta_2,
    }


def page(slug: str, name: str, matchups: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": name,
        "source_url": f"https://example.test/{slug}",
        "matchups": matchups,
    }


def roster_entry(
    slug: str,
    name: str,
    pick_rate: float,
    source_order: int,
    overall_win_rate: float = 50.0,
) -> dict[str, Any]:
    return {
        "champion_id": source_order + 1,
        "slug": slug,
        "name": name,
        "source_order": source_order,
        "pick_rate": pick_rate,
        "overall_win_rate": overall_win_rate,
    }


def arguments(
    base: str = "zoe", candidate: str = "veigar"
) -> argparse.Namespace:
    return argparse.Namespace(
        base=base,
        candidate=candidate,
        lane="middle",
        tier="emerald_plus",
        period="30",
    )


def four_champion_fixture() -> dict[str, Any]:
    """Return a sparse matrix in which every opponent still has an all-WR."""
    roster = [
        roster_entry("zoe", "Zoe", 40.0, 0),
        roster_entry("ahri", "Ahri", 30.0, 1),
        roster_entry("malzahar", "Malzahar", 20.0, 2),
        roster_entry("veigar", "Veigar", 10.0, 3),
    ]
    pages = {
        "zoe": page(
            "zoe",
            "Zoe",
            {
                "ahri": matchup(40.0),
                "malzahar": matchup(50.0),
            },
        ),
        # Zoe appears here, supplying the all-champions baseline needed to
        # model missing cells whose opponent is Zoe.
        "ahri": page(
            "ahri",
            "Ahri",
            {
                "zoe": matchup(50.0),
                "malzahar": matchup(60.0),
            },
        ),
        # Veigar appears here for the same complete-baseline reason.
        "malzahar": page(
            "malzahar",
            "Malzahar",
            {
                "ahri": matchup(45.0),
                "veigar": matchup(50.0),
            },
        ),
        "veigar": page(
            "veigar",
            "Veigar",
            {
                "ahri": matchup(50.0),
            },
        ),
    }
    return BUILD.build_dataset(
        arguments(), roster, pages, "https://example.test/tierlist"
    )


class PosteriorCalculationTests(unittest.TestCase):
    def test_direct_prior_center_is_observed_minus_delta_2(self) -> None:
        row = matchup(60.0, delta_2=8.0)
        self.assertEqual(BUILD.observed_prior_center(row), 52.0)

    def test_complete_prior_combines_opponent_all_wr_and_median_strength(self) -> None:
        dataset = {
            "champions": [
                {
                    "slug": "candidate",
                    "name": "Candidate",
                    "matchups": {
                        "opponent": matchup(
                            55.0,
                            all_champs_win_rate=50.0,
                            delta_2=2.0,
                        )
                    },
                },
                {
                    "slug": "opponent",
                    "name": "Opponent",
                    "matchups": {
                        "candidate": matchup(47.0),
                    },
                },
            ]
        }
        adjustments = BUILD.champion_strength_adjustments(dataset)
        opponent_rates = BUILD.opponent_all_champs_rates(dataset)
        model = {
            "champion_strength_adjustments": adjustments,
            "opponent_all_champs_win_rates": opponent_rates,
        }

        self.assertAlmostEqual(adjustments["candidate"], 3.0)
        self.assertAlmostEqual(opponent_rates["opponent"], 50.0)
        self.assertAlmostEqual(
            BUILD.complete_prior_center("candidate", "opponent", model),
            53.0,
        )

    def test_posterior_mean_is_hand_checkable(self) -> None:
        summary = BUILD.beta_posterior_summary(
            win_rate=60.0,
            games=100,
            prior_center=50.0,
            concentration=100.0,
        )

        # Approximate observations are 60 wins / 40 losses. Adding a
        # Beta(50, 50) prior gives Beta(110, 90), whose mean is 55%.
        self.assertEqual(summary["approximate_wins"], 60)
        self.assertAlmostEqual(summary["alpha"], 110.0)
        self.assertAlmostEqual(summary["beta"], 90.0)
        self.assertAlmostEqual(summary["mean"], 55.0)
        self.assertAlmostEqual(
            summary["variance"],
            110 * 90 / (200**2 * 201) * 10_000,
        )

    def test_posterior_mean_uses_the_disclosed_displayed_rate_formula(self) -> None:
        summary = BUILD.beta_posterior_summary(
            win_rate=52.37,
            games=137,
            prior_center=49.25,
            concentration=83.0,
        )
        expected = (137 * 52.37 + 83 * 49.25) / (137 + 83)

        self.assertAlmostEqual(summary["mean"], expected)
        self.assertEqual(summary["approximate_wins"], 72)

    def test_more_games_shrink_less_and_narrow_the_interval(self) -> None:
        small = BUILD.beta_posterior_summary(
            win_rate=60.0,
            games=100,
            prior_center=50.0,
            concentration=100.0,
        )
        large = BUILD.beta_posterior_summary(
            win_rate=60.0,
            games=1_000,
            prior_center=50.0,
            concentration=100.0,
        )

        self.assertAlmostEqual(small["mean"], 55.0)
        self.assertAlmostEqual(large["mean"], 650 / 1_100 * 100)
        self.assertGreater(large["mean"], small["mean"])
        self.assertLess(
            large["interval"]["upper"] - large["interval"]["lower"],
            small["interval"]["upper"] - small["interval"]["lower"],
        )

    def test_zero_concentration_recovers_the_approximate_observed_rate(self) -> None:
        summary = BUILD.beta_posterior_summary(
            win_rate=60.0,
            games=100,
            prior_center=25.0,
            concentration=0.0,
        )
        self.assertAlmostEqual(summary["mean"], 60.0)

    def test_zero_matchup_delta_leaves_the_point_estimate_unchanged(self) -> None:
        summary = BUILD.beta_posterior_summary(
            win_rate=52.0,
            games=250,
            prior_center=52.0,
            concentration=100.0,
        )
        self.assertAlmostEqual(summary["mean"], 52.0)

    def test_prior_only_cell_has_zero_games_and_uses_complete_center(self) -> None:
        dataset = four_champion_fixture()
        model = BUILD.build_prior_model(dataset, concentration=100.0)
        zoe = next(
            champion
            for champion in dataset["champions"]
            if champion["slug"] == "zoe"
        )
        estimate = BUILD.matchup_estimate(zoe, "veigar", model)

        self.assertEqual(estimate["status"], "modeled_only")
        self.assertEqual(estimate["games"], 0)
        self.assertIsNone(estimate["raw_win_rate"])
        self.assertEqual(
            estimate["prior_center_source"],
            "opponent_all_wr_plus_champion_strength",
        )
        self.assertAlmostEqual(estimate["prior_center"], 50.0)
        self.assertAlmostEqual(estimate["adjusted_win_rate"], 50.0)
        self.assertAlmostEqual(estimate["matchup_effect"], 0.0)
        self.assertIsNone(estimate["raw_delta_2"])
        self.assertAlmostEqual(estimate["data_weight"], 0.0)

    def test_matchup_effect_is_shrunk_delta_2(self) -> None:
        dataset = four_champion_fixture()
        model = BUILD.build_prior_model(dataset, concentration=100.0)
        zoe = next(
            champion
            for champion in dataset["champions"]
            if champion["slug"] == "zoe"
        )
        estimate = BUILD.matchup_estimate(zoe, "ahri", model)

        # Zoe's direct row is 40% with Delta 2=-10 over 100 games. With
        # k=100, half of that normalized matchup effect remains.
        self.assertAlmostEqual(estimate["raw_delta_2"], -10.0)
        self.assertAlmostEqual(estimate["data_weight"], 0.5)
        self.assertAlmostEqual(estimate["matchup_effect"], -5.0)
        self.assertAlmostEqual(
            estimate["adjusted_win_rate"],
            estimate["strength_expectation"] + estimate["matchup_effect"],
        )

    def test_absolute_and_complementarity_scores_are_distinct(self) -> None:
        base = {
            "status": "direct",
            "raw_win_rate": 55.0,
            "raw_delta_2": 5.0,
            "strength_expectation": 50.0,
            "adjusted_win_rate": 55.0,
            "matchup_effect": 5.0,
        }
        generally_strong = {
            "status": "direct",
            "raw_win_rate": 60.0,
            "raw_delta_2": 0.0,
            "strength_expectation": 60.0,
            "adjusted_win_rate": 60.0,
            "matchup_effect": 0.0,
        }
        specialist = {
            "status": "direct",
            "raw_win_rate": 54.0,
            "raw_delta_2": 6.0,
            "strength_expectation": 48.0,
            "adjusted_win_rate": 54.0,
            "matchup_effect": 6.0,
        }

        strong_absolute = BUILD.score_matchup_estimates(
            base, generally_strong, 0.5, "absolute"
        )
        strong_complementarity = BUILD.score_matchup_estimates(
            base, generally_strong, 0.5, "complementarity"
        )
        specialist_absolute = BUILD.score_matchup_estimates(
            base, specialist, 0.5, "absolute"
        )
        specialist_complementarity = BUILD.score_matchup_estimates(
            base, specialist, 0.5, "complementarity"
        )

        self.assertAlmostEqual(strong_absolute["contribution"], 2.5)
        self.assertAlmostEqual(strong_complementarity["contribution"], 0.0)
        self.assertAlmostEqual(specialist_absolute["contribution"], 0.0)
        self.assertAlmostEqual(
            specialist_complementarity["contribution"], 0.5
        )
        self.assertAlmostEqual(
            strong_absolute["strength_contribution"]
            + strong_absolute["matchup_contribution"],
            strong_absolute["contribution"],
        )
        self.assertLess(strong_absolute["matchup_contribution"], 0.0)

    def test_global_empirical_bayes_fit_is_positive_and_order_invariant(self) -> None:
        dataset = four_champion_fixture()
        fitted = BUILD.fit_global_prior_concentration(dataset)
        reversed_dataset = dataset | {
            "champions": list(reversed(dataset["champions"]))
        }
        reversed_fitted = BUILD.fit_global_prior_concentration(reversed_dataset)

        self.assertTrue(math.isfinite(fitted))
        self.assertGreater(fitted, 0)
        self.assertAlmostEqual(fitted, reversed_fitted)

    def test_fixed_assignment_gain_interval_matches_visible_components(self) -> None:
        interval = BUILD.fixed_assignment_gain_interval(
            [
                {
                    "weight": 0.5,
                    "improvement": 4.0,
                    "base_variance": 1.0,
                    "candidate_variance": 3.0,
                },
                {
                    "weight": 0.5,
                    "improvement": 0.0,
                    "base_variance": 100.0,
                    "candidate_variance": 100.0,
                },
            ]
        )

        # Only the fixed winning assignment participates: mean=.5*4=2 and
        # variance=.5^2*(1+3)=1. The losing row cannot create Jensen uplift.
        self.assertAlmostEqual(interval["mean"], 2.0)
        self.assertAlmostEqual(interval["variance"], 1.0)
        self.assertAlmostEqual(
            interval["lower"],
            2.0 - BUILD.NormalDist().inv_cdf(0.95),
        )

        crossing = BUILD.fixed_assignment_gain_interval(
            [
                {
                    "weight": 1.0,
                    "improvement": 0.1,
                    "base_variance": 1.0,
                    "candidate_variance": 1.0,
                }
            ]
        )
        self.assertLess(crossing["lower"], 0.0)


class CoverageCalculationTests(unittest.TestCase):
    def test_one_template_renders_distinct_linked_pages_and_preserves_raw(self) -> None:
        dataset = four_champion_fixture()

        rankings = BUILD.render_page(dataset, "rankings")
        pair = BUILD.render_page(dataset, "pair")
        projected = BUILD.page_dataset(dataset)

        self.assertIn('data-page-kind="rankings"', rankings)
        self.assertIn('data-page-kind="pair"', pair)
        self.assertIn("pair/?base=", rankings)
        self.assertNotIn(BUILD.DATA_MARKER, rankings)
        self.assertNotIn(BUILD.PAGE_MARKER, rankings)
        self.assertEqual(
            projected["champions"][0]["matchups"]["ahri"]["delta_2"],
            -10.0,
        )
        self.assertNotIn(
            "all_champs_win_rate",
            projected["champions"][0]["matchups"]["ahri"],
        )
        self.assertNotIn(
            "delta_1",
            projected["champions"][0]["matchups"]["ahri"],
        )
        self.assertEqual(projected["champions"][0]["overall_win_rate"], 50.0)
        self.assertIn("concentration", projected["prior_model"])
        with self.assertRaisesRegex(BUILD.ScrapeError, "Unknown page kind"):
            BUILD.render_page(dataset, "other")

    def test_common_universe_missing_rows_mirror_and_coverage(self) -> None:
        dataset = four_champion_fixture()
        BUILD.validate_dataset(dataset)
        analysis = BUILD.analyze_singleton_pool(
            dataset, "zoe", concentration=100.0
        )
        opponents = {
            opponent["slug"]: opponent
            for opponent in analysis["opponent_universe"]["opponents"]
        }
        candidates = {
            candidate["slug"]: candidate for candidate in analysis["candidates"]
        }
        veigar = candidates["veigar"]

        # The common roster is every eligible lane champion, not only Zoe's
        # displayed rows: Ahri=.5, Malzahar=1/3, Veigar=1/6.
        self.assertEqual(analysis["opponent_universe"]["common_count"], 3)
        self.assertAlmostEqual(opponents["ahri"]["weight"], 0.5)
        self.assertAlmostEqual(opponents["malzahar"]["weight"], 1 / 3)
        self.assertAlmostEqual(opponents["veigar"]["weight"], 1 / 6)
        self.assertEqual(
            opponents["veigar"]["base_estimate"]["status"], "modeled_only"
        )
        self.assertAlmostEqual(analysis["pool_before"], 47.5)
        self.assertAlmostEqual(
            analysis["opponent_universe"]["base_direct_weight"], 5 / 6
        )

        # With k=100, Zoe's 40% observed Ahri row adjusts to 45%; Veigar's
        # 50% row remains 50%, yielding .5*(50-45)=2.5 percentage points.
        self.assertAlmostEqual(veigar["gain"], 2.5)
        self.assertAlmostEqual(veigar["observed_only_gain"], 5.0)
        self.assertAlmostEqual(veigar["pool_after"], 50.0)
        self.assertAlmostEqual(veigar["gain_interval"]["mean"], veigar["gain"])
        self.assertEqual(veigar["matchups"]["malzahar"]["status"], "modeled_only")
        self.assertEqual(
            veigar["matchups"]["veigar"]["status"], "unavailable_mirror"
        )
        self.assertEqual(veigar["observed_matchups"], 1)
        self.assertEqual(veigar["modeled_matchups"], 1)
        self.assertEqual(veigar["unavailable_matchups"], 1)
        self.assertAlmostEqual(veigar["evidence_coverage"], 0.6)
        self.assertEqual(veigar["coverage"]["joint_direct_rows"], 1)
        self.assertAlmostEqual(veigar["coverage"]["joint_direct_weight"], 0.6)
        self.assertAlmostEqual(veigar["direct_contribution_share"], 1.0)

    def test_both_score_modes_sum_and_drive_their_own_rank_order(self) -> None:
        dataset = four_champion_fixture()
        analysis = BUILD.analyze_singleton_pool(
            dataset, "zoe", concentration=100.0
        )

        default_ranks = [
            candidate["scores"][BUILD.DEFAULT_SCORE_MODE]["rank"]
            for candidate in analysis["candidates"]
        ]
        self.assertEqual(default_ranks, sorted(default_ranks))
        for candidate in analysis["candidates"]:
            for mode in BUILD.SCORE_MODES:
                score = candidate["scores"][mode]
                contribution_sum = math.fsum(
                    row["scores"][mode]["contribution"]
                    for row in candidate["matchups"].values()
                )
                self.assertAlmostEqual(contribution_sum, score["gain"])
                raw_contribution_sum = math.fsum(
                    row["scores"][mode]["raw_contribution"]
                    for row in candidate["matchups"].values()
                    if row["scores"][mode]["raw_contribution"] is not None
                )
                self.assertAlmostEqual(
                    raw_contribution_sum,
                    score["observed_only_gain"],
                )
                self.assertAlmostEqual(
                    score["pool_after"],
                    analysis["pool_before_by_mode"][mode] + score["gain"],
                )
                self.assertAlmostEqual(
                    score["gain_interval"]["mean"], score["gain"]
                )
                self.assertEqual(
                    score["rank"],
                    analysis["rank_by_mode"][mode][candidate["slug"]],
                )
            absolute = candidate["scores"]["absolute"]
            self.assertAlmostEqual(
                absolute["strength_contribution"]
                + absolute["matchup_contribution"],
                absolute["gain"],
            )

    def test_exact_score_ties_share_a_rank(self) -> None:
        dataset = four_champion_fixture()
        for champion in dataset["champions"]:
            for row in champion["matchups"].values():
                row.update(
                    {
                        "win_rate": 50.0,
                        "all_champs_win_rate": 50.0,
                        "delta_1": 0.0,
                        "delta_2": 0.0,
                    }
                )

        analysis = BUILD.analyze_singleton_pool(
            dataset, "zoe", concentration=100.0
        )

        for mode in BUILD.SCORE_MODES:
            self.assertEqual(
                {
                    candidate["scores"][mode]["rank"]
                    for candidate in analysis["candidates"]
                },
                {1},
            )


if __name__ == "__main__":
    unittest.main()
