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
| `--download-pdfs` | fetch open-access PDFs in a browser after writing the CSV | off |
| `--pdf-dir` | where PDFs are stored | `output/pdfs` |
| `--pdf-limit N` | stop after N download attempts | all |
| `--pdf-engine` | `browser` or `http` | `browser` |
| `--show-browser` | visible browser window (helps with captchas) | off |
| `--include-closed` | also probe rows marked `closed` | off |
| `--refs` | write RIS and BibTeX next to the CSV | off |
| `--from-csv PATH` | skip discovery; operate on an existing CSV | — |

## Getting the PDFs

The CSV holds metadata only. `--download-pdfs` additionally fetches the
**open-access** full text and saves it as
`output/pdfs/<year>/<surname>_<year>_<journal>.pdf`.

Downloads run through a **real Chromium session**, because plain HTTP requests
are refused by almost every publisher. Install the browser once:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

On a machine without root, the browser also needs a few system libraries.
Install them into the conda environment instead:

```bash
conda install -c conda-forge at-spi2-core atk libxkbcommon libgbm \
    xorg-libxdamage xorg-libxcomposite xorg-libxrandr xorg-libxfixes \
    xorg-libxext xorg-libxrender xorg-libxtst alsa-lib
```

The tool adds the environment's `lib/` directory to `LD_LIBRARY_PATH` itself,
so no shell configuration is needed.

```bash
# from scratch: collect metadata, then download
python -m pf --all -o output/tco_all.csv --download-pdfs

# reuse an existing CSV: no re-crawling, no OpenAlex cost
python -m pf --from-csv output/tco_all.csv --download-pdfs

# only the first 50 attempts
python -m pf --from-csv output/tco_all.csv --download-pdfs --pdf-limit 50

# see what the browser is doing (useful when a captcha appears)
python -m pf --from-csv output/tco_all.csv --download-pdfs --show-browser

# fast, no browser: mostly blocked, but fine behind a proxy or on a campus network
python -m pf --from-csv output/tco_all.csv --download-pdfs --pdf-engine http
```

### Expectations: this is not a bulk PDF mirror

Measured on random 30-paper samples of open-access rows from a 5,971-row run:

A full run over all 2,287 open-access rows from this table:

| Outcome | Rows | Share |
|---|---|---|
| PDF saved | **751** | 33% |
| Bot challenge / hard block | 906 | 40% |
| Landing page with no downloadable file | 630 | 27% |
| Plain HTTP engine instead | ~16% | — |

Successful downloads take 1-4 seconds; failures cost 8-25 seconds because the
page has to load before it can be classified. Expect roughly one paper every
12 seconds, so a 2,300-row run takes several hours. Interrupting is safe:
papers already on disk are skipped on the next run.

The hard ceiling is the metadata itself: of 5,971 rows, **2,287 are open access**
and the other 3,684 are `closed` — no legal free copy exists for those, and the
tool never reaches for shadow libraries. `--include-closed` rarely helps; in a
60-paper probe just one had a PDF link anywhere in OpenAlex.

What the browser fixes, and what it does not:

| Host | Plain HTTP | Browser |
|---|---|---|
| Nature / Scientific Reports / PRL | 403 | **downloads** |
| MDPI | 403 | **downloads** |
| Wiley, IOP | 403 | mostly still blocked |
| RSC, ACS, AIP, ScienceDirect, Taylor & Francis | 403 | Cloudflare challenge never clears |
| HAL | "not a bot" page | same |
| PMC | 403 | reCAPTCHA |
| IEEE | perfdrive check | same |

The pattern is that datacentre IPs are the real blocker. The sample run came
from a Hong Kong cloud address (`AS202736`), which is exactly what Cloudflare
and friends are built to reject. **Run this from a campus network or a
residential connection and the success rate is materially higher** — the code
is unchanged, only the address differs.

Hosts that never clear a challenge are detected and skipped quickly rather
than waiting out a long timeout on each.

The browser catches a PDF two ways, because publishers deliver it two ways:
a route handler inspects every response for a `application/pdf` body (this is
how an inline PDF is caught), and a download listener catches the save prompt.
Whichever fires first wins, which is why a good download finishes in a couple
of seconds.

### The manual remainder

Rows that could not be fetched are written to `output/download_failed.csv`, with
`output/references/download_failed.ris` and `.bib` alongside. Import the RIS into
Zotero and use the Zotero Connector with your institutional access — a real
browser session with your cookies resolves the rest.

## Reference files

`--refs` writes two files next to the CSV, under `output/references/`:

- `<name>.ris` — one `TY  - JOUR` record per paper, with `AU`, `PY`, `T2`, `VL`, `SP`/`EP`, `AB`, `KW`, `DO`, `UR`. The `N1` note records `oa_status` and `channel`. Imports cleanly into Zotero, EndNote and Mendeley.
- `<name>.bib` — `@article` entries keyed `<surname><year>_<n>`, with LaTeX-escaped `&`, `{`, `}`.

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

