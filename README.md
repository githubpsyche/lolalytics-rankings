# Lolalytics Champion Rankings

An unofficial, lightweight scraper and interactive ranking table for the
server-rendered champion statistics on [Lolalytics](https://lolalytics.com/).

**Live table:** https://githubpsyche.github.io/lolalytics-rankings/

The generated page supports:

- single-column ascending and descending sorting;
- inclusive minimum and maximum filters for every metric;
- optional metric columns; and
- configurable lane, tier, region, and period.

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
