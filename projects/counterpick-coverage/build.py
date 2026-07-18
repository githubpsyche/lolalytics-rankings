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
REPORT_PATH = REPOSITORY_ROOT / "docs" / "counterpick-coverage" / "index.html"
DATA_MARKER = "__COUNTERPICK_COVERAGE_DATA__"

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
            "Rank additions to a champion pool by observed marginal "
            "counterpick coverage."
        )
    )
    parser.add_argument("--lane", choices=LANES, default="middle")
    parser.add_argument(
        "--pool",
        nargs="+",
        default=["zoe"],
        metavar="SLUG",
        help=(
            "current champion-pool slugs, separated by spaces or commas (default: zoe)"
        ),
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

    pool = []
    for value in args.pool:
        pool.extend(part.strip().lower() for part in value.split(",") if part.strip())
    if not pool:
        parser.error("--pool must contain at least one champion slug")
    invalid = [slug for slug in pool if not re.fullmatch(r"[a-z0-9]+", slug)]
    if invalid:
        parser.error(
            "--pool values must be lowercase Lolalytics slugs: " + ", ".join(invalid)
        )
    if len(pool) != len(set(pool)):
        parser.error("--pool contains duplicate champion slugs")
    args.pool = pool
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
    unknown_pool = [slug for slug in args.pool if slug not in roster_by_slug]
    if unknown_pool:
        raise ScrapeError(
            "Pool champions are not in the selected lane roster: "
            + ", ".join(unknown_pool)
        )

    baseline_by_opponent: dict[str, dict[str, Any]] = {}
    for pool_slug in args.pool:
        for opponent_slug, matchup in pages[pool_slug]["matchups"].items():
            if opponent_slug not in roster_by_slug:
                continue
            current = baseline_by_opponent.get(opponent_slug)
            if current is None:
                current = {
                    "pool_slug": pool_slug,
                    "pool_name": pages[pool_slug]["name"],
                    "observed_pool_slugs": [],
                    **matchup,
                }
                baseline_by_opponent[opponent_slug] = current
            current["observed_pool_slugs"].append(pool_slug)
            if matchup["win_rate"] > current["win_rate"]:
                current.update(
                    {
                        "pool_slug": pool_slug,
                        "pool_name": pages[pool_slug]["name"],
                        **matchup,
                    }
                )

    if not baseline_by_opponent:
        raise ScrapeError("The selected pool has no displayed matchup universe")

    universe_pick_rate = math.fsum(
        roster_by_slug[slug]["pick_rate"] for slug in baseline_by_opponent
    )
    if universe_pick_rate <= 0:
        raise ScrapeError("The displayed opponent universe has no pick-rate weight")

    eligible_opponents = [
        champion
        for champion in roster
        if any(pool_slug != champion["slug"] for pool_slug in args.pool)
    ]
    eligible_pick_rate = math.fsum(
        champion["pick_rate"] for champion in eligible_opponents
    )
    if eligible_pick_rate <= 0:
        raise ScrapeError("The eligible opponent roster has no pick-rate weight")

    opponents = []
    for opponent_slug, baseline in baseline_by_opponent.items():
        roster_entry = roster_by_slug[opponent_slug]
        opponents.append(
            {
                "slug": opponent_slug,
                "name": pages[opponent_slug]["name"],
                "source_order": roster_entry["source_order"],
                "pick_rate": roster_entry["pick_rate"],
                "weight": roster_entry["pick_rate"] / universe_pick_rate,
                "current_best": {
                    "slug": baseline["pool_slug"],
                    "name": baseline["pool_name"],
                    "win_rate": baseline["win_rate"],
                    "games": baseline["games"],
                    "pool_evidence_count": len(baseline["observed_pool_slugs"]),
                    "pool_size": len(args.pool),
                },
            }
        )
    opponents.sort(key=lambda opponent: opponent["source_order"])

    pool_before = round_value(
        math.fsum(
            opponent["weight"] * opponent["current_best"]["win_rate"]
            for opponent in opponents
        )
    )

    candidates = []
    for roster_entry in roster:
        candidate_slug = roster_entry["slug"]
        if candidate_slug in args.pool:
            continue

        candidate_page = pages[candidate_slug]
        candidate_matchups: dict[str, dict[str, Any]] = {}
        observed_weight = 0.0
        applicable_weight = 0.0
        applicable_count = 0
        contributions = []

        for opponent in opponents:
            opponent_slug = opponent["slug"]
            if opponent_slug == candidate_slug:
                continue
            applicable_weight += opponent["weight"]
            applicable_count += 1
            matchup = candidate_page["matchups"].get(opponent_slug)
            if matchup is None:
                continue

            observed_weight += opponent["weight"]
            improvement = max(
                0.0, matchup["win_rate"] - opponent["current_best"]["win_rate"]
            )
            contribution = round_value(opponent["weight"] * improvement)
            contributions.append(contribution)
            candidate_matchups[opponent_slug] = {
                "win_rate": matchup["win_rate"],
                "games": matchup["games"],
                "all_champs_win_rate": matchup["all_champs_win_rate"],
                "delta_1": matchup["delta_1"],
                "delta_2": matchup["delta_2"],
                "improvement": round_value(improvement),
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
                "slug": candidate_slug,
                "name": candidate_page["name"],
                "source_order": roster_entry["source_order"],
                "source_url": candidate_page["source_url"],
                "overall_win_rate": roster_entry["overall_win_rate"],
                "pick_rate": roster_entry["pick_rate"],
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

    generated_at = datetime.now(UTC)
    return {
        "schema_version": 1,
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
            "estimate": "observed_matchup_win_rate",
            "opponent_weight": "lane_pick_rate_normalized_over_pool_universe",
            "missing_candidate_matchup": "zero_demonstrated_improvement",
            "candidate_self_matchup": "unavailable",
        },
        "pool": [
            {
                "slug": slug,
                "name": pages[slug]["name"],
                "source_url": pages[slug]["source_url"],
            }
            for slug in args.pool
        ],
        "pool_before": pool_before,
        "opponent_universe": {
            "opponents": opponents,
            "displayed_count": len(opponents),
            "eligible_count": len(eligible_opponents),
            "pick_rate_coverage": round_value(universe_pick_rate / eligible_pick_rate),
        },
        "candidates": candidates,
    }


def validate_dataset(dataset: dict[str, Any]) -> None:
    if dataset.get("schema_version") != 1:
        raise ScrapeError("Dataset schema version is not supported")
    if not dataset.get("pool"):
        raise ScrapeError("Dataset has no current champion pool")

    opponents = dataset["opponent_universe"]["opponents"]
    if not opponents:
        raise ScrapeError("Dataset has no opponent universe")
    opponent_slugs = [opponent["slug"] for opponent in opponents]
    if len(opponent_slugs) != len(set(opponent_slugs)):
        raise ScrapeError("Dataset contains duplicate opponents")
    weight_sum = math.fsum(opponent["weight"] for opponent in opponents)
    if not math.isclose(weight_sum, 1.0, abs_tol=1e-9):
        raise ScrapeError(f"Opponent weights sum to {weight_sum}, not 1")

    recomputed_before = round_value(
        math.fsum(
            opponent["weight"] * opponent["current_best"]["win_rate"]
            for opponent in opponents
        )
    )
    if not math.isclose(recomputed_before, dataset["pool_before"], abs_tol=1e-9):
        raise ScrapeError("Pool-before score does not match opponent contributions")

    opponent_by_slug = {opponent["slug"]: opponent for opponent in opponents}
    candidate_slugs = [candidate["slug"] for candidate in dataset["candidates"]]
    if len(candidate_slugs) != len(set(candidate_slugs)):
        raise ScrapeError("Dataset contains duplicate candidates")

    for candidate in dataset["candidates"]:
        if candidate["slug"] in {member["slug"] for member in dataset["pool"]}:
            raise ScrapeError(f"{candidate['name']} is both pool member and candidate")
        if not set(candidate["matchups"]).issubset(opponent_by_slug):
            raise ScrapeError(f"{candidate['name']} has matchups outside the universe")
        if candidate["slug"] in candidate["matchups"]:
            raise ScrapeError(f"{candidate['name']} contains an impossible mirror row")
        if candidate["observed_matchups"] != len(candidate["matchups"]):
            raise ScrapeError(
                f"{candidate['name']}: observed matchup count is inconsistent"
            )
        expected_applicable_count = len(opponents) - int(
            candidate["slug"] in opponent_by_slug
        )
        if candidate["applicable_matchups"] != expected_applicable_count:
            raise ScrapeError(
                f"{candidate['name']}: applicable matchup count is inconsistent"
            )

        contributions = []
        observed_weight = 0.0
        applicable_weight = 0.0
        for opponent in opponents:
            if opponent["slug"] == candidate["slug"]:
                continue
            applicable_weight += opponent["weight"]
            matchup = candidate["matchups"].get(opponent["slug"])
            if matchup is None:
                continue
            observed_weight += opponent["weight"]
            expected_improvement = round_value(
                max(
                    0.0,
                    matchup["win_rate"] - opponent["current_best"]["win_rate"],
                )
            )
            if not math.isclose(
                expected_improvement, matchup["improvement"], abs_tol=1e-9
            ):
                raise ScrapeError(
                    f"{candidate['name']} vs {opponent['name']}: "
                    "improvement is inconsistent"
                )
            expected_contribution = round_value(
                opponent["weight"] * matchup["improvement"]
            )
            if not math.isclose(
                expected_contribution, matchup["contribution"], abs_tol=1e-9
            ):
                raise ScrapeError(
                    f"{candidate['name']} vs {opponent['name']}: "
                    "contribution is inconsistent"
                )
            contributions.append(matchup["contribution"])

        expected_gain = round_value(math.fsum(contributions))
        if not math.isclose(expected_gain, candidate["gain"], abs_tol=1e-9):
            raise ScrapeError(
                f"{candidate['name']}: contribution sum does not match gain"
            )
        if not math.isclose(
            round_value(dataset["pool_before"] + candidate["gain"]),
            candidate["pool_after"],
            abs_tol=1e-9,
        ):
            raise ScrapeError(f"{candidate['name']}: pool-after score is inconsistent")
        expected_coverage = (
            round_value(observed_weight / applicable_weight)
            if applicable_weight > 0
            else 0.0
        )
        if not math.isclose(
            expected_coverage, candidate["evidence_coverage"], abs_tol=1e-9
        ):
            raise ScrapeError(f"{candidate['name']}: evidence coverage is inconsistent")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def page_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """Project the archived dataset to only fields used by the static page."""
    universe = dataset["opponent_universe"]
    return {
        "schema_version": dataset["schema_version"],
        "generated_at": dataset["generated_at"],
        "filters": dataset["filters"],
        "source": dataset["source"],
        "method": dataset["method"],
        "pool": dataset["pool"],
        "pool_before": dataset["pool_before"],
        "opponent_universe": {
            "displayed_count": universe["displayed_count"],
            "eligible_count": universe["eligible_count"],
            "pick_rate_coverage": universe["pick_rate_coverage"],
            "opponents": [
                {
                    "slug": opponent["slug"],
                    "name": opponent["name"],
                    "weight": opponent["weight"],
                    "current_best": opponent["current_best"],
                }
                for opponent in universe["opponents"]
            ],
        },
        "candidates": [
            {
                key: candidate[key]
                for key in (
                    "slug",
                    "name",
                    "source_order",
                    "source_url",
                    "pool_after",
                    "gain",
                    "evidence_coverage",
                    "observed_matchups",
                    "applicable_matchups",
                )
            }
            | {
                "matchups": {
                    opponent_slug: {
                        key: matchup[key]
                        for key in (
                            "win_rate",
                            "games",
                            "improvement",
                            "contribution",
                        )
                    }
                    for opponent_slug, matchup in candidate["matchups"].items()
                }
            }
            for candidate in dataset["candidates"]
        ],
    }


def render_report(dataset: dict[str, Any]) -> str:
    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise ScrapeError(f"Could not read {TEMPLATE_PATH.name}: {error}") from error
    if template.count(DATA_MARKER) != 1:
        raise ScrapeError(
            f"{TEMPLATE_PATH.name} must contain exactly one {DATA_MARKER} marker"
        )
    embedded = json.dumps(
        page_dataset(dataset), ensure_ascii=False, separators=(",", ":")
    )
    embedded = embedded.replace("<", "\\u003c")
    return template.replace(DATA_MARKER, embedded)


def archive_name(dataset: dict[str, Any]) -> str:
    generated = datetime.fromisoformat(dataset["generated_at"].replace("Z", "+00:00"))
    filters = dataset["filters"]
    pool = "-".join(member["slug"] for member in dataset["pool"])
    timestamp = generated.strftime("%Y%m%dT%H%M%SZ")
    cohort = (
        f"{filters['lane']}-{filters['tier']}-{filters['region']}-"
        f"{filters['period']}-{pool}"
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
        unknown_pool = [slug for slug in args.pool if slug not in roster_slugs]
        if unknown_pool:
            raise ScrapeError(
                "Pool champions are not in the selected lane roster: "
                + ", ".join(unknown_pool)
            )

        roster_by_slug = {champion["slug"]: champion for champion in roster}
        roster_by_id = {champion["champion_id"]: champion for champion in roster}
        fetch_order = [
            *args.pool,
            *(
                champion["slug"]
                for champion in roster
                if champion["slug"] not in args.pool
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
        report = render_report(dataset)
        if not args.render_only:
            pretty_json = json.dumps(dataset, ensure_ascii=False, indent=2) + "\n"
            archive_path = ARCHIVE_DIR / archive_name(dataset)
            atomic_write(archive_path, pretty_json)
            atomic_write(LATEST_PATH, pretty_json)
        atomic_write(REPORT_PATH, report)
    except (ScrapeError, OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", flush=True)
        return 1

    if not args.render_only:
        print(f"Wrote {LATEST_PATH.relative_to(REPOSITORY_ROOT)}", flush=True)
        print(f"Wrote {archive_path.relative_to(REPOSITORY_ROOT)}", flush=True)
    print(f"Wrote {REPORT_PATH.relative_to(REPOSITORY_ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
