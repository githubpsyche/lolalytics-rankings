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
import unittest
from pathlib import Path
from typing import Any


BUILD_PATH = Path(__file__).with_name("build.py")
SPEC = importlib.util.spec_from_file_location("counterpick_coverage_build", BUILD_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {BUILD_PATH}")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def matchup(win_rate: float, games: int = 100) -> dict[str, Any]:
    return {
        "opponent_name": "fixture opponent",
        "win_rate": win_rate,
        "games": games,
        "all_champs_win_rate": 50.0,
        "delta_1": win_rate - 50.0,
        "delta_2": win_rate - 50.0,
    }


def page(slug: str, name: str, matchups: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": name,
        "source_url": f"https://example.test/{slug}",
        "matchups": matchups,
    }


def roster_entry(
    slug: str, name: str, pick_rate: float, source_order: int
) -> dict[str, Any]:
    return {
        "champion_id": source_order + 1,
        "slug": slug,
        "name": name,
        "source_order": source_order,
        "pick_rate": pick_rate,
        "overall_win_rate": 50.0,
    }


def arguments(pool: list[str]) -> argparse.Namespace:
    return argparse.Namespace(
        pool=pool,
        lane="middle",
        tier="emerald_plus",
        period="30",
    )


class CoverageCalculationTests(unittest.TestCase):
    def test_fixed_weights_missing_row_and_candidate_self(self) -> None:
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
            "ahri": page(
                "ahri",
                "Ahri",
                {
                    "malzahar": matchup(60.0),
                },
            ),
            "malzahar": page("malzahar", "Malzahar", {}),
            "veigar": page(
                "veigar",
                "Veigar",
                {
                    "ahri": matchup(50.0),
                },
            ),
        }

        dataset = BUILD.build_dataset(
            arguments(["zoe"]), roster, pages, "https://example.test/tierlist"
        )
        BUILD.validate_dataset(dataset)
        opponents = {
            opponent["slug"]: opponent
            for opponent in dataset["opponent_universe"]["opponents"]
        }
        candidates = {
            candidate["slug"]: candidate for candidate in dataset["candidates"]
        }

        self.assertAlmostEqual(opponents["ahri"]["weight"], 0.6)
        self.assertAlmostEqual(opponents["malzahar"]["weight"], 0.4)
        self.assertAlmostEqual(dataset["pool_before"], 44.0)

        # Veigar improves one observed cell; the missing Malzahar cell keeps
        # its original weight and contributes zero rather than being dropped.
        self.assertAlmostEqual(candidates["veigar"]["gain"], 6.0)
        self.assertAlmostEqual(candidates["veigar"]["pool_after"], 50.0)
        self.assertAlmostEqual(candidates["veigar"]["evidence_coverage"], 0.6)
        self.assertNotIn("malzahar", candidates["veigar"]["matchups"])

        # Ahri cannot be selected against Ahri, so only Malzahar is applicable.
        self.assertEqual(candidates["ahri"]["applicable_matchups"], 1)
        self.assertAlmostEqual(candidates["ahri"]["evidence_coverage"], 1.0)
        self.assertAlmostEqual(candidates["ahri"]["gain"], 4.0)
        self.assertNotIn("ahri", candidates["ahri"]["matchups"])

    def test_multi_pool_baseline_reports_displayed_evidence(self) -> None:
        roster = [
            roster_entry("zoe", "Zoe", 40.0, 0),
            roster_entry("veigar", "Veigar", 30.0, 1),
            roster_entry("ahri", "Ahri", 20.0, 2),
            roster_entry("malzahar", "Malzahar", 10.0, 3),
            roster_entry("annie", "Annie", 5.0, 4),
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
            "veigar": page(
                "veigar",
                "Veigar",
                {
                    "ahri": matchup(45.0),
                },
            ),
            "ahri": page("ahri", "Ahri", {"malzahar": matchup(52.0)}),
            "malzahar": page("malzahar", "Malzahar", {"ahri": matchup(44.0)}),
            "annie": page(
                "annie",
                "Annie",
                {
                    "ahri": matchup(46.0),
                    "malzahar": matchup(55.0),
                },
            ),
        }

        dataset = BUILD.build_dataset(
            arguments(["zoe", "veigar"]),
            roster,
            pages,
            "https://example.test/tierlist",
        )
        BUILD.validate_dataset(dataset)
        opponents = {
            opponent["slug"]: opponent
            for opponent in dataset["opponent_universe"]["opponents"]
        }
        annie = next(
            candidate
            for candidate in dataset["candidates"]
            if candidate["slug"] == "annie"
        )

        ahri_best = opponents["ahri"]["current_best"]
        self.assertEqual(ahri_best["slug"], "veigar")
        self.assertEqual(ahri_best["pool_evidence_count"], 2)
        self.assertEqual(ahri_best["pool_size"], 2)

        # Veigar has no displayed Malzahar row, so Zoe is the best displayed
        # baseline and the 1/2 evidence count exposes that limitation.
        malzahar_best = opponents["malzahar"]["current_best"]
        self.assertEqual(malzahar_best["slug"], "zoe")
        self.assertEqual(malzahar_best["pool_evidence_count"], 1)
        self.assertEqual(malzahar_best["pool_size"], 2)

        self.assertAlmostEqual(dataset["pool_before"], 46.6666666667)
        self.assertAlmostEqual(annie["gain"], 2.3333333333)
        self.assertAlmostEqual(annie["pool_after"], 49.0)


if __name__ == "__main__":
    unittest.main()
