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

An exploratory table for expanding a one-champion pool to two champions.
Choose the champion you already play; the landing page ranks every valid
addition by marginal observed counterpick coverage and links each result to a
separate, shareable pair breakdown. Both tables support column sorting, text
search, and inclusive numeric minimum and maximum filters. The pair breakdown
exposes every opponent-level term behind the selected score. Opponent weights
come from the current champion's displayed matchup universe and opposing lane
pick rates. The rankings default to Zoe in Middle, while a pair URL with no
parameters defaults to Zoe + Zilean.

Refresh its data and generated pages with:

```bash
uv run projects/counterpick-coverage/build.py
```

Defaults are Zoe as the initial current champion, Zilean as the initial
addition, Middle, Emerald+, Global, Ranked Solo/Duo, and the rolling 30-day
period. Every scraped champion remains selectable in the generated pages. Build
flags can change the initial pair, lane, tier, or period; Global and Ranked
Solo/Duo remain fixed:

```bash
uv run projects/counterpick-coverage/build.py \
  --lane middle \
  --base zoe \
  --candidate veigar \
  --tier emerald_plus \
  --period 30
```

The script uses the same dependency-light PEP 723 pattern as the rankings
project. A successful scrape atomically updates:

- `projects/counterpick-coverage/data/latest.json`;
- an ignored local timestamped snapshot under
  `projects/counterpick-coverage/data/archive/`;
- `docs/counterpick-coverage/index.html`, the addition rankings; and
- `docs/counterpick-coverage/pair/index.html`, the linked pair breakdown.

Generated URLs keep the current selection shareable:

```text
https://githubpsyche.github.io/hextech-studies/counterpick-coverage/?base=zoe
https://githubpsyche.github.io/hextech-studies/counterpick-coverage/pair/?base=zoe&candidate=zilean
```

To rebuild only the pages after editing their shared template:

```bash
uv run projects/counterpick-coverage/build.py --render-only
```

Run the small hand-checkable calculation fixtures with:

```bash
uv run projects/counterpick-coverage/test_build.py
```

This first pass deliberately uses observed matchup win rates. Each scrape
stores the directed matchup rows for every champion in the selected lane,
allowing the page to rebuild the opponent universe, weights, and addition
rankings whenever the current champion changes. Missing addition rows receive
no demonstrated improvement, weights are never renormalized per addition, and
the addition's own mirror matchup is unavailable. Scores are conditional on
opponents for which the selected current champion has a displayed matchup
baseline; universe coverage makes the omitted pick-rate weight explicit, while
evidence coverage reports how much applicable weight has an addition estimate.
Shrinkage and uncertainty are reserved for a later statistical milestone.

## Repository layout

```text
docs/                         GitHub Pages output
  index.html                  Hextech Studies homepage
  champion-rankings/          generated rankings page
  counterpick-coverage/       generated addition rankings and pair breakdown
projects/
  champion-rankings/
    build.py                  scraper and generator
    template.html             standalone page template
    data/                     latest data and local archives
  counterpick-coverage/
    build.py                  scraper, calculator, and generator
    test_build.py             hand-checkable calculation fixture
    template.html             shared rankings/pair page template
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
