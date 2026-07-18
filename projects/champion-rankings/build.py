# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "parsel>=1.10,<2",
#   "requests>=2.32,<3",
# ]
# ///

"""Scrape Lolalytics champion stats and build the rankings project page."""

from __future__ import annotations

import argparse
import json
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
REPORT_PATH = REPOSITORY_ROOT / "docs" / "champion-rankings" / "index.html"
DATA_MARKER = "__LOLALYTICS_DATA__"

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
REGIONS = (
    "all",
    "br",
    "eune",
    "euw",
    "jp",
    "kr",
    "lan",
    "las",
    "na",
    "oce",
    "sg",
    "tr",
    "tw",
    "ru",
    "vn",
)
CATEGORIES = (
    ("overall", "Overall", True),
    ("combat", "Combat", True),
    ("economy", "Economy & Farming", True),
    ("best_worldwide", "Best Worldwide", False),
)
TIERLIST_METRICS = (
    ("tier", "Tier", True, "overall"),
    ("win_rate", "Win %", True, "overall"),
    ("pick_rate", "Pick %", True, "overall"),
    ("ban_rate", "Ban %", True, "overall"),
    ("best_rank", "Best Rank", True, "best_worldwide"),
    ("best_win_rate", "Best Win %", True, "best_worldwide"),
    ("best_games", "Best Games", True, "best_worldwide"),
    ("best_delta", "Best Δ", True, "best_worldwide"),
    ("best_elo", "Best Elo", False, "best_worldwide"),
)
STAT_METRICS = (
    ("physical_damage", "Physical Damage", True, "combat"),
    ("magic_damage", "Magic Damage", True, "combat"),
    ("true_damage", "True Damage", True, "combat"),
    ("total_damage", "Total Damage", True, "combat"),
    ("damage_taken", "Damage Taken", True, "combat"),
    ("healing", "Healing", True, "combat"),
    ("kills", "Kills", True, "combat"),
    ("deaths", "Deaths", True, "combat"),
    ("assists", "Assists", True, "combat"),
    ("max_kill_spree", "Max Kill Spree", True, "combat"),
    ("gold", "Gold", True, "economy"),
    ("minions_killed", "Minions Killed", True, "economy"),
    ("jungle_cs", "Jungle CS", True, "economy"),
)
METRICS = tuple(
    metric
    for category, _, _ in CATEGORIES
    for metric in TIERLIST_METRICS + STAT_METRICS
    if metric[3] == category
)
TIER_LABELS = (
    "S+",
    "S",
    "S-",
    "A+",
    "A",
    "A-",
    "B+",
    "B",
    "B-",
    "C+",
    "C",
    "C-",
    "D+",
    "D",
    "D-",
)


