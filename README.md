# paper_finder

Given a keyword set, collect **journal article metadata** — title, authors, affiliations, year, journal, abstract, DOI, citation count — and export it as a CSV. No PDFs are downloaded, and no subscription or API key is required.

Built for transparent conductive oxides (TCO / ITO / FTO / AZO), but the query and filters are fully configurable.

## What it does

```
keyword
   │
   ├─ 1. Crossref  query.title, offset paging, type=journal-article   (free)
   │       12000 candidates scanned -> keep only title matches
   │
   ├─ 2. keyword filter: groups ANDed, terms inside a group ORed
   │       (transparent*|tco|ito) AND (conduc*|electrode*)
   │
   ├─ 3. dedupe by DOI, tag the channel it came from
   │
   ├─ 4. OpenAlex  filter=doi:a|b|c  (100 DOIs per call)
   │       fills in year / journal / authors / institutions / abstract /
   │       citations / volume / pages / keywords
   │       abstract is rebuilt from abstract_inverted_index
   │
   └─ 5. flag pv_related, write CSV, cache in SQLite
```

## Why two sources

Neither source is sufficient alone:

| Source | Role | Notes |
|---|---|---|
| **Crossref** | discovery | free, no key, up to 1000 rows per request. Relevance ranking is good near the top and degrades with depth. |
| **OpenAlex** | enrichment | the only free source that supplies title, year, authors, **affiliations**, abstract, journal and citation count together. Bulk DOI lookups cost $0.0001 per 100 DOIs; single-record lookups are free. |

The public unauthenticated OpenAlex budget is roughly **$0.10/day (~100 list calls)**. A full 6,000-paper run costs about **$0.006**, so there is ample headroom. A free OpenAlex account raises this to $1/day, but is not needed here.

Sources that were evaluated and rejected:

- **Semantic Scholar** — anonymous access now returns 429.
- **Europe PMC** — frequently returns 503, and coverage for materials science is thin.
- **PubMed** — "transparent conducting oxide" yields only 319 records, mostly biomedical.
- **Lens / CORE / BASE / Scilit** — require registration or block scripts.
- **OpenAlex snapshot** — 745 GB, impractical for a single project.

## Install

```bash
conda create -n paper_finder python=3.11 -y
conda activate paper_finder
pip install -r requirements.txt
```

## Usage

```bash
# all four channels defined in config.yaml (~6,000 papers, ~2.5 min)
python -m pf --all -o output/tco_all.csv

# a single channel
python -m pf --channel TCO -o output/tco.csv

# several channels
python -m pf --channel TCO --channel ITO -o output/tco_ito.csv

# an ad-hoc query: words of the query are ANDed automatically
python -m pf "perovskite solar cell" --since 2020 -o output/psc.csv

# explicit filter groups: terms inside a group are ORed with |, groups are ANDed
python -m pf "transparent conductive oxide" \
    --must "transparent|tco|ito" --must "conduc*|electrode*" \
    -o output/custom.csv

# discovery only: scans Crossref and prints counts, spends no OpenAlex budget
python -m pf --all --dry-run

# ignore and skip the cache
python -m pf --all --no-cache -o output/fresh.csv
```

### Options

| Option | Meaning | Default |
|---|---|---|
| `--all` | run every channel in `config.yaml` | off |
| `--channel NAME` | run one channel, repeatable | off |
| `--must GROUP` | required keyword group, repeatable | query words |
| `--since YEAR` | earliest publication year | `2015` |
| `--depth N` | candidates scanned per query | `4000` |
| `--doc-type` | Crossref type filter | `journal-article` |
| `--match` | `title` or `title_abstract` | `title` |
| `-o, --output` | CSV path | `output/papers.csv` |
| `--config` | config path | `./config.yaml` |
| `--cache` | SQLite cache path | `output/cache.db` |
| `--no-cache` | ignore and do not write the cache | off |
| `--dry-run` | discovery only, no OpenAlex calls | off |

## Output

One row per paper, 17 columns:

```
title, authors, affiliations, year, journal, abstract, doi,
citations, oa_status, url, openalex_id, source,
volume, pages, keywords, channel, pv_related
```

Formatting conventions:

- `authors` — `; ` separated, in the order the source lists them.
- `affiliations` — `; ` separated and **positionally aligned with `authors`**; the nth entry belongs to the nth author. An author with several institutions joins them with ` | `, and trailing blanks are trimmed. This keeps the cell parseable while preserving the author ↔ affiliation mapping.
  ```
  authors:      张三; 李四; Wang Wu
  affiliations: Tsinghua University; Chinese Academy of Sciences | Peking University; MIT
  ```
- `abstract` — reconstructed from OpenAlex's `abstract_inverted_index`; falls back to the Crossref abstract when OpenAlex has none.
- `pages` — `first_page-last_page`.
- `keywords` — OpenAlex keywords, `; ` separated.
- `channel` — which configured channel produced the row: `TCO`, `ITO`, `FTO`, `AZO`, or `custom`.
- `pv_related` — `yes` / `no`. The paper mentions photovoltaics in its title or abstract. **This never removes rows**; it only labels them.
- `source` — `crossref+openalex` normally, `crossref` when OpenAlex had no record for that DOI.
- Encoding is UTF-8 with BOM so Excel opens Chinese and accented names without garbling.

## Keyword matching

All text is lowercased and every run of non-alphanumeric characters is collapsed to a space, so `SnO2:F` becomes `sno2 f`. Matching is then done on **whole tokens**, which is why `ito` cannot match `composition` and `azo` cannot match `amazon`.

