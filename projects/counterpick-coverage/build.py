# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "parsel>=1.10,<2",
#   "requests>=2.32,<3",
# ]
# ///

"""Scrape Lolalytics matchup data and build the counterpick-coverage page."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from parsel import Selector


PROJECT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = PROJECT_ROOT.parents[1]
TEMPLATE_PATH = PROJECT_ROOT / "template.html"
LATEST_PATH = PROJECT_ROOT / "data" / "latest.json"
ARCHIVE_DIR = PROJECT_ROOT / "data" / "archive"
RANKINGS_REPORT_PATH = (
    REPOSITORY_ROOT / "docs" / "counterpick-coverage" / "index.html"
)
PAIR_REPORT_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "counterpick-coverage"
    / "pair"
    / "index.html"
)
DATA_MARKER = "__COUNTERPICK_COVERAGE_DATA__"
PAGE_MARKER = "__COUNTERPICK_COVERAGE_PAGE__"

BASE_URL = "https://lolalytics.com/lol"
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.25
REQUEST_ATTEMPTS = 3

LANES = ("top", "jungle", "middle", "bottom", "support")
LANE_LABELS = {
    "top": "Top",
    "jungle": "Jungle",
    "middle": "Middle",
    "bottom": "Bottom",
    "support": "Support",
}
TIERS = (
    "all",
    "1trick",
    "challenger",
    "grandmaster",
    "grandmaster_plus",
    "master",
    "master_plus",
    "diamond",
    "d2_plus",
    "diamond_plus",
    "emerald",
    "emerald_plus",
    "platinum",
    "platinum_plus",
    "gold",
    "gold_plus",
    "silver",
    "bronze",
    "iron",
    "unranked",
)


class ScrapeError(RuntimeError):
    """Raised when source data are incomplete or no longer match expectations."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rank second-champion additions to any singleton pool by observed "
            "marginal counterpick coverage."
        )
    )
    parser.add_argument("--lane", choices=LANES, default="middle")
    parser.add_argument(
        "--base",
        "--pool",
        dest="base",
        default="zoe",
        metavar="SLUG",
        help="initial current-champion slug (default: zoe)",
    )
    parser.add_argument(
        "--candidate",
        default="zilean",
        metavar="SLUG",
        help="initial champion-to-add slug (default: zilean)",
    )
    parser.add_argument("--tier", choices=TIERS, default="emerald_plus")
    parser.add_argument(
        "--period",
        default="30",
        help='current, 7, 14, 30, or a patch such as "16.14" (default: 30)',
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="rebuild the HTML from data/latest.json without scraping",
    )
    args = parser.parse_args()

    if args.period not in {"current", "7", "14", "30"} and not re.fullmatch(
        r"\d{1,2}\.\d{1,2}", args.period
    ):
        parser.error("--period must be current, 7, 14, 30, or a patch such as 16.14")

    for argument in ("base", "candidate"):
        slug = getattr(args, argument).strip().lower()
        if not re.fullmatch(r"[a-z0-9]+", slug):
            parser.error(
                f"--{argument} must be one lowercase Lolalytics champion slug"
            )
        setattr(args, argument, slug)
    if args.base == args.candidate:
        parser.error("--base and --candidate must be different champions")
    return args


def query_params(
    args: argparse.Namespace, *, include_opponent_lane: bool = False
) -> dict[str, str]:
    params = {"lane": args.lane, "tier": args.tier}
    if include_opponent_lane:
        params["vslane"] = args.lane
    else:
        params["region"] = "all"
    if args.period != "current":
        params["patch"] = args.period
    return params


def build_url(path: str, params: dict[str, str]) -> str:
    return f"{BASE_URL}/{path}/?{urlencode(params)}"


def fetch(session: requests.Session, url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(REQUEST_ATTEMPTS):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            if not response.text.strip():
                raise ScrapeError(f"Empty response from {url}")
            return response.text
        except (requests.RequestException, ScrapeError) as error:
            last_error = error
            if attempt + 1 < REQUEST_ATTEMPTS:
                time.sleep(2**attempt)
    raise ScrapeError(f"Could not fetch {url}: {last_error}") from last_error


def resolve_qwik_reference(objects: list[Any], reference: Any) -> Any:
    if not isinstance(reference, str):
        return None
    try:
        index = int(reference, 36)
    except ValueError:
        return None
    if 0 <= index < len(objects):
        return objects[index]
    return None


def resolved_row_value(objects: list[Any], row: dict[str, Any], key: str) -> Any:
    if key not in row:
        raise ScrapeError(f"Tier-list row is missing {key!r}")
    value = resolve_qwik_reference(objects, row[key])
    if value is None:
        raise ScrapeError(f"Tier-list value {key!r} could not be resolved")
    return value


def parse_number(value: Any, label: str) -> float:
    normalized = str(value).replace(",", "").replace("%", "").strip()
    try:
        number = float(normalized)
    except ValueError as error:
        raise ScrapeError(f"{label} has a non-numeric value: {value!r}") from error
    if not math.isfinite(number):
        raise ScrapeError(f"{label} is not finite: {value!r}")
    return number


def extract_roster(html: str) -> list[dict[str, Any]]:
    selector = Selector(html)
    payload_text = selector.css('script[type="qwik/json"]::text').get()
    if not payload_text:
        raise ScrapeError("Tier-list page is missing its Qwik data")

    try:
        objects = json.loads(payload_text)["objs"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ScrapeError("Tier-list Qwik data is not readable") from error
    if not isinstance(objects, list):
        raise ScrapeError("Tier-list Qwik object collection is invalid")

    rows = [
        item
        for item in objects
        if isinstance(item, dict) and {"cid", "row", "placeholder"}.issubset(item)
    ]
    champion_ids = [resolve_qwik_reference(objects, row["cid"]) for row in rows]
    if not champion_ids or not all(isinstance(cid, int) for cid in champion_ids):
        raise ScrapeError("Could not identify champions in the tier list")
    if len(champion_ids) != len(set(champion_ids)):
        raise ScrapeError("Tier list contains duplicate champion rows")

    roster_ids = set(champion_ids)
    best_slug_map: dict[int, str] = {}
    for item in objects:
        if not isinstance(item, dict) or len(item) < len(roster_ids):
            continue
        candidate: dict[int, str] = {}
        for slug, reference in item.items():
            if (
                not isinstance(slug, str)
                or slug.isdigit()
                or not re.fullmatch(r"[a-z0-9]+", slug)
            ):
                continue
            champion_id = resolve_qwik_reference(objects, reference)
            if isinstance(champion_id, int) and champion_id in roster_ids:
                candidate[champion_id] = slug
        if len(candidate) > len(best_slug_map):
            best_slug_map = candidate

    missing_ids = roster_ids - best_slug_map.keys()
    if missing_ids:
        raise ScrapeError(
            f"Could not resolve {len(missing_ids)} tier-list champions to URLs"
        )

    roster_slugs = set(best_slug_map.values())
    best_name_map: dict[str, str] = {}
    best_name_score = -1
    for item in objects:
        if not isinstance(item, dict) or len(item) < len(roster_slugs):
            continue
        candidate = {
            slug: resolved
            for slug, reference in item.items()
            if slug in roster_slugs
            and isinstance(resolved := resolve_qwik_reference(objects, reference), str)
        }
        if len(candidate) < len(roster_slugs):
            continue
        score = sum(
            re.sub(r"[^a-z0-9]", "", name.lower()) == slug
            for slug, name in candidate.items()
        )
        if score > best_name_score:
            best_name_map = candidate
            best_name_score = score
    missing_names = roster_slugs - best_name_map.keys()
    if missing_names:
        raise ScrapeError(
            f"Could not resolve {len(missing_names)} tier-list champion names"
        )

    roster = []
    for source_order, (champion_id, outer_row) in enumerate(
        zip(champion_ids, rows, strict=True)
    ):
        row = resolve_qwik_reference(objects, outer_row["row"])
        if not isinstance(row, dict):
            raise ScrapeError("Tier-list champion row could not be resolved")
        roster.append(
            {
                "champion_id": champion_id,
                "slug": best_slug_map[champion_id],
                "name": best_name_map[best_slug_map[champion_id]],
                "source_order": source_order,
                "pick_rate": parse_number(
                    resolved_row_value(objects, row, "pr"), "Pick rate"
                ),
                "overall_win_rate": parse_number(
                    resolved_row_value(objects, row, "wr"), "Win rate"
                ),
            }
        )
    return roster


def extract_matchup_page(
    html: str,
    champion: dict[str, Any],
    roster_by_id: dict[int, dict[str, Any]],
    args: argparse.Namespace,
    source_url: str,
) -> dict[str, Any]:
    selector = Selector(html)
    payload_text = selector.css('script[type="qwik/json"]::text').get()
    if not payload_text:
        raise ScrapeError(f"{champion['name']}: counter page has no Qwik data")
    try:
        objects = json.loads(payload_text)["objs"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ScrapeError(
            f"{champion['name']}: counter-page Qwik data is not readable"
        ) from error
    if not isinstance(objects, list):
        raise ScrapeError(
            f"{champion['name']}: counter-page Qwik collection is invalid"
        )

    context_keys = {
        "path",
        "page",
        "lane",
        "mode",
        "patch",
        "tier",
        "region",
        "status",
    }
    raw_contexts = [
        item
        for item in objects
        if isinstance(item, dict) and context_keys.issubset(item)
    ]
    contexts = [
        {
            key: resolve_qwik_reference(objects, item[key])
            for key in (*context_keys, "vsLane")
            if key in item
        }
        for item in raw_contexts
    ]
    matching_contexts = [
        context
        for context in contexts
        if context.get("path") == f"/lol/{champion['slug']}/counters/"
        and context.get("page") == "counters"
    ]
    if not matching_contexts:
        raise ScrapeError(
            f"{champion['name']}: could not find the expected page context"
        )
    context = matching_contexts[0]
    expected_context = {
        "path": f"/lol/{champion['slug']}/counters/",
        "page": "counters",
        "lane": args.lane,
        "vsLane": args.lane,
        "mode": "ranked",
        "tier": args.tier,
        "region": "all",
        "status": 200,
    }
    mismatches = {
        key: (context.get(key), expected)
        for key, expected in expected_context.items()
        if context.get(key) != expected
    }
    if args.period != "current" and context.get("patch") != args.period:
        mismatches["patch"] = (context.get("patch"), args.period)
    if mismatches:
        details = ", ".join(
            f"{key}={actual!r} (expected {expected!r})"
            for key, (actual, expected) in mismatches.items()
        )
        raise ScrapeError(f"{champion['name']}: wrong page context: {details}")

    row_keys = {"cid", "vsWr", "n", "d1", "d2", "allWr", "defaultLane"}
    rows = [
        item for item in objects if isinstance(item, dict) and row_keys.issubset(item)
    ]
    if not rows:
        raise ScrapeError(f"{champion['name']}: no displayed matchup rows found")
    matchups: dict[str, dict[str, Any]] = {}
    for row in rows:
        resolved = {
            key: resolve_qwik_reference(objects, value) for key, value in row.items()
        }
        opponent_id = resolved["cid"]
        if not isinstance(opponent_id, int):
            raise ScrapeError(f"{champion['name']}: matchup champion ID is invalid")
        opponent = roster_by_id.get(opponent_id)
        if opponent is None:
            continue
        opponent_slug = opponent["slug"]
        if opponent_slug in matchups:
            raise ScrapeError(
                f"{champion['name']}: duplicate matchup row for {opponent_slug}"
            )

        win_rate = parse_number(
            resolved["vsWr"],
            f"{champion['name']} vs {opponent['name']} win rate",
        )
        all_champs_win_rate = parse_number(
            resolved["allWr"],
            f"All champions vs {opponent['name']} win rate",
        )
        games = parse_number(
            resolved["n"], f"{champion['name']} vs {opponent['name']} games"
        )
        if not 0 <= win_rate <= 100 or not 0 <= all_champs_win_rate <= 100:
            raise ScrapeError(
                f"{champion['name']} vs {opponent['name']}: win rate is out of range"
            )
        if not games.is_integer() or games <= 0:
            raise ScrapeError(
                f"{champion['name']} vs {opponent['name']}: games are invalid"
            )

        matchups[opponent_slug] = {
            "opponent_name": opponent["name"],
            "win_rate": win_rate,
            "games": int(games),
            "all_champs_win_rate": all_champs_win_rate,
            "delta_1": parse_number(resolved["d1"], "Delta 1"),
            "delta_2": parse_number(resolved["d2"], "Delta 2"),
        }

    return {
        "slug": champion["slug"],
        "name": champion["name"],
        "source_url": source_url,
        "matchups": matchups,
    }


def round_value(value: float) -> float:
    return round(value, 10)


def build_dataset(
    args: argparse.Namespace,
    roster: list[dict[str, Any]],
    pages: dict[str, dict[str, Any]],
    tierlist_url: str,
) -> dict[str, Any]:
    roster_by_slug = {champion["slug"]: champion for champion in roster}
    unknown_defaults = [
        slug for slug in (args.base, args.candidate) if slug not in roster_by_slug
    ]
    if unknown_defaults:
        raise ScrapeError(
            "Default champions are not in the selected lane roster: "
            + ", ".join(unknown_defaults)
        )

    missing_pages = [
        champion["slug"] for champion in roster if champion["slug"] not in pages
    ]
    if missing_pages:
        raise ScrapeError(
            "Matchup pages are missing for: " + ", ".join(missing_pages)
        )

    champions = []
    for roster_entry in roster:
        slug = roster_entry["slug"]
        page = pages[slug]
        matchups = {
            opponent_slug: {
                key: matchup[key]
                for key in (
                    "win_rate",
                    "games",
                    "all_champs_win_rate",
                    "delta_1",
                    "delta_2",
                )
            }
            for opponent_slug, matchup in page["matchups"].items()
            if opponent_slug in roster_by_slug and opponent_slug != slug
        }
        champions.append(
            {
                "slug": slug,
                "name": page["name"],
                "source_order": roster_entry["source_order"],
                "source_url": page["source_url"],
                "overall_win_rate": roster_entry["overall_win_rate"],
                "pick_rate": roster_entry["pick_rate"],
                "matchups": matchups,
            }
        )

    generated_at = datetime.now(UTC)
    return {
        "schema_version": 2,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "filters": {
            "lane": args.lane,
            "lane_label": LANE_LABELS[args.lane],
            "tier": args.tier,
            "region": "all",
            "period": args.period,
            "queue": "Ranked Solo/Duo",
        },
        "source": {
            "name": "Lolalytics",
            "tierlist_url": tierlist_url,
            "display_threshold_games": 100,
        },
        "method": {
            "question": "expand_singleton_pool_to_pair",
            "estimate": "observed_matchup_win_rate",
            "opponent_universe": "displayed_matchups_for_selected_base",
            "opponent_weight": "lane_pick_rate_normalized_over_base_universe",
            "missing_candidate_matchup": "zero_demonstrated_improvement",
            "candidate_self_matchup": "unavailable",
        },
        "defaults": {
            "base_slug": args.base,
            "candidate_slug": args.candidate,
        },
        "champions": champions,
    }


def analyze_singleton_pool(
    dataset: dict[str, Any], base_slug: str
) -> dict[str, Any]:
    champion_by_slug = {
        champion["slug"]: champion for champion in dataset["champions"]
    }
    base = champion_by_slug.get(base_slug)
    if base is None:
        raise ScrapeError(f"Unknown current champion: {base_slug}")

    raw_opponents = [
        (champion_by_slug[slug], matchup)
        for slug, matchup in base["matchups"].items()
        if slug in champion_by_slug and slug != base_slug
    ]
    raw_opponents.sort(key=lambda entry: entry[0]["source_order"])
    if not raw_opponents:
        raise ScrapeError(f"{base['name']} has no displayed matchup universe")

    universe_pick_rate = math.fsum(
        opponent["pick_rate"] for opponent, _ in raw_opponents
    )
    if universe_pick_rate <= 0:
        raise ScrapeError(f"{base['name']} has no opponent pick-rate weight")

    eligible_pick_rate = math.fsum(
        champion["pick_rate"]
        for champion in dataset["champions"]
        if champion["slug"] != base_slug
    )
    if eligible_pick_rate <= 0:
        raise ScrapeError("The eligible opponent roster has no pick-rate weight")

    opponents = [
        {
            "slug": opponent["slug"],
            "name": opponent["name"],
            "source_order": opponent["source_order"],
            "pick_rate": opponent["pick_rate"],
            "weight": opponent["pick_rate"] / universe_pick_rate,
            "base_estimate": {
                "win_rate": matchup["win_rate"],
                "games": matchup["games"],
            },
        }
        for opponent, matchup in raw_opponents
    ]
    pool_before = round_value(
        math.fsum(
            opponent["weight"] * opponent["base_estimate"]["win_rate"]
            for opponent in opponents
        )
    )

    candidates = []
    for candidate in dataset["champions"]:
        if candidate["slug"] == base_slug:
            continue
        candidate_matchups: dict[str, dict[str, Any]] = {}
        observed_weight = 0.0
        applicable_weight = 0.0
        applicable_count = 0
        contributions = []

        for opponent in opponents:
            opponent_slug = opponent["slug"]
            if opponent_slug == candidate["slug"]:
                continue
            applicable_weight += opponent["weight"]
            applicable_count += 1
            matchup = candidate["matchups"].get(opponent_slug)
            if matchup is None:
                continue

            observed_weight += opponent["weight"]
            improvement = round_value(
                max(
                    0.0,
                    matchup["win_rate"]
                    - opponent["base_estimate"]["win_rate"],
                )
            )
            contribution = round_value(opponent["weight"] * improvement)
            contributions.append(contribution)
            candidate_matchups[opponent_slug] = {
                "win_rate": matchup["win_rate"],
                "games": matchup["games"],
                "improvement": improvement,
                "contribution": contribution,
            }

        gain = round_value(math.fsum(contributions))
        evidence_coverage = (
            round_value(observed_weight / applicable_weight)
            if applicable_weight > 0
            else 0.0
        )
        candidates.append(
            {
                "slug": candidate["slug"],
                "name": candidate["name"],
                "source_order": candidate["source_order"],
                "source_url": candidate["source_url"],
                "pool_after": round_value(pool_before + gain),
                "gain": gain,
                "evidence_coverage": evidence_coverage,
                "observed_matchups": len(candidate_matchups),
                "applicable_matchups": applicable_count,
                "matchups": candidate_matchups,
            }
        )

    candidates.sort(
        key=lambda candidate: (-candidate["gain"], candidate["source_order"])
    )
    return {
        "base": {
            key: base[key]
            for key in ("slug", "name", "source_order", "source_url")
        },
        "pool_before": pool_before,
        "opponent_universe": {
            "opponents": opponents,
            "displayed_count": len(opponents),
            "eligible_count": len(dataset["champions"]) - 1,
            "pick_rate_coverage": round_value(
                universe_pick_rate / eligible_pick_rate
            ),
        },
        "candidates": candidates,
    }


def validate_dataset(dataset: dict[str, Any]) -> None:
    if dataset.get("schema_version") != 2:
        raise ScrapeError("Dataset schema version is not supported")

    champions = dataset.get("champions")
    if not isinstance(champions, list) or len(champions) < 2:
        raise ScrapeError("Dataset needs at least two champions")
    champion_slugs = [champion["slug"] for champion in champions]
    source_orders = [champion["source_order"] for champion in champions]
    if len(champion_slugs) != len(set(champion_slugs)):
        raise ScrapeError("Dataset contains duplicate champions")
    if len(source_orders) != len(set(source_orders)):
        raise ScrapeError("Dataset contains duplicate source order values")

    champion_by_slug = {champion["slug"]: champion for champion in champions}
    defaults = dataset.get("defaults", {})
    for key in ("base_slug", "candidate_slug"):
        if defaults.get(key) not in champion_by_slug:
            raise ScrapeError(f"Dataset {key} is not in the champion roster")
    if defaults["base_slug"] == defaults["candidate_slug"]:
        raise ScrapeError("Default current champion and candidate are identical")

    display_threshold = dataset["source"]["display_threshold_games"]
    for champion in champions:
        if (
            not math.isfinite(champion["pick_rate"])
            or champion["pick_rate"] < 0
        ):
            raise ScrapeError(f"{champion['name']}: pick rate is invalid")
        if not 0 <= champion["overall_win_rate"] <= 100:
            raise ScrapeError(f"{champion['name']}: overall win rate is invalid")
        if not champion["matchups"]:
            raise ScrapeError(f"{champion['name']}: no displayed matchups")
        for opponent_slug, matchup in champion["matchups"].items():
            if opponent_slug not in champion_by_slug:
                raise ScrapeError(
                    f"{champion['name']}: matchup endpoint is outside the roster"
                )
            if opponent_slug == champion["slug"]:
                raise ScrapeError(
                    f"{champion['name']}: matrix contains an impossible mirror row"
                )
            if not 0 <= matchup["win_rate"] <= 100:
                raise ScrapeError(
                    f"{champion['name']} vs {opponent_slug}: win rate is invalid"
                )
            if not 0 <= matchup["all_champs_win_rate"] <= 100:
                raise ScrapeError(
                    f"{champion['name']} vs {opponent_slug}: all-WR is invalid"
                )
            if (
                not isinstance(matchup["games"], int)
                or matchup["games"] < display_threshold
            ):
                raise ScrapeError(
                    f"{champion['name']} vs {opponent_slug}: games are invalid"
                )
            expected_delta_1 = matchup["win_rate"] - matchup["all_champs_win_rate"]
            if not math.isclose(
                matchup["delta_1"], expected_delta_1, abs_tol=0.02
            ):
                raise ScrapeError(
                    f"{champion['name']} vs {opponent_slug}: delta 1 is inconsistent"
                )
            if not math.isfinite(matchup["delta_2"]):
                raise ScrapeError(
                    f"{champion['name']} vs {opponent_slug}: delta 2 is invalid"
                )

    for base_slug in champion_slugs:
        analysis = analyze_singleton_pool(dataset, base_slug)
        opponents = analysis["opponent_universe"]["opponents"]
        opponent_by_slug = {
            opponent["slug"]: opponent for opponent in opponents
        }
        weight_sum = math.fsum(opponent["weight"] for opponent in opponents)
        if not math.isclose(weight_sum, 1.0, abs_tol=1e-9):
            raise ScrapeError(
                f"{analysis['base']['name']}: opponent weights do not sum to 1"
            )
        if not 0 <= analysis["opponent_universe"]["pick_rate_coverage"] <= 1:
            raise ScrapeError(
                f"{analysis['base']['name']}: universe coverage is invalid"
            )
        if len(analysis["candidates"]) != len(champions) - 1:
            raise ScrapeError(
                f"{analysis['base']['name']}: candidate count is inconsistent"
            )
        for candidate in analysis["candidates"]:
            raw_candidate = champion_by_slug[candidate["slug"]]
            applicable_opponents = [
                opponent
                for opponent in opponents
                if opponent["slug"] != candidate["slug"]
            ]
            observed_opponents = [
                opponent
                for opponent in applicable_opponents
                if opponent["slug"] in raw_candidate["matchups"]
            ]
            if candidate["applicable_matchups"] != len(applicable_opponents):
                raise ScrapeError(
                    f"{candidate['name']}: applicable matchup count is inconsistent"
                )
            if candidate["observed_matchups"] != len(observed_opponents):
                raise ScrapeError(
                    f"{candidate['name']}: observed matchup count is inconsistent"
                )
            applicable_weight = math.fsum(
                opponent["weight"] for opponent in applicable_opponents
            )
            expected_coverage = (
                round_value(
                    math.fsum(
                        opponent["weight"] for opponent in observed_opponents
                    )
                    / applicable_weight
                )
                if applicable_weight > 0
                else 0.0
            )
            if not math.isclose(
                expected_coverage,
                candidate["evidence_coverage"],
                abs_tol=1e-9,
            ):
                raise ScrapeError(
                    f"{candidate['name']}: evidence coverage is inconsistent"
                )
            contribution_sum = round_value(
                math.fsum(
                    matchup["contribution"]
                    for matchup in candidate["matchups"].values()
                )
            )
            if not math.isclose(
                contribution_sum, candidate["gain"], abs_tol=1e-9
            ):
                raise ScrapeError(
                    f"{candidate['name']}: contribution sum is inconsistent"
                )
            expected_pair_score = round_value(
                math.fsum(
                    opponent["weight"]
                    * max(
                        opponent["base_estimate"]["win_rate"],
                        (
                            raw_candidate["matchups"][opponent["slug"]][
                                "win_rate"
                            ]
                            if (
                                opponent["slug"] != candidate["slug"]
                                and opponent["slug"]
                                in raw_candidate["matchups"]
                            )
                            else opponent["base_estimate"]["win_rate"]
                        ),
                    )
                    for opponent in opponent_by_slug.values()
                )
            )
            if not math.isclose(
                expected_pair_score,
                candidate["pool_after"],
                abs_tol=1e-9,
            ):
                raise ScrapeError(
                    f"{candidate['name']}: pair score does not match the raw matrix"
                )
            if not 0 <= candidate["evidence_coverage"] <= 1:
                raise ScrapeError(
                    f"{candidate['name']}: evidence coverage is invalid"
                )


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def page_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """Project the archived dataset to only fields used by the static page."""
    return {
        "schema_version": dataset["schema_version"],
        "generated_at": dataset["generated_at"],
        "filters": dataset["filters"],
        "source": dataset["source"],
        "method": dataset["method"],
        "defaults": dataset["defaults"],
        "champions": [
            {
                key: champion[key]
                for key in (
                    "slug",
                    "name",
                    "source_order",
                    "pick_rate",
                    "source_url",
                )
            }
            | {
                "matchups": {
                    opponent_slug: {
                        key: matchup[key]
                        for key in ("win_rate", "games")
                    }
                    for opponent_slug, matchup in champion["matchups"].items()
                }
            }
            for champion in dataset["champions"]
        ],
    }


def render_page(dataset: dict[str, Any], page_kind: str) -> str:
    if page_kind not in {"rankings", "pair"}:
        raise ScrapeError(f"Unknown page kind: {page_kind}")
    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise ScrapeError(f"Could not read {TEMPLATE_PATH.name}: {error}") from error
    if template.count(DATA_MARKER) != 1 or template.count(PAGE_MARKER) != 1:
        raise ScrapeError(
            f"{TEMPLATE_PATH.name} must contain exactly one data marker and "
            "one page marker"
        )
    embedded = json.dumps(
        page_dataset(dataset), ensure_ascii=False, separators=(",", ":")
    )
    embedded = embedded.replace("<", "\\u003c")
    return template.replace(DATA_MARKER, embedded).replace(PAGE_MARKER, page_kind)


def render_reports(dataset: dict[str, Any]) -> dict[Path, str]:
    """Render the ranking overview and pair-level calculation pages."""
    return {
        RANKINGS_REPORT_PATH: render_page(dataset, "rankings"),
        PAIR_REPORT_PATH: render_page(dataset, "pair"),
    }


def archive_name(dataset: dict[str, Any]) -> str:
    generated = datetime.fromisoformat(dataset["generated_at"].replace("Z", "+00:00"))
    filters = dataset["filters"]
    timestamp = generated.strftime("%Y%m%dT%H%M%SZ")
    cohort = (
        f"{filters['lane']}-{filters['tier']}-{filters['region']}-"
        f"{filters['period']}-singleton-matrix"
    )
    return f"{timestamp}-{cohort}.json"


def scrape(args: argparse.Namespace) -> dict[str, Any]:
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (compatible; HextechStudies/1.0; "
        "+https://githubpsyche.github.io/hextech-studies/)"
    )

    try:
        tierlist_url = build_url("tierlist", query_params(args))
        print(f"Fetching roster: {tierlist_url}", flush=True)
        roster = extract_roster(fetch(session, tierlist_url))
        print(f"Found {len(roster)} {LANE_LABELS[args.lane]} champions", flush=True)

        roster_slugs = {champion["slug"] for champion in roster}
        unknown_defaults = [
            slug for slug in (args.base, args.candidate) if slug not in roster_slugs
        ]
        if unknown_defaults:
            raise ScrapeError(
                "Default champions are not in the selected lane roster: "
                + ", ".join(unknown_defaults)
            )

        roster_by_slug = {champion["slug"]: champion for champion in roster}
        roster_by_id = {champion["champion_id"]: champion for champion in roster}
        fetch_order = [
            args.base,
            args.candidate,
            *(
                champion["slug"]
                for champion in roster
                if champion["slug"] not in {args.base, args.candidate}
            ),
        ]
        pages = {}
        for index, slug in enumerate(fetch_order, start=1):
            if index > 1:
                time.sleep(REQUEST_DELAY)
            print(f"[{index:>3}/{len(fetch_order)}] {slug}", flush=True)
            source_url = build_url(
                f"{slug}/counters",
                query_params(args, include_opponent_lane=True),
            )
            pages[slug] = extract_matchup_page(
                fetch(session, source_url),
                roster_by_slug[slug],
                roster_by_id,
                args,
                source_url,
            )
            pages[slug]["source_order"] = roster_by_slug[slug]["source_order"]

        return build_dataset(args, roster, pages, tierlist_url)
    finally:
        session.close()


def main() -> int:
    args = parse_args()
    try:
        if args.render_only:
            if not LATEST_PATH.exists():
                raise ScrapeError(f"{LATEST_PATH} does not exist")
            dataset = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
            print(f"Rendering {LATEST_PATH.relative_to(REPOSITORY_ROOT)}", flush=True)
        else:
            dataset = scrape(args)

        validate_dataset(dataset)
        reports = render_reports(dataset)
        if not args.render_only:
            pretty_json = json.dumps(dataset, ensure_ascii=False, indent=2) + "\n"
            archive_path = ARCHIVE_DIR / archive_name(dataset)
            atomic_write(archive_path, pretty_json)
            atomic_write(LATEST_PATH, pretty_json)
        for report_path, report in reports.items():
            atomic_write(report_path, report)
    except (ScrapeError, OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", flush=True)
        return 1

    if not args.render_only:
        print(f"Wrote {LATEST_PATH.relative_to(REPOSITORY_ROOT)}", flush=True)
        print(f"Wrote {archive_path.relative_to(REPOSITORY_ROOT)}", flush=True)
    for report_path in reports:
        print(f"Wrote {report_path.relative_to(REPOSITORY_ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