class ScrapeError(RuntimeError):
    """Raised when a response is incomplete or no longer matches expectations."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an interactive table from Lolalytics champion stats."
    )
    parser.add_argument(
        "--lane",
        choices=("all", *LANES),
        default="all",
        help="scrape all lanes or one lane for a faster targeted run (default: all)",
    )
    parser.add_argument("--tier", choices=TIERS, default="emerald_plus")
    parser.add_argument("--region", choices=REGIONS, default="all")
    parser.add_argument(
        "--period",
        default="30",
        help='current, 7, 14, 30, or a patch such as "16.14" (default: 30)',
    )
    args = parser.parse_args()
    if args.period not in {"current", "7", "14", "30"} and not re.fullmatch(
        r"\d{1,2}\.\d{1,2}", args.period
    ):
        parser.error("--period must be current, 7, 14, 30, or a patch such as 16.14")
    return args


def query_params(args: argparse.Namespace, lane: str) -> dict[str, str]:
    params = {"lane": lane, "tier": args.tier, "region": args.region}
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


def resolved_row_value(
    objects: list[Any], row: dict[str, Any], key: str
) -> Any:
    if key not in row:
        raise ScrapeError(f"Tier-list row is missing {key!r}")
    value = resolve_qwik_reference(objects, row[key])
    if value is None:
        raise ScrapeError(f"Tier-list value {key!r} could not be resolved")
    return value


def numeric_entry(
    value: Any, label: str, *, signed: bool = False, integer: bool = False
) -> dict[str, int | float | str]:
    number = parse_numeric(str(value), label)
    if integer:
        if not isinstance(number, int):
            raise ScrapeError(f"{label} is not an integer: {value!r}")
        display = f"{number:,}"
    else:
        display = f"{number:.2f}"
        if signed and number > 0:
            display = f"+{display}"
    return {"value": number, "display": display}


def tierlist_values(
    objects: list[Any], row: dict[str, Any]
) -> dict[str, dict[str, int | float | str]]:
    tier_number = parse_numeric(
        str(resolved_row_value(objects, row, "tier")), "Tier"
    )
    if not isinstance(tier_number, int) or not 1 <= tier_number <= len(TIER_LABELS):
        raise ScrapeError(f"Tier has an unexpected value: {tier_number!r}")

    elo = parse_numeric(
        str(resolved_row_value(objects, row, "topElo")), "Best Elo"
    )
    if not isinstance(elo, int):
        raise ScrapeError(f"Best Elo is not an integer: {elo!r}")
    elo_label = "CH" if elo >= 900 else "GM" if elo >= 500 else "M"

    return {
        "tier": {
            "value": tier_number,
            "sort_value": len(TIER_LABELS) + 1 - tier_number,
            "display": TIER_LABELS[tier_number - 1],
        },
        "win_rate": numeric_entry(
            resolved_row_value(objects, row, "wr"), "Win %"
        ),
        "pick_rate": numeric_entry(
            resolved_row_value(objects, row, "pr"), "Pick %"
        ),
        "ban_rate": numeric_entry(
            resolved_row_value(objects, row, "br"), "Ban %"
        ),
        "best_rank": numeric_entry(
            resolved_row_value(objects, row, "topRank"),
            "Best Rank",
            integer=True,
        ),
        "best_win_rate": numeric_entry(
            resolved_row_value(objects, row, "topWr"), "Best Win %"
        ),
        "best_games": numeric_entry(
            resolved_row_value(objects, row, "topGames"),
            "Best Games",
            integer=True,
        ),
        "best_delta": numeric_entry(
            resolved_row_value(objects, row, "topDelta"), "Best Δ"
        ),
        "best_elo": {"value": elo, "display": elo_label},
    }


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
        if isinstance(item, dict)
        and {"cid", "row", "placeholder"}.issubset(item)
    ]
    champion_ids = [
        resolve_qwik_reference(objects, row["cid"])
        for row in rows
    ]
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

    roster = []
    for source_order, (champion_id, outer_row) in enumerate(
        zip(champion_ids, rows, strict=True)
    ):
        row = resolve_qwik_reference(objects, outer_row["row"])
        if not isinstance(row, dict):
            raise ScrapeError("Tier-list champion row could not be resolved")
        roster.append(
            {
                "slug": best_slug_map[champion_id],
                "source_order": source_order,
                "values": tierlist_values(objects, row),
            }
        )
    return roster


def parse_numeric(display: str, metric: str) -> int | float:
    normalized = display.replace(",", "").strip()
    try:
        value = float(normalized)
    except ValueError as error:
        raise ScrapeError(f"{metric} has a non-numeric value: {display!r}") from error
    return int(value) if value.is_integer() else value


def extract_champion(html: str, slug: str, source_order: int) -> dict[str, Any]:
    selector = Selector(html)
    headings = selector.xpath(
        '//h2[substring(normalize-space(.), '
        'string-length(normalize-space(.)) - 5) = " Stats"]'
    )
    if len(headings) != 1:
        raise ScrapeError(f"{slug}: expected one Stats panel, found {len(headings)}")

    heading = headings[0]
    heading_text = heading.xpath("normalize-space(.)").get()
    name = heading_text.removesuffix(" Stats") if heading_text else slug
    panel = heading.xpath("..")
    values: dict[str, dict[str, int | float | str]] = {}

    for key, label, _, _ in STAT_METRICS:
        labels = panel.xpath(f'.//div[normalize-space(text())="{label}:"]')
        if len(labels) != 1:
            raise ScrapeError(
                f"{name}: expected one {label!r} value, found {len(labels)}"
            )
        display = labels[0].xpath(
            "following-sibling::div[1]/div[1]/text()"
        ).get()
        if display is None:
            raise ScrapeError(f"{name}: {label!r} has no value")
        display = display.strip()
        values[key] = {
            "value": parse_numeric(display, label),
            "display": display,
        }

    return {
        "name": name,
        "slug": slug,
        "source_order": source_order,
        "values": values,
    }


def validate_dataset(dataset: dict[str, Any]) -> None:
    lanes = dataset["lanes"]
    if not lanes:
        raise ScrapeError("No lanes were scraped")
    lane_keys = [lane["key"] for lane in lanes]
    if len(lane_keys) != len(set(lane_keys)):
        raise ScrapeError("The completed dataset contains duplicate lanes")
    if dataset["default_lane"] not in lane_keys:
        raise ScrapeError("The default lane is not present in the dataset")

    expected_keys = {key for key, _, _, _ in METRICS}
    for lane in lanes:
        champions = lane["champions"]
        if not champions:
            raise ScrapeError(f"No {lane['label']} champions were scraped")
        slugs = [champion["slug"] for champion in champions]
        if len(slugs) != len(set(slugs)):
            raise ScrapeError(f"The {lane['label']} dataset contains duplicates")
        for champion in champions:
            actual_keys = set(champion["values"])
            if actual_keys != expected_keys:
                missing = ", ".join(sorted(expected_keys - actual_keys))
                raise ScrapeError(
                    f"{lane['label']} {champion['name']} is missing metrics: {missing}"
                )
            for key, entry in champion["values"].items():
                if not isinstance(entry.get("value"), (int, float)):
                    raise ScrapeError(
                        f"{lane['label']} {champion['name']}: {key} is not numeric"
                    )
                if not isinstance(entry.get("display"), str) or not entry["display"]:
                    raise ScrapeError(
                        f"{lane['label']} {champion['name']}: {key} has no display value"
                    )


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def render_report(dataset: dict[str, Any]) -> str:
    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise ScrapeError(f"Could not read {TEMPLATE_PATH.name}: {error}") from error
    if template.count(DATA_MARKER) != 1:
        raise ScrapeError(
            f"{TEMPLATE_PATH.name} must contain exactly one {DATA_MARKER} marker"
        )
    embedded = json.dumps(dataset, ensure_ascii=False, separators=(",", ":"))
    embedded = embedded.replace("<", "\\u003c")
    return template.replace(DATA_MARKER, embedded)


def archive_name(generated_at: datetime, args: argparse.Namespace) -> str:
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    cohort = f"{args.lane}-{args.tier}-{args.region}-{args.period}"
    return f"{timestamp}-{cohort}.json"


def scrape_lane(
    session: requests.Session, args: argparse.Namespace, lane: str
) -> dict[str, Any]:
    params = query_params(args, lane)
    tierlist_url = build_url("tierlist", params)
    print(f"\n{LANE_LABELS[lane]} lane", flush=True)
    print(f"Fetching roster: {tierlist_url}", flush=True)
    roster = extract_roster(fetch(session, tierlist_url))
    print(f"Found {len(roster)} champions", flush=True)

    champions = []
    for index, roster_champion in enumerate(roster, start=1):
        slug = roster_champion["slug"]
        if index > 1:
            time.sleep(REQUEST_DELAY)
        print(f"[{index:>3}/{len(roster)}] {slug}", flush=True)
        champion_url = build_url(f"{slug}/build", params)
        champion = extract_champion(
            fetch(session, champion_url),
            slug=slug,
            source_order=roster_champion["source_order"],
        )
        champion["values"] = {
            **roster_champion["values"],
            **champion["values"],
        }
        champions.append(champion)

    return {
        "key": lane,
        "label": LANE_LABELS[lane],
        "source_url": tierlist_url,
        "champions": champions,
    }


def main() -> int:
    args = parse_args()
    selected_lanes = LANES if args.lane == "all" else (args.lane,)
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (compatible; HextechStudies/1.0; "
        "+https://lolalytics.com/)"
    )

    try:
        lanes = [scrape_lane(session, args, lane) for lane in selected_lanes]

        generated_at = datetime.now(UTC)
        default_lane = "middle" if "middle" in selected_lanes else selected_lanes[0]
        dataset = {
            "schema_version": 4,
            "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
            "default_lane": default_lane,
            "filters": {
                "tier": args.tier,
                "region": args.region,
                "period": args.period,
                "queue": "Ranked Solo/Duo",
            },
            "categories": [
                {
                    "key": key,
                    "label": label,
                    "default_visible": default_visible,
                }
                for key, label, default_visible in CATEGORIES
            ],
            "metrics": [
                {
                    "key": key,
                    "label": label,
                    "filterable": filterable,
                    "category": category,
                }
                for key, label, filterable, category in METRICS
            ],
            "lanes": lanes,
        }
        validate_dataset(dataset)
        pretty_json = json.dumps(dataset, ensure_ascii=False, indent=2) + "\n"
        report = render_report(dataset)

        archive_path = ARCHIVE_DIR / archive_name(generated_at, args)
        atomic_write(archive_path, pretty_json)
        atomic_write(LATEST_PATH, pretty_json)
        atomic_write(REPORT_PATH, report)
    except (ScrapeError, OSError) as error:
        print(f"Error: {error}", flush=True)
        return 1
    finally:
        session.close()

    print(f"Wrote {LATEST_PATH.relative_to(REPOSITORY_ROOT)}", flush=True)
    print(f"Wrote {archive_path.relative_to(REPOSITORY_ROOT)}", flush=True)
    print(f"Wrote {REPORT_PATH.relative_to(REPOSITORY_ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