A trailing `*` makes a term a prefix match:

```
conduc*    -> conducting, conductive, conductivity, conductor
solar      -> solar only
```

A `must` list is a list of **groups**. Groups are ANDed, terms inside a group are ORed.

Why the TCO channel uses only two groups:

```yaml
- [transparent*, tco, ito, azo, gzo, fto, itazo, "indium tin oxide"]
- [conduc*, electrode*]
```

Adding a third group requiring `oxide` cut the result set from 90% to 44% retention and **wrongly dropped 149 photovoltaic papers** such as `Transparent Conductive Nanofiber Paper for Foldable Solar Cells`, where the title never says "oxide". Two groups keep the relevant literature and still reject proton-conducting solid-oxide fuel cells and ionic conductors.

The ITO / FTO / AZO channels use three groups, because their primary term is also a very common word sequence. For ITO, two groups leave noise such as `The toxicology of indium tin oxide` in the results; a third group of context words (film, layer, electrode, solar, deposition, …) removes it without losing photovoltaic papers.

## Configuration

`config.yaml` holds the email used for the polite pools, global defaults, channel definitions and the photovoltaic keyword list.

```yaml
email: 469900626@qq.com

defaults:
  since: 2015
  doc_type: journal-article
  depth: 4000
  page_size: 1000
  oa_batch: 100
  min_remaining_usd: 0.02
  cache: true
  match: title

channels:
  TCO:
    - query: transparent conductive oxide
      must:
        - [transparent*, tco, ito, azo, gzo, fto, itazo, "indium tin oxide"]
        - [conduc*, electrode*]
    - query: transparent conducting oxide      # both spellings are required
      must:
        - [transparent*, tco, ito, azo, gzo, fto, itazo, "indium tin oxide"]
        - [conduc*, electrode*]
```

Both spellings matter: "transparent conductive oxide" and "transparent conducting oxide" barely overlap in Crossref (945 versus 837 title hits), so dropping either one loses roughly half the literature.

To add a channel, append a new key under `channels` with a list of `{query, must}` entries, then run `python -m pf --channel YOURS`.

## Measured results

Run of `--all` on 2026-11-01, `since: 2015`, `depth: 4000` per query:

| Metric | Value |
|---|---|
| Crossref requests | 20 (free) |
| Unique DOIs discovered | 6,027 |
| Rows after year filter | **5,971** |
| — TCO / ITO / FTO / AZO | 3,651 / 1,712 / 323 / 285 |
| OpenAlex matches | 5,951 (99.7%) |
| OpenAlex cost | **$0.0058** |
| Journal present | 100% |
| Year present | 100% |
| Keywords present | 99.7% |
| Volume present | 97.9% |
| Affiliation present | 94% |
| Abstract present | 64% |
| `pv_related = yes` | 1,487 (25%) |
| Runtime | ~2.5 min |

Abstract coverage is capped by the sources themselves — OpenAlex supplies an abstract for roughly two thirds of works and Crossref for roughly a third — not by the tool.

## Caching

Every enriched paper is stored in SQLite keyed by DOI (`output/cache.db`). A rerun re-scans Crossref but skips the OpenAlex calls for DOIs already present, so repeated runs consume almost no budget. Use `--no-cache` to bypass it, or delete the file to start fresh.

## Budget safety

Each OpenAlex response carries `X-RateLimit-Remaining-USD`. When the remaining budget falls below `min_remaining_usd` (default `$0.02`), the run stops enriching and still writes the CSV using whatever metadata it has, rather than failing outright.

## Project layout

```
config.yaml                 channels, defaults, pv keywords
pf/
  cli.py                    argument parsing and pipeline orchestration
  models.py                 Paper dataclass, CSV columns, DOI normalisation
  filters.py                token-level keyword matching
  export.py                 CSV writer
  store.py                  SQLite cache
  llm.py                    optional LLM hook (no-op by default)
  sources/
    crossref.py             discovery
    openalex.py             enrichment, inverted-index decoding
tests/
  test_filters.py           offline unit tests
output/                     generated CSV and cache (gitignored)
```

## Tests

```bash
python -m pytest tests/ -q
```

The tests are pure logic and never touch the network. They cover token boundaries (`ito` versus `composition`), prefix terms, multi-word terms, group AND/OR semantics, and the photovoltaic flag.

## Limitations

- **Journal articles only.** Conference papers are excluded by `type:journal-article`; pass `--doc-type proceedings-article` to include them.
- **Crossref depth is capped.** Crossref rejects `offset + rows > 10000`, so at most 10,000 candidates per query can be scanned. Relevance also degrades with depth: the first 1,000 records are ~90% on-topic, and by offset 3,000 that drops to ~37%.
- **Abstracts are missing for ~36% of papers.** Neither source has them; this is a metadata gap, not a bug.
- **Affiliations reflect OpenAlex's data.** Some publishers deposit institution names only for the corresponding author, so the list can contain blanks for other authors.
- **No PDF download.** The tool deliberately collects metadata only, since full text usually sits behind a subscription.

## Data sources and attribution

Metadata comes from [Crossref](https://www.crossref.org/) and [OpenAlex](https://openalex.org/). OpenAlex data is published under [CC0](https://creativecommons.org/publicdomain/zero/1.0/); Crossref metadata is openly available under its terms of use. Please keep the `email` in `config.yaml` accurate — both services use it to route requests into their polite pools.

## License

MIT