# Lolalytics Champion Rankings

An unofficial, lightweight scraper and interactive ranking table for the
server-rendered champion statistics on [Lolalytics](https://lolalytics.com/).

**Live table:** https://githubpsyche.github.io/lolalytics-rankings/

The generated page supports:

- single-column ascending and descending sorting;
- inclusive minimum and maximum filters for every metric;
- independently toggled Overall, Combat, Economy & Farming, and Best
  Worldwide metric groups; and
- configurable lane, tier, region, and period.

Overall, Combat, and Economy & Farming are visible by default. Best Worldwide
is opt-in. The table combines selected tier-list performance and best-player
fields with the 13 per-champion damage, combat, economy, and farming
statistics.

Tier filters use the ordered D- to S+ scale: a minimum of A includes A and
better, while a maximum of S includes S and worse.

## Refresh the data

Install [uv](https://docs.astral.sh/uv/), then run:

```bash
uv run lolalytics_rankings.py
```

Defaults are Middle, Emerald+, Global, Ranked Solo/Duo, and the rolling
30-day period. For example:

```bash
uv run lolalytics_rankings.py \
  --lane middle \
  --tier emerald_plus \
  --region all \
  --period 30
```

Each successful run updates:

- `index.html` — the self-contained GitHub Pages site;
- `data/latest.json` — the latest structured dataset; and
- `data/archive/` — timestamped snapshots.

The script declares its only dependencies, `requests` and `parsel`, through
PEP 723 metadata. A failed or incomplete scrape does not replace the previous
successful outputs.

## Disclaimer

This project is not affiliated with or endorsed by Lolalytics or Riot Games.
The report links back to the Lolalytics cohort page used as its data source.
