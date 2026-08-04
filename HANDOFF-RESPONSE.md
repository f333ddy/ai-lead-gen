# Handoff Response — Scraper/Enrichment Pipeline → Lavi Sales Atlas

**From:** the scraper/AI-augmentation pipeline repo (`ai-lead-gen`, branch `master` @ `d0dc03c`)
**To:** the Lavi Sales Atlas repo / Neon `neondb`
**Date:** 2026-08-03

Answers §4–§8 of the handoff, and resolves every open question in §9. Findings marked **[VERIFIED]** were reproduced by running the code, not inferred.

---

## 0. Executive summary

**The good news is bigger than expected.** The current pipeline (`new-ai-lead-gen.py` + `scrapers/`) already emits per-article dicts whose keys are almost exactly the `raw_documents` column set. This was clearly built against the same contract. Only **one** column has no producer at all (`scraper_source`).

Three headline findings:

1. **All 6 active sources scrape full article body text.** This was flagged as "the biggest single risk to §5" — it is not a risk. Every source extracts real paragraph text, not summaries or teasers. §5 is essentially unblocked.
2. **The AI gate already computes `eligible`, `confidence`, `triggers`, and `evidence_quotes` — and then throws them away.** `test_run_eligibility_gate()` returns only `content["extracted"]` and discards the top-level keys. Four "will we be able to fill this?" columns are a ~5-line change, not new AI work.
3. **`company_id` is the only genuinely new build.** There is no company resolution, no address extraction, and no geocoding anywhere in the repo. This is the real work item, and §6's proposed approach needs one substantive correction (see §6 below — the Geocoding API is the wrong API for this input).

Both §9 data-format mysteries are resolved and both are pipeline-side bugs with clean fixes.

**Scope note:** v1 targets the core write path only. `enriched_documents.industries` is **deliberately not persisted** in v1, and a number of data-quality and hygiene items are consciously deferred. Everything deferred is catalogued in **§11**, with the reversibility cost of each. One deferral is lossy; the rest are free or backfillable.

---

## 1. What the pipeline actually is (orientation for the Atlas side)

There are **two** entrypoints in the repo. Read the right one:

| File | Status | Shape it emits |
|---|---|---|
| [new-ai-lead-gen.py](new-ai-lead-gen.py) | **Current.** Imports the `scrapers/` package. | `{published_at, discovered_at, title, url, content, language, language_confidence, source_domain, source_name, document_type}` — i.e. `raw_documents` |
| [ai-lead-gen.py](ai-lead-gen.py) | Legacy monolith, superseded. Inline scraper functions. | `{title, link, description}` |

All persistence work should target `new-ai-lead-gen.py` and the `scrapers/` package. The legacy file should be deleted once the migration lands, with one exception noted in §4a.

