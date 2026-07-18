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

### [Counterpick Coverage](https://githubpsyche.github.io/hextech-studies/counterpick-coverage/)

An exploratory table ranking additions to a champion pool by marginal expected
counterpick coverage. The first published analysis uses Zoe in Middle as the
current pool and weights each displayed matchup by the opposing champion's
lane pick rate. Expanding any candidate reveals every opponent-level term that
sums to its score.

Refresh its data and generated page with:

```bash
uv run projects/counterpick-coverage/build.py
```

Defaults are Zoe, Middle, Emerald+, Global, Ranked Solo/Duo, and the rolling
30-day period. The current pool and cohort can be changed when rebuilding:

```bash
uv run projects/counterpick-coverage/build.py \
  --lane middle \
  --pool zoe veigar \
  --tier emerald_plus \
  --period 30
```

The script uses the same dependency-light PEP 723 pattern as the rankings
project. A successful scrape atomically updates:

- `projects/counterpick-coverage/data/latest.json`;
- an ignored local timestamped snapshot under
  `projects/counterpick-coverage/data/archive/`; and
- `docs/counterpick-coverage/index.html`.

To rebuild only the page after editing its template:

```bash
uv run projects/counterpick-coverage/build.py --render-only
```

Run the small hand-checkable calculation fixture with:

```bash
uv run projects/counterpick-coverage/test_build.py
```

This first pass deliberately uses observed matchup win rates. Missing candidate
rows receive no demonstrated improvement, weights are never renormalized per
candidate, and weighted evidence coverage is reported. Scores are conditional
on opponents for which the current pool has a displayed matchup baseline;
universe coverage makes the omitted pick-rate weight explicit. For a
multi-champion pool, the baseline uses the best displayed pool-member estimate
and records how many pool members supplied evidence. Shrinkage and uncertainty
are reserved for a later statistical milestone.

## Repository layout

```text
docs/                         GitHub Pages output
  index.html                  Hextech Studies homepage
  champion-rankings/          generated rankings page
  counterpick-coverage/       generated coverage page
projects/
  champion-rankings/
    build.py                  scraper and generator
    template.html             standalone page template
    data/                     latest data and local archives
  counterpick-coverage/
    build.py                  scraper, calculator, and generator
    test_build.py             hand-checkable calculation fixture
    template.html             standalone page template
    data/                     latest data and local archives
```

Projects remain vertically self-contained: each may use its own data sources,
schema, dependencies, validation, and build process. A shared framework will
only be introduced if multiple projects reveal a concrete need for one.

## Disclaimer

Hextech Studies is not affiliated with or endorsed by Riot Games. Individual
projects identify and link to their own data sources; the champion rankings
and counterpick coverage projects are also not affiliated with or endorsed by
Lolalytics.