To add a channel, append a new key under `channels` with a list of `{query, must}` entries, then run `python -m pf --channel YOURS`. A query entry may also set `match: title_abstract` to widen matching beyond the title, and `depth` to scan deeper than the global default.

### Deposition technique: `TCO_SPUTTER`

`TCO` alone returns every transparent conducting oxide, regardless of how it
was made. `TCO_SPUTTER` narrows that to films grown by **magnetron sputtering**,
which is what this project uses:

```bash
python -m pf --channel TCO_SPUTTER -o output/tco_magnetron.csv
```

Matching runs on **title and abstract**, because only about 4% of TCO titles
name the technique while the abstract almost always does. A paper must satisfy
all three groups:

1. the TCO term (`transparent*`, `tco`, `ito`, `azo`, `gzo`, `fto`, `indium tin oxide`)
2. the conductive/electrode term
3. the technique term — **`magnetron`**, not bare "sputter"

Requiring `magnetron` rather than `sputter*` is deliberate: it excludes
ion-beam sputtering, and the project's films come off a magnetron target.

Five Crossref queries feed the channel, merged by DOI:

```
transparent conductive oxide magnetron sputtering
transparent conducting oxide magnetron sputtering
magnetron sputtered transparent conductive oxide
magnetron sputtering transparent conducting oxide film
magnetron sputtered indium tin oxide
```

Each is scanned to depth 10,000 (Crossref's hard ceiling). Shallow scans are
misleading here: the first 1,000 candidates yield 152 matches, but offsets
1,000-9,000 add another 175, so a depth of 4,000 loses about a third of the
literature.

Measured result: **480 papers**, 77% with an abstract, 92% with affiliations.
Only two rows lack the word "magnetron" in their stored text, and both are
records where Crossref supplied no abstract.

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
  export.py                 CSV writer and reader
  store.py                  SQLite cache
  pdf.py                    link resolution and the plain-HTTP fetcher
  browser.py                Chromium fetcher (default; clears JS checks)
  refs.py                   RIS / BibTeX export
  llm.py                    optional LLM hook (no-op by default)
  sources/
    crossref.py             discovery
    openalex.py             enrichment, inverted-index decoding
tests/
  test_filters.py           keyword matching
  test_export.py            CSV round-tripping
  test_pdf.py               file naming and reference export
output/                     generated CSV, PDFs and cache (gitignored)
  pdfs/<year>/              downloaded PDFs
  references/               RIS and BibTeX
  download_failed.csv       rows that still need a manual fetch
```

## Tests

```bash
python -m pytest tests/ -q
```

The tests are pure logic and never touch the network. They cover token boundaries (`ito` versus `composition`), prefix terms, multi-word terms, group AND/OR semantics, the photovoltaic flag, PDF filename generation, CSV round-tripping (including the blank-affiliation alignment case), and the RIS/BibTeX writers.

## Limitations

- **Journal articles only.** Conference papers are excluded by `type:journal-article`; pass `--doc-type proceedings-article` to include them.
- **Crossref depth is capped.** Crossref rejects `offset + rows > 10000`, so at most 10,000 candidates per query can be scanned. Relevance also degrades with depth: the first 1,000 records are ~90% on-topic, and by offset 3,000 that drops to ~37%.
- **Abstracts are missing for ~36% of papers.** Neither source has them; this is a metadata gap, not a bug.
- **Affiliations reflect OpenAlex's data.** Some publishers deposit institution names only for the corresponding author, so the list can contain blanks for other authors.
- **PDF download works for roughly a third of papers.** Measured end-to-end: 751 of 2,287 open-access rows. The rest sit behind anti-bot protection, and the rate depends heavily on the network — a campus or residential connection does considerably better than a datacentre (the reference run came from a Hong Kong cloud address), because that is what the protections key on. Use the `download_failed.ris` + Zotero route for the remainder.
- **No paywall circumvention.** Rows marked `closed` are never fetched from shadow libraries; they are left for your institutional subscription.
- **Reference exports require a DOI.** Records without one are skipped by the RIS/BibTeX writers rather than emitted with a guessed key.

## Data sources and attribution

Metadata comes from [Crossref](https://www.crossref.org/) and [OpenAlex](https://openalex.org/); open-access locations come from [Unpaywall](https://unpaywall.org/). OpenAlex data is published under [CC0](https://creativecommons.org/publicdomain/zero/1.0/); Crossref metadata is openly available under its terms of use. Please keep the `email` in `config.yaml` accurate — all three services use it to route requests into their polite pools.

When downloading full text, respect each publisher's terms of use and any licence attached to the individual article. `oa_status = bronze` means free to read on the publisher's site, which is not the same as openly licensed for redistribution.

## License

MIT