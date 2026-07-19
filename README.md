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
addition by sample-adjusted marginal counterpick coverage and links each result
to a separate, shareable pair breakdown. Both tables support column sorting,
text search, and inclusive numeric minimum and maximum filters. The pair
breakdown exposes every opponent-level term behind the selected score,
including the raw rate, games, strength-only expectation, adjusted estimate,
interval, and whether a matchup has direct data. Opponent weights come from
opposing lane pick rates over one common roster. The rankings default to Zoe in
Middle, while a pair URL with no parameters defaults to Zoe + Zilean.

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

Each displayed matchup rate is adjusted toward a strength-only expectation
derived from Lolalytics Δ2. The weight assigned to that expectation is one
globally fitted empirical-Bayes concentration, disclosed in the generated page
as equivalent prior games. Lolalytics-suppressed rows use the opponent's
all-champions baseline plus the focal champion's median strength adjustment
with zero observed games and are visibly marked as prior-only. The addition's
own mirror matchup remains unavailable.

Candidates are ranked only by the sum of their positive adjusted
opponent-level contributions. Pick rate, direct-data coverage, observed-only
gain, and uncertainty are separate context rather than inputs to a hidden
composite. The observed-only gain is an unrenormalized raw-rate audit subtotal
over rows where both champions have direct data. Approximate 90% gain intervals
use a fixed point-estimate pick rule and a normal approximation to independent
directional matchup posteriors. Coverage reports base, candidate, and joint
direct data separately from modelled rows.

The results are current-snapshot, population-level evidence assuming both
champions are fully learned and the opposing laner is known. Adjustment
addresses binomial matchup sample size; it does not correct specialist
selection, individual proficiency, causal effects, or future-patch stability.
Held-out validation, stability checks, and abstention rules remain possible
downstream work rather than claims made by this project.

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
