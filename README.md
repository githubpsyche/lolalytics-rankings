# Hextech Studies

Small, source-explicit tools and data explorations for
[League of Legends](https://www.leagueoflegends.com/).

**Live site:** https://githubpsyche.github.io/hextech-studies/

## Projects

### [Lolalytics Champion Rankings](https://githubpsyche.github.io/hextech-studies/champion-rankings/)

An interactive comparison of lane-specific champion statistics scraped from
[Lolalytics](https://lolalytics.com/). It supports sorting, inclusive minimum
and maximum filters (including on hidden columns), category and individual
column visibility controls, tier filters, and switching among Top, Jungle,
Middle, Bottom, and Support.

Refresh its data and generated page with:

```bash
uv run projects/champion-rankings/build.py
```

Defaults are Emerald+, Global, Ranked Solo/Duo, all five lanes, and the rolling
30-day period. A targeted run is also available:

```bash
uv run projects/champion-rankings/build.py \
  --lane middle \
  --tier emerald_plus \
  --region all \
  --period 30
```

The script declares its `requests` and `parsel` dependencies through PEP 723
metadata. Each successful run atomically updates:

- `projects/champion-rankings/data/latest.json`;
- a local timestamped snapshot under
  `projects/champion-rankings/data/archive/`; and
- `docs/champion-rankings/index.html`.

The existing historical snapshots remain in the repository. New timestamped
snapshots are ignored by Git, while `latest.json` and the generated page stay
tracked.

## Repository layout

```text
docs/                         GitHub Pages output
  index.html                  Hextech Studies homepage
  champion-rankings/          generated rankings page
projects/
  champion-rankings/
    build.py                  scraper and generator
    template.html             standalone page template
    data/                     latest data and local archives
```

Projects remain vertically self-contained: each may use its own data sources,
schema, dependencies, validation, and build process. A shared framework will
only be introduced if multiple projects reveal a concrete need for one.

## Disclaimer

Hextech Studies is not affiliated with or endorsed by Riot Games. Individual
projects identify and link to their own data sources; the champion rankings
project is also not affiliated with or endorsed by Lolalytics.
