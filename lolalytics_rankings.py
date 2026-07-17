# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "parsel>=1.10,<2",
#   "requests>=2.32,<3",
# ]
# ///

"""Scrape Lolalytics champion stats and build a standalone ranking table."""

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


ROOT = Path(__file__).resolve().parent
TEMPLATE_PATH = ROOT / "report_template.html"
LATEST_PATH = ROOT / "data" / "latest.json"
ARCHIVE_DIR = ROOT / "data" / "archive"
REPORT_PATH = ROOT / "index.html"
DATA_MARKER = "__LOLALYTICS_DATA__"

BASE_URL = "https://lolalytics.com/lol"
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.25
REQUEST_ATTEMPTS = 3

LANES = ("top", "jungle", "middle", "bottom", "support")
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
METRICS = (
    ("physical_damage", "Physical Damage"),
    ("magic_damage", "Magic Damage"),
    ("true_damage", "True Damage"),
    ("total_damage", "Total Damage"),
    ("damage_taken", "Damage Taken"),
    ("healing", "Healing"),
    ("kills", "Kills"),
    ("deaths", "Deaths"),
    ("assists", "Assists"),
    ("max_kill_spree", "Max Kill Spree"),
    ("gold", "Gold"),
    ("minions_killed", "Minions Killed"),
    ("jungle_cs", "Jungle CS"),
)


class ScrapeError(RuntimeError):
    """Raised when a response is incomplete or no longer matches expectations."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an interactive table from Lolalytics champion stats."
    )
    parser.add_argument("--lane", choices=LANES, default="middle")
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


def query_params(args: argparse.Namespace) -> dict[str, str]:
    params = {"lane": args.lane, "tier": args.tier, "region": args.region}
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


def extract_roster(html: str) -> list[str]:
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
    return [best_slug_map[cid] for cid in champion_ids]


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

    for key, label in METRICS:
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
    champions = dataset["champions"]
    if not champions:
        raise ScrapeError("No champions were scraped")

    slugs = [champion["slug"] for champion in champions]
    if len(slugs) != len(set(slugs)):
        raise ScrapeError("The completed dataset contains duplicate champions")

    expected_keys = {key for key, _ in METRICS}
    for champion in champions:
        actual_keys = set(champion["values"])
        if actual_keys != expected_keys:
            missing = ", ".join(sorted(expected_keys - actual_keys))
            raise ScrapeError(f"{champion['name']} is missing metrics: {missing}")


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


def main() -> int:
    args = parse_args()
    params = query_params(args)
    tierlist_url = build_url("tierlist", params)
    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (compatible; LolalyticsRankings/1.0; "
        "+https://lolalytics.com/)"
    )

    try:
        print(f"Fetching roster: {tierlist_url}", flush=True)
        roster = extract_roster(fetch(session, tierlist_url))
        print(f"Found {len(roster)} champions", flush=True)

        champions = []
        for index, slug in enumerate(roster, start=1):
            if index > 1:
                time.sleep(REQUEST_DELAY)
            print(f"[{index:>3}/{len(roster)}] {slug}", flush=True)
            champion_url = build_url(f"{slug}/build", params)
            champion = extract_champion(
                fetch(session, champion_url),
                slug=slug,
                source_order=index - 1,
            )
            champions.append(champion)

        generated_at = datetime.now(UTC)
        dataset = {
            "schema_version": 1,
            "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
            "filters": {
                "lane": args.lane,
                "tier": args.tier,
                "region": args.region,
                "period": args.period,
                "queue": "Ranked Solo/Duo",
            },
            "source_url": tierlist_url,
            "metrics": [
                {"key": key, "label": label}
                for key, label in METRICS
            ],
            "champions": champions,
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

    print(f"Wrote {LATEST_PATH.relative_to(ROOT)}", flush=True)
    print(f"Wrote {archive_path.relative_to(ROOT)}", flush=True)
    print(f"Wrote {REPORT_PATH.relative_to(ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