Current run flow ([new-ai-lead-gen.py:354-379](new-ai-lead-gen.py#L354-L379)):

```
6 scrapers → docs[]  →  test_run_eligibility_gate()  →  extracted[]
                              (OpenAI, per doc)
                                    ↓
       HubSpot industry→team map → bucket_articles_by_team() → team_buckets{}
                                    ↓
                    test_send_emails_to_teams() + send_filtered_email()
```

**There is no database code, no DB driver, and no `DATABASE_URL` anywhere in the repo or its git history.** This migration starts from zero on the persistence side.

### On `casinoorgnews`

The handoff cites 3 existing `raw_documents` rows from a `casinoorgnews` scraper as evidence the shape is known. **No such scraper exists in this repo, at any commit** (verified against `git log --diff-filter=A --name-only`). However, its stored `source_domain='casino'` is byte-for-byte what this repo's `tldextract` convention produces for `casino.org` — so it was written against this same document contract, just not from this codebase. Please confirm where it lives; if it is a fourth source we don't know about, it needs to be folded into `scrapers/` or it will drift.

---

## 2. §4a — Per-site field inventory

**Six active sources**, one of which (EventRegistry) is an aggregator fanning out to 8 publishers.

| Site / proposed `scraper_source` | Module | Fields currently extracted | Full body text? | Publish date? | Notes |
|---|---|---|---|---|---|
| **`eventregistry`** | [scrapers/eventregistry/](scrapers/eventregistry/) | `published_at` (API `dateTime`), `discovered_at`, `title`, `url`, `content` (API `body`), `source_name` (API `source.title`), `source_domain` (API `source.uri`), `document_type` | **yes** — API returns full `body` | **yes** — real datetime from API | **Only source missing `language`/`language_confidence`** ([transforms.py:5-19](scrapers/eventregistry/transforms.py#L5-L19)). `source_domain` is a **FQDN** here (`retaildive.com`), unlike every other source. Filters API `isDuplicate`. |
| **`airportindustrynews`** | [scrapers/airport_industry_news.py](scrapers/airport_industry_news.py) | full set incl. `language`, `language_confidence` | **yes** — all `<p>` in `article.article__body` | **yes** — ISO from `<time datetime>` attr | Cleanest source. Paginates until it hits yesterday. `source_domain` = `airportindustry-news`. |
| **`chainstoreage`** | [scrapers/chainstoreage.py](scrapers/chainstoreage.py) | full set | **yes** — `div.eiq-paragraph p, li`, with JSON-LD `articleBody` fallback | **yes** — JSON-LD `datePublished` | Most robust date parsing (14 accepted formats). Needs `premium_proxy=True` on the index page. |
| **`nacs`** | [scrapers/nacs.py](scrapers/nacs.py) | full set | **yes** — `div.nacs-page-content p` | **⚠️ synthetic** | `published_at` is hardcoded to **today at 00:00 UTC** for every article ([nacs.py:34](scrapers/nacs.py#L34)). The comment says NACS's own schema.org always reports midnight — so this is faithful to the source, but it is a date, not a timestamp. |
| **`nahb`** | [scrapers/nahb.py](scrapers/nahb.py) | full set | **yes** — `.rich-text p` | **⚠️ date only** | Parsed from `"Mar 24, 2026"` → midnight UTC. No time component available on the listing page. |
| **`prnewswire`** | [scrapers/prnewswire.py](scrapers/prnewswire.py) | full set | **yes** — `section.release-body p` | **⚠️ time only + assumed today** | Listing shows `"14:30 ET"` for today's items; the date is stamped from `datetime.now()`. Drops docs with empty content and non-English docs. Hard-capped at 3 pages. |

**Not currently active but relevant:**

- **EIN Presswire** — `get_ein_presswire()` exists in the legacy [ai-lead-gen.py:791-863](ai-lead-gen.py#L791-L863) and was **never ported** to `scrapers/`. A working source was silently dropped in the refactor. Recommend porting it (it's ~40 lines) before deleting the legacy file.
- **AAAE** — [scrapers/aaae.py](scrapers/aaae.py) is a 0-byte placeholder. American Association of Airport Executives, presumably planned.

**EventRegistry publisher fan-out** (currently enabled, [payloads.py:6-28](scrapers/eventregistry/payloads.py#L6-L28)): `constructiondive.com`, `fooddive.com`, `grocerydive.com`, `manufacturingdive.com`, `restaurantdive.com`, `retaildive.com`, `supplychaindive.com`, `truckingdive.com`. Nine more (banking, biopharma, esg, healthcare, highered, medtech, smartcities, utility, waste) are commented out and can be re-enabled without code changes.

**Recommendation on `scraper_source` for EventRegistry:** use the single value `eventregistry` (it identifies the *collector*, matching the column's documented meaning) and let `source_domain` carry publisher-level granularity for dashboard filtering. Writing 8 different `scraper_source` values would misrepresent one code path as eight.

---

## 3. §4b — Mapping to `raw_documents`

| `raw_documents` column | Status | Detail |
|---|---|---|
| `url` | ✅ **have it** | All 6. Absolute and canonicalized via `urljoin`. Safe as the dedupe key. |
| `title` | ✅ **have it** | All 6. |
| `content` | ✅ **have it** | All 6 produce real body text. See caveats below. |
| `published_at` | ⚠️ **some sites** | Trustworthy on 3 (eventregistry, airportindustrynews, chainstoreage). Degraded on 3 (nacs synthetic-midnight, nahb date-only, prnewswire date-assumed). **Also a type problem** — see below. |
| `discovered_at` | ⚠️ **have it, but wrong semantics** | All 6 populate it, but 4 use `du.DATE_NOW`, a **module-import-time constant** ([date_utils.py:6](date_utils.py#L6)). Every document in a run gets an identical timestamp equal to process start, not discovery. Harmless today; wrong once this is the system of record. |
| `scraper_source` | ❌ **missing everywhere** | **No scraper emits this.** Column is `NOT NULL`, so inserts fail without it. One-line addition per scraper. |
| `source_name` | ✅ **have it** | All 6. Hardcoded per scraper, from API for eventregistry. |
| `source_domain` | ⚠️ **have it, two incompatible formats** | See §9-A. Scraped sites emit a bare label (`chainstoreage`); eventregistry emits a FQDN (`retaildive.com`). |
| `document_type` | ✅ **have it** | All 6, always the literal `"news"`. |
| `language` | ⚠️ **5 of 6** | Missing on eventregistry. |
| `language_confidence` | ⚠️ **5 of 6, and the unit is wrong** | Missing on eventregistry; where present it's a log-prob, not a confidence. See §9-B. |
| `created_at` / `updated_at` | ✅ auto | DB defaults. |

**`published_at` type inconsistency** — the pipeline emits three different Python types for this field:

| Source | Emitted type |
|---|---|
| eventregistry | `str` (raw API string, unparsed) |
| prnewswire | `str` (`.isoformat()`) |
| airportindustrynews, chainstoreage, nahb, nacs | `datetime` object |

Psycopg will adapt `datetime` correctly and *may* coerce the strings, but eventregistry's value is passed through completely unvalidated. Normalize all six to tz-aware UTC `datetime` in a shared helper before insert.

**`content` caveats** (none blocking, all worth knowing):
- **eventregistry** body length is EventRegistry-plan-dependent. If the plan returns truncated bodies, this is the one source where §5 quality could quietly degrade. Worth a length histogram after the first persisted run — trivial to check once rows are in the DB, impossible to check today.
- **prnewswire** silently `continue`s on empty content, so those articles never reach the DB at all. Once persisting, prefer storing the raw row with `content=NULL` so we can see what we're losing.
- **chainstoreage** JSON-LD fallback applies `re.sub(r"(?<=[a-z])(?=[A-Z])", " ", ...)` to fix run-together camelCase text — a heuristic that will occasionally split legitimate intra-word capitals ("iPhone", "McDonald's"). Cosmetic.

---

## 4. §4c — Gap summary

**Have for all 6 sites:** `url`, `title`, `content`, `discovered_at`, `source_name`, `source_domain`, `document_type`

**Have for some sites:**
- `language` / `language_confidence` — 5 of 6 (missing: eventregistry)
- Reliable `published_at` — 3 of 6 (degraded: nacs, nahb, prnewswire)

**Missing everywhere:** `scraper_source` — **not OK to leave empty**, the column is `NOT NULL`. Must be added before the first insert.

**Present but needs format correction before it's useful:**
- `source_domain` — two formats in play (§9-A)
- `language_confidence` — log-prob, not a confidence (§9-B)
- `published_at` — mixed Python types

---

## 5. §5 — Augmentation feasibility matrix

The gate is a single OpenAI structured-output call ([new-ai-lead-gen.py:203-220](new-ai-lead-gen.py#L203-L220)) against the schema in [eligibility_schema.py](eligibility_schema.py). It returns:

```
{ eligible, confidence, triggers[], evidence_spans[{quote}],
  extracted: { amount_usd, fiscal_year, location, doc_date,
               summary, article_link, title, industries[], company } }
```

**The critical finding:** [new-ai-lead-gen.py:239](new-ai-lead-gen.py#L239) appends only `extracted`. The top-level `eligible`, `confidence`, `triggers`, and `evidence_spans` are computed by the model, paid for, and then dropped on the floor. Four of the "can we fill this?" columns are already being generated.

| `enriched_documents` field | Feasibility | Notes |
|---|---|---|
| `raw_document_id` | ⚠️ **plumbing needed** | Not blocked by data — blocked by structure. `extracted` is returned orphaned from its source doc; there is no handle back to the raw row. Fix: return `(raw_doc, gate_response)` pairs instead of bare `extracted`. |
| `url` | ✅ **fulfillable now** | Take from the raw doc, **not** from AI-echoed `article_link` — the model can and does reformat URLs. Use `article_link` for display only. |
| `title` | ✅ **fulfillable now** | Prefer the scraped raw title over the AI echo, same reasoning. |
| `summary` | ✅ **fulfillable now** | Prompt explicitly requests 1–2 sentences ([prompts.py:87](prompts.py#L87)). |
| `company` | ✅ **fulfillable now** | Prompt targets the primary commercial entity and de-prioritizes governments/regulators ([prompts.py:78-80](prompts.py#L78-L80)). **Free text, unnormalized** — "Walmart", "Walmart Inc.", "Wal-Mart Stores" are three distinct strings today. Matters for §6. |
| `amount_usd` | ⚠️ **partial — content-limited** | Schema-nullable; populated only when the article states a figure. Genuinely absent from most articles. Not fixable upstream. |
| `fiscal_year` | ⚠️ **partial — content-limited** | Same. |
| `company_id` | ❌ **blocked — new build required** | No company resolution, no geocoding, no address extraction exists. See §6. **This is the one substantial new engineering item in the whole handoff.** |
| `doc_date` | ✅ **fulfillable now, but use the wrong-ish source** | The AI's `doc_date` is an unconstrained free-text string. `raw_documents.published_at` is a real timestamp we already have. **Recommend populating `doc_date` from `published_at`**, and treating the AI's value as a fallback only when `published_at` is null. |
| `article_link` | ✅ **fulfillable now** | From the raw doc. |
| `eligible` | ✅ **fulfillable now — currently discarded** | Computed at [new-ai-lead-gen.py:235](new-ai-lead-gen.py#L235), never persisted. |
| `confidence` | ✅ **fulfillable now — currently discarded** | Real 0–1 float, schema-constrained. Gate threshold is 0.80 ([new-ai-lead-gen.py:52](new-ai-lead-gen.py#L52)). |
| `industries` | ⛔ **deferred — out of scope for v1** | Technically fulfillable now (hard-constrained HubSpot enum), but **deliberately not persisted** in v1. Team routing still derives from industries in memory. See §11. |
| `triggers` | ✅ **fulfillable now — currently discarded** | 4-value enum: `New funding approved`, `Remodel/renovation announced`, `Department allocation relevant to our services`, `Active procurement/RFP/RFQ`. Note these are *sentences*, not the terse tags §5 imagines ("funding", "expansion", "RFP"). Either Atlas displays them verbatim or we add a short-code mapping — flag your preference. |
| `evidence_quotes` | ✅ **fulfillable now — currently discarded** | Produced as `evidence_spans: [{quote: str}]`; flatten to `text[]`. Guaranteed non-empty when `eligible=true`, guaranteed empty when false (enforced in the prompt). |
| `enriched_at` | ✅ | `now()` at insert. |

### The requested split

**Blocked purely by a missing scrape input:** **none.** Every AI field depends on `content`, and all 6 sources deliver full body text. This is the headline answer to the handoff's biggest stated risk.

**Blocked by a missing *pipeline* step (not a scrape gap):**
- `company_id` — needs company resolution + geocoding (§6)
- `raw_document_id` — needs the raw doc threaded through the gate

**Blocked because the source content doesn't contain it:**
- `amount_usd`, `fiscal_year` — genuinely absent from the majority of articles; no upstream fix exists
- Company **HQ address** — near-never present in a news article. This is why §6's proposed approach needs correcting.

**Not blocked at all, just discarded by current code:**
- `eligible`, `confidence`, `triggers`, `evidence_quotes`

### `industries` — deferred, and the decision it defers with it

**Persisting `enriched_documents.industries` is out of scope for v1.** Recorded in the §11 register.

This also parks a decision that would otherwise have blocked us. The pipeline is **label-keyed end to end** — `get_hubspot_industries()` returns `opt["label"]`, the AI enum is built from labels, and `build_industry_to_teams_map()` is keyed by label — so the column would naturally fill with `"Entertainment-Casinos and Gaming"`. But labels are editable in HubSpot, and a rename silently orphans every historical row, whereas internal values (`HRS000`) are stable. That's exactly what the versioned `hubspot_industry_labels` table exists to resolve.

Since nothing is being written, nothing can be written *wrong*, and the label-vs-value question can wait until the column is actually wired. When it is: our recommendation is **values**, reverse-mapped from labels via `INDUSTRY_VALUE_TO_LABEL` (which the pipeline already builds), with `hubspot_industry_labels` resolving display names. The one outcome to avoid is shipping ambiguously and mixing both formats in one array column.

**What still works without it:** team routing is unaffected. `bucket_articles_by_team()` consumes industries **in memory** during the run and writes only the resulting `document_team_buckets` rows. Routing, per-team emails, and the QA digest all behave exactly as they do today. What we lose is the ability to *see* or *re-derive* the tags after the fact — see §11 for why that one is worth a second look.

---

## 6. §6 — Location & map plan

### Agreement

Coordinates are right, company-HQ-as-waypoint is right, geocode-once-per-company is right, and two `double precision` columns are sufficient. **No PostGIS needed now.** Revisit only when a real spatial query appears — "signals within 50 mi of a rep's territory" is the trigger; marker placement is not.

### One substantive correction: the Geocoding API is the wrong API for our input

§6 step 1–2 assumes we extract an address and geocode it. **We will almost never have an address.** These are news articles; they name a company, not its HQ street address. The Google **Geocoding** API is an address→coordinates service and performs poorly on a bare business name.

**Recommend Google Places API — Text Search (New)** (`places:searchText`) as the primary resolver. Given `"Riverside Casino & Resort"` it returns everything the `companies` table wants, in one call:

| Places response field | `companies` column |
|---|---|
| `id` | `google_place_id` |
| `formattedAddress` | `formatted_address` |
| `location.latitude` / `.longitude` | `latitude` / `longitude` |
| `displayName.text` | `name` |
| `websiteUri` | `website` (currently unpopulated) |

Keep the Geocoding API as a **fallback** for the minority of cases where the article does state an address.

### Free upgrade: `extracted.location` already exists and is being discarded

The AI schema still produces `extracted.location` ([eligibility_schema.py:39](eligibility_schema.py#L39)) — the column was dropped Atlas-side in migration 0003, but the pipeline still generates it. **Repurpose it as the geocoder disambiguation hint** rather than deleting it. Passing `"Riverside Casino & Resort" + "Riverside, Iowa"` to Text Search is dramatically more accurate than the name alone, at zero additional AI cost. This alone should be the difference between usable and unusable map data for chain businesses.

### Known accuracy limits — please read before trusting the map

- **Text Search returns the best-matching *place*, not necessarily the HQ.** For a single-site venue (casino, airport, stadium) that's exactly right. For `"Walmart"` it returns *a* Walmart. The location hint mitigates this; it does not eliminate it.
- The handoff says "handle geocoding failures gracefully (leave coords null, flag for review)" — **there is nowhere to record that flag.** `companies` has no status column. See the migration list.
- **`NULL` google_place_id breaks the dedupe key.** Postgres treats NULLs as distinct in unique indexes, so every failed geocode creates a *new* `companies` row, and the same unresolvable company multiplies on every run. A fallback dedupe key is required, not optional. See the migration list.

### Single vs multi-location

**Single location per company (HQ / primary place), as specified.** Correct call for v1. Note the consequence explicitly so nobody is surprised: an article about a Walmart opening in Tulsa will pin at whichever Walmart place Google resolves, not Tulsa. If per-signal geography matters later, the fix is an article-level location on `enriched_documents` — but per §6 that is deliberately out of scope, and we agree it should stay out of scope for v1.

### Cost control

Skip resolution entirely when the company already has coordinates (as specified). Additionally, cache **negative** results — an unresolvable company name will otherwise be re-queried on every run forever. A `geocode_status` column covers both this and the review flag.

---

## 7. §7 — Persistence plan

### Where each write happens

New module `db.py` (or a `db/` package) exposing `upsert_raw_document`, `resolve_and_upsert_company`, `upsert_enriched_document`, `insert_team_buckets`, `start_pipeline_run`, `finish_pipeline_run`. Driver: `psycopg[binary]` v3.

Changes to [new-ai-lead-gen.py:354-379](new-ai-lead-gen.py#L354-L379):

1. **`start_pipeline_run()`** as the first statement — `status='running'`, `run_type`, `started_at`. Wrap the whole `__main__` in `try/except/finally` so a crash still records `status='failed'` + `error_message`.
2. **After each `get_*_documents()` call**, upsert that scraper's raw docs immediately and attach the returned `id` to each dict. Per-scraper rather than one batch at the end, so a crash in scraper #5 doesn't discard scrapers #1–4. Commit these independently — a raw document is a cheap, always-valid fact.
3. **New step — skip already-enriched URLs before calling OpenAI.** Query which of this run's URLs already have an `enriched_documents` row and drop them from the gate input. This is the largest immediate win from persisting: today every run re-pays OpenAI for every previously-seen article, because the only dedupe is a per-scraper in-memory `seen_urls` set that dies with the process. **The DB `UNIQUE (url)` constraint is the first real cross-run dedupe this pipeline has ever had.**
4. **Rewrite `test_run_eligibility_gate()`** to return `(raw_doc, full_gate_response)` pairs rather than bare `extracted`. Required for `raw_document_id`, and it's what stops `eligible`/`confidence`/`triggers`/`evidence_quotes` being discarded.
5. **Resolve + upsert the company** (§6), then insert/update `enriched_documents` with both FKs set.
6. **Persist ineligible rows too**, with `eligible=false`. They currently exist only as the QA email's `FILTERED_RESULTS` list. Storing them turns false-negative review into a dashboard query instead of an email archaeology exercise — and it's the only way to measure gate precision over time.
7. **`bucket_articles_by_team()`** currently buckets bare `extracted` dicts and has no enriched-row id to write. Thread `enriched_document_id` through it. `team_name` comes from `get_all_teams()`, which is currently called *inside* the email function — hoist it so bucketing can use it.
8. **Emails render from persisted rows** (`SELECT ... WHERE eligible = true AND enriched_at >= run_start`), so email and DB cannot drift.

### Upsert strategy

| Table | Key | Strategy |
|---|---|---|
| `raw_documents` | `url` | `ON CONFLICT (url) DO UPDATE` — refresh `title`, `content`, `updated_at`. Never overwrite `discovered_at`. |
| `companies` | `google_place_id` | `ON CONFLICT (google_place_id) DO UPDATE`. **Requires a unique constraint that may not exist — verify.** Needs a fallback key for NULL place_ids. |
| `enriched_documents` | `raw_document_id` | **Recommend adding `UNIQUE (raw_document_id)` and upserting on it.** |
| `document_team_buckets` | `(enriched_document_id, team_id)` | `ON CONFLICT DO NOTHING`. Already constrained — nothing needed. |

**On the `enriched_documents` uniqueness question (§7 asks us to flag a choice):** we want **`UNIQUE (raw_document_id)`**. The domain model is one signal per article — the gate emits exactly one decision per document, so a second row can only ever be a duplicate. A re-run should refresh the enrichment, not accumulate. We'd rather have the constraint than a pipeline-side guard, because the constraint also protects against concurrent runs, which a guard doesn't. If Atlas ever wants multiple signals per article, that's `UNIQUE (raw_document_id, company_id)` — but we see no current need and don't want to design for it speculatively.

### Transactions

- Raw document upsert: its own transaction, committed immediately.
- Company + enriched + team buckets for one document: **one transaction**, so a signal never lands without its routing.
- Run-level: no global transaction. A single bad document should not roll back a whole night's work.

### Email retention — confirmed

**Email is retained.** Both digests stay: the per-team `[Business Signals]` digest and the `[QA Review]` filtered digest. The only change is that both render from persisted rows rather than in-memory lists.

One thing to fix while we're in here: `new-ai-lead-gen.py` currently calls **`test_send_emails_to_teams()`**, which force-adds `perryk@`, `will.geller@`, and `federico.aguilar@` to *every* team's email ([new-ai-lead-gen.py:307-309](new-ai-lead-gen.py#L307-L309)). That's QA behavior sitting in the main path. It should become an env-gated flag (`QA_MODE=true`) rather than a separate function, so the production and QA paths can't diverge. The DB write must happen identically in both modes.

### Answering §9's team question definitively

`team_id` and `team_name` both come from **HubSpot**. There is no config list and no `teams` table is needed:

- **`team_id`** — from HubSpot custom object `2-54755382`, property `team`, which holds values shaped `team_id_58816923`. The pipeline strips the `team_id_` prefix ([new-ai-lead-gen.py:160](new-ai-lead-gen.py#L160)), so **write the bare numeric string** (`"58816923"`).
- **`team_name`** — from `GET /settings/v3/users/teams`, field `name`.
- The industry→team mapping itself lives in that same HubSpot custom object (`industry` → `team`), fetched fresh each run.

Two notes for Atlas: team names are user-editable in HubSpot, so `team_name` is a **denormalized snapshot** — join on `team_id`, display `team_name`. And the mapping fetch is capped at `limit=100` with no pagination ([new-ai-lead-gen.py:119](new-ai-lead-gen.py#L119)); fine now, silently lossy if the mapping object grows past 100.

### `uuid-ossp`

The pipeline will **omit `id` and rely on the column default**, so the extension must be present. Recommend Atlas switch the defaults to `gen_random_uuid()` (built into Postgres 13+ via pgcrypto, no extension required) to remove the dependency entirely. Low-priority but free. We'll add a startup assertion either way.

---

## 8. §9 — Open questions, resolved

### A. `source_domain`: why `casino` and not `casino.org` **[VERIFIED]**

`tldextract.extract(url).domain` returns the registrable name **without** the public suffix. Reproduced:

```
https://www.casino.org/news/            domain=casino               top_domain_under_public_suffix=casino.org
https://www.convenience.org/            domain=convenience          top_domain_under_public_suffix=convenience.org
https://airportindustry-news.com/news/  domain=airportindustry-news top_domain_under_public_suffix=airportindustry-news.com
```

So `casino` is not a bug in that row — it's this repo's convention, applied consistently across all 5 scraped sources. **But EventRegistry doesn't use `tldextract` at all**; it passes through the API's `source.uri`, which *is* a FQDN. The column therefore contains two formats today.

**Recommendation: standardize on the FQDN** (`casino.org`, `retaildive.com`) — it's unambiguous, matches EventRegistry's existing values, and is directly usable for favicon/link display in the dashboard. Change the 5 scrapers to `.top_domain_under_public_suffix` (note: `.registered_domain` has the same behavior but is deprecated in tldextract 5.x and warns). Requires a one-time backfill of existing rows; at 3 rows that's trivial today and won't be later.

### B. `language_confidence ≈ -7222`: what the unit is **[VERIFIED]**

It's `langid.classify()`'s **unnormalized log-probability**, and it **scales with document length**. Measured:

| Input | `langid.classify()` | Normalized |
|---|---|---|
| 45 chars | `('en', -22.65)` | — |
| 1,840 chars | `('en', -1259.38)` | `('en', 1.0)` |

`-7222` is therefore consistent with a ~10,000-character article, and carries **no information about detection confidence** — a long English article and a long Spanish article both score large negatives. Sorting or thresholding on this column is meaningless.

**Fix (pipeline-side, no migration):** switch to the normalized identifier, which returns a true 0–1 probability:

```python
from langid.langid import LanguageIdentifier, model
_identifier = LanguageIdentifier.from_modelstring(model, norm_probs=True)
language, confidence = _identifier.classify(content)   # ('en', 1.0)
```

Recommend Atlas **NULL out the existing rows'** `language_confidence` rather than leaving two incompatible scales in one column. And EventRegistry needs language detection added — it currently writes neither field.

### C. `doc_date` as `text` while other timestamps are `timestamptz`

**Recommend normalizing.** Today's value is a free-text AI extraction with no format guarantee, which means the dashboard can't sort or range-filter on it. Two options:

1. **Keep `text`, pipeline guarantees strict `YYYY-MM-DD`** — zero migration, sortable lexically, but nothing *enforces* the format.
2. **Migrate to `date`** — enforced, properly filterable. 3 existing rows makes this near-free right now.

**We'd take option 2.** Either way, populate it from `raw_documents.published_at` rather than the AI's string (see §5). Your call, since it's your column — but please decide before row count grows.

### D. Location model — agreed, with the API correction in §6.

### E. Team source — answered in §7.

### F. `UNIQUE (raw_document_id)` — yes, please add it. Reasoning in §7.

---

## 9. §8.6 — Consolidated migration list for the Atlas repo

Ordered by priority. Items 1–3 **block** the persistence work.

| # | Migration | Priority | Why |
|---|---|---|---|
| **1** | `ALTER TABLE enriched_documents ADD CONSTRAINT enriched_documents_raw_document_id_key UNIQUE (raw_document_id);` | **Blocking** | Idempotent enrichment. Without it, re-runs duplicate signals. Our stated choice per §7. |
| **2** | ~~Verify / add `UNIQUE (google_place_id)`~~ — **no migration needed, but the handoff's SQL must change** | **Blocking** | **[VERIFIED against live DB]** The index exists, but as a **partial** index: `companies_google_place_id_key ... WHERE (google_place_id IS NOT NULL)`. Arbiter inference therefore requires the predicate — `ON CONFLICT (google_place_id) DO UPDATE` **fails** with `InvalidColumnReference: there is no unique or exclusion constraint matching the ON CONFLICT specification`. It must be written `ON CONFLICT (google_place_id) WHERE google_place_id IS NOT NULL DO UPDATE`. Same applies to the equivalent partial index on `companies.domain`. |
| **3** | Fallback company dedupe key — add `name_normalized text` + `CREATE UNIQUE INDEX ... ON companies (name_normalized) WHERE google_place_id IS NULL AND domain IS NULL;` | **Blocking** | **[VERIFIED]** `companies.domain` also carries a partial unique index, so it's a usable second key — but with **both** `google_place_id` and `domain` NULL, duplicates are still possible: inserting the same `name` twice produced 2 rows. Every unresolvable company would multiply on every run. |
| **4** | `ALTER TABLE companies ADD COLUMN geocode_status text, ADD COLUMN geocode_attempted_at timestamptz;` | High | §6 requires "flag for review" on geocode failure, but there is no column to flag *into*. Also enables negative-result caching so we stop re-billing Google for unresolvable names. Suggested values: `resolved` / `not_found` / `ambiguous` / `error`. |
| **5** | `ALTER TABLE companies ADD COLUMN resolution_method text;` | Medium | Distinguishes Places-Text-Search from Geocoding-API-from-address results. Precision differs materially; the dashboard should be able to tell them apart. |
| **6** | `CREATE INDEX ON enriched_documents (eligible, enriched_at DESC);` and `CREATE INDEX ON enriched_documents (company_id);` | Medium | The dashboard's default query is "recent eligible signals"; the map joins on `company_id`. Cheap now, awkward under load. |
| **7** | Backfill: `UPDATE raw_documents SET source_domain = ... ` to FQDN form; `UPDATE raw_documents SET language_confidence = NULL;` | Medium | Resolves §9-A and §9-B on the 3 existing rows. Trivial at current row count. |
| **8** | `ALTER TABLE enriched_documents ALTER COLUMN doc_date TYPE date USING doc_date::date;` | Low — needs your decision | §9-C option 2. Only worth doing while the table is nearly empty. |
| **9** | Switch PK defaults from `uuid_generate_v4()` to `gen_random_uuid()` | Low | Drops the `uuid-ossp` extension dependency. Built into PG 13+. |

**No migration needed for:** `raw_documents.scraper_source` (column exists; the pipeline just has to supply it) and `document_team_buckets` (already correctly constrained).

---

## 10. Work items on our side (pipeline repo)

For visibility — this is what we'd sequence, roughly in dependency order.

| # | Item | Size |
|---|---|---|
| 1 | Add `scraper_source` to all 6 scrapers | XS |
| 2 | Normalize `source_domain` to FQDN across the 5 `tldextract` scrapers | XS |
| 3 | Switch `langid` to `norm_probs=True`; add language detection to EventRegistry | XS |
| 4 | Normalize `published_at` to tz-aware UTC `datetime` in one shared helper | S |
| 5 | Fix `discovered_at` to be per-document `now()`, not import-time `DATE_NOW` | XS |
| 6 | `db.py` — connection, the five upsert helpers, `pipeline_runs` lifecycle | M |
| 7 | Raw-document persistence wired into `__main__`, per-scraper | S |
| 8 | Skip-already-enriched pre-filter before the OpenAI call | S |
| 9 | Rewrite the gate to return `(raw_doc, full_response)` and stop discarding `eligible`/`confidence`/`triggers`/`evidence_spans` | S |
| 10 | Company resolution + Google Places Text Search + `companies` upsert | **L** |
| 11 | Thread `enriched_document_id` into team bucketing; persist `document_team_buckets` | S |
| 12 | Re-point both email templates at persisted rows | M |
| 13 | Replace `test_*` QA functions with an env-gated `QA_MODE` flag | S |
| 14 | Add `psycopg[binary]` + Places client to `requirements.txt` | XS |

Item 10 is the only large one, and it's the only genuinely new capability in this handoff.

### Two housekeeping bugs found along the way

Not blocking, but they become more consequential once dates are persisted rather than emailed and forgotten:

- **`requirements.txt` is UTF-16LE encoded** (visible BOM + null-padded bytes). It was almost certainly produced by piping `pipreqs` through PowerShell `>`. `pip install -r` will fail to parse it on a clean machine. Rewrite as UTF-8.
- **Two default arguments are evaluated at import time**, not call time: `date_utils.DATE_NOW` ([date_utils.py:6](date_utils.py#L6)) and `is_nahb_date(..., target_date=datetime.now(timezone.utc).date())` ([date_utils.py:38](date_utils.py#L38)). Both silently produce yesterday's date for any process that runs across midnight UTC. Harmless for a short cron job; a real correctness bug for a long-lived or retried run.

---

## 11. Deferred scope register

Everything knowingly left out of v1, so nothing is lost by accident. The **Reversibility** column is the one to read:

- **Free** — add later at the same cost as adding it now. No data lost meanwhile.
- **Backfillable** — recoverable later from data we *are* storing (`url`, `content`), but the backfill grows with row count.
- **Lossy** — the data is gone. Recovering it means re-running the AI or re-scraping.

### 11.0 What "core v1" is, for contrast

In scope: `raw_documents` upsert on `url` for all 6 sources (incl. `scraper_source` and normalized `published_at`) → skip-already-enriched pre-filter → `enriched_documents` insert with `raw_document_id`, `url`, `title`, `summary`, `company`, `amount_usd`, `fiscal_year`, `doc_date`, `article_link`, `eligible`, `confidence`, `triggers`, `evidence_quotes`, `enriched_at`, `company_id` → company resolve + geocode + `companies` upsert → `document_team_buckets` → `pipeline_runs` lifecycle → both emails rendered from persisted rows.

Out of scope is everything below.

### 11.A Deferred by decision

| # | Deferred | Why | What it costs us in v1 | Reversibility |
|---|---|---|---|---|
| A1 | **`enriched_documents.industries` persistence** | Scope call — focus on core write path | Dashboard industry filtering (a §2 consumer) has no data. Routing can't be audited or re-derived after the fact — you can see *that* a signal went to team X, not *why*. | **Lossy** — see §11.F |
| A2 | Label-vs-value decision for that column | Moot while A1 is deferred | Nothing — parking it is the point | Free |
| A3 | `hubspot_industry_labels` stays reference-only | Follows from A1 | Table is unused in v1 | Free |
| A4 | Article-level location on `enriched_documents` | Atlas-side call; column dropped in migration 0003 | Map pins company HQ, not article geography — a Tulsa store opening pins at whichever Walmart Google resolves | Free |
| A5 | PostGIS `geography(Point, 4326)` | Two doubles are enough to drop markers | No radius or bounding-box queries | Free — migrate when "signals within 50 mi of a territory" appears |

### 11.B Deferred data-quality items

| # | Deferred | Why | What it costs us in v1 | Reversibility |
|---|---|---|---|---|
| B1 | `source_domain` FQDN standardization (§9-A) | Cosmetic until the dashboard displays it | Column holds two formats — bare label (`chainstoreage`) and FQDN (`retaildive.com`) | **Backfillable** from `url`; cost grows with row count |
| B2 | `language_confidence` normalization (§9-B) | Nothing consumes it yet | Values are length-scaled log-probs. Unsortable, unthresholdable, actively misleading if anyone tries | **Backfillable** by recomputing from `content` |
| B3 | `language` / `language_confidence` on EventRegistry | Nothing consumes it yet | Both NULL for our highest-volume source | **Backfillable** from `content` |
| B4 | `doc_date` text → `date` migration (§9-C) | Needs an Atlas decision | Not range-filterable or sortable in the dashboard | **Backfillable** — cheapest right now at 3 rows |
| B5 | `published_at` quality on nacs / nahb / prnewswire | Source-limited, not fixable by us | nacs always midnight; nahb date-only; prnewswire assumes today's date | **Not fixable** — document per-source reliability instead (§2 table) |
| B6 | `discovered_at` import-time `DATE_NOW` | Cosmetic | Every doc in a run shares one process-start timestamp instead of true discovery time | Free — historical rows stay slightly off |
| B7 | EventRegistry `content` truncation audit | Impossible before rows exist | We don't yet know whether API bodies are full or plan-truncated | Free — **becomes possible after the first persisted run**; check then |

### 11.C Deferred pipeline hygiene and known bugs

| # | Deferred | Why | What it costs us in v1 | Reversibility |
|---|---|---|---|---|
| C1 | `GENERAL` tag is unreachable (prompt instructs it; enum can't express it) | Industries-adjacent | No-match articles fall to `industries: []` instead of a `GENERAL` tag | Free — defer with A1 |
| C2 | Empty-industries silent drop | Industries-adjacent | An `eligible=true` signal with `industries: []` routes to zero teams, is never emailed, and leaves no trace | **Gets worse under A1** — the `cardinality(industries) = 0` diagnostic needs the column we're not writing |
| C3 | `requirements.txt` is UTF-16LE | Doesn't block us locally | `pip install -r` fails on a clean machine or in CI | Free |
| C4 | Import-time default args (`DATE_NOW`, `is_nahb_date` target) | Short cron job masks it | Wrong date if a run crosses midnight UTC or is retried | Free |
| C5 | `test_*` QA functions → env-gated `QA_MODE` | Current behavior, not a regression | Every team email CCs 3 internal addresses ([new-ai-lead-gen.py:307-309](new-ai-lead-gen.py#L307-L309)) | Free |
| C6 | HubSpot industry→team map `limit=100`, no pagination | Under the limit today | Silently lossy past 100 mappings ([new-ai-lead-gen.py:119](new-ai-lead-gen.py#L119)) | Free |
| C7 | prnewswire silently drops empty-content docs | Current behavior | Invisible losses — better to store the raw row with `content=NULL` | Free |
| C8 | prnewswire 3-page cap + comma-based date heuristic | Works today | May miss items on heavy news days | Free |
| C9 | chainstoreage camelCase-splitting regex | Cosmetic | Occasionally splits legitimate intra-word capitals ("iPhone") | Free |
| C10 | `companies.ticker`, `employee_count` | No data source exists | Columns stay NULL | Free — needs a firmographics provider |
| C11 | `companies.domain` | Derivable but unparsed | Stays NULL unless we parse Places `websiteUri` | Free — near-trivial once `website` lands |
| C12 | `triggers` are full sentences, not terse tags | Display decision | Atlas renders `"New funding approved"` rather than `"funding"` | Free |

### 11.D Deferred sources

| # | Deferred | Why | What it costs us in v1 | Reversibility |
|---|---|---|---|---|
| D1 | **EIN Presswire** | Never ported from legacy [ai-lead-gen.py:791-863](ai-lead-gen.py#L791-L863) | A previously-working source stays dark | Free — ~40 lines. **Port before deleting the legacy file** |
| D2 | AAAE | [scrapers/aaae.py](scrapers/aaae.py) is a 0-byte placeholder | Planned source absent | Free |
| D3 | 9 commented EventRegistry publishers | Config-only change | Narrower coverage (banking, healthcare, medtech, …) | Free — uncomment [payloads.py:27-61](scrapers/eventregistry/payloads.py#L27-L61) |
| D4 | `casinoorgnews` provenance | Not in this repo at any commit | 3 orphan rows; an unknown source may drift from our contract | **Needs an answer from the Atlas side** |

### 11.E Deferred Atlas-side migrations

Non-blocking items from §9's list. Numbering matches that table.

| # | Deferred | Priority | What it costs us in v1 | Reversibility |
|---|---|---|---|---|
| #5 | `companies.resolution_method` | Medium | Can't distinguish Places-Text-Search from Geocoding precision on the map | Free |
| #6 | Dashboard indexes on `enriched_documents` | Medium | Fine at low volume; awkward under load | Free |
| #7 | Backfill of the 3 existing rows | Medium | Two `source_domain` formats and one log-prob coexist with new data | Backfillable |
| #8 | `doc_date` type change | Needs decision | See B4 | Backfillable |
| #9 | `gen_random_uuid()` default swap | Low | `uuid-ossp` dependency remains | Free |

### 11.F The one lossy deferral — and a cheap way to make it free

A1 is different in kind from everything else here. B1–B3 are recoverable from `url` and `content`, which we *are* storing. A1 is not: the industry tags exist only inside an OpenAI response that we discard at the end of the run. **Every run that goes by without storing them is a run you cannot backfill without re-paying for enrichment.** At a daily cadence that gap compounds quietly.

If you want the deferral without the data loss, one nullable column removes the problem:

```sql
ALTER TABLE enriched_documents ADD COLUMN gate_response jsonb;
```

Store the gate response verbatim. It costs one migration, no contract decisions, no Atlas query changes, and no work on our side beyond passing through a dict we already have in hand. It preserves `industries` — and anything else we later decide we want — as a backfill instead of a re-run. It also restores the C2 diagnostic (`gate_response->'extracted'->'industries'`), which A1 otherwise blinds us to.

**Our recommendation:** take it. It is the difference between "deferred" and "discarded," and it's the last cheap moment to decide — the value of the column is proportional to how many runs happen before it exists. Entirely your call, and v1 proceeds either way.
