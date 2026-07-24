# job-alerts

Pings my phone within ~30 min when a **new grad or internship** role in
**AI/ML or SWE** drops at one of my target companies.

Free to run: GitHub Actions cron + [ntfy.sh](https://ntfy.sh). No server, no
subscriptions, no API keys.

## How it works

```
every 30 min (GitHub Actions cron)
      │
      ├─ ATS feeds ─────► Greenhouse / Lever / Ashby / SmartRecruiters /
      │                   Rippling JSON, one per company, fetched in parallel
      │                   (the same data the company careers page renders,
      │                    updated the instant the company publishes)
      │
      ├─ big-co APIs ───► Amazon search.json, Workday CXS (NVIDIA, Salesforce,
      │                   Adobe, Capital One, eBay, …), Eightfold (Netflix)
      │
      ├─ aggregators ───► SimplifyJobs + vanshb03 new-grad / internship
      │                   listings.json (broad safety net, filtered to my
      │                   target companies through their name aliases)
      │
      ├─ filter ────────► title must match a LEVEL keyword (intern, new grad,
      │                   entry level, …) AND a ROLE keyword (software
      │                   engineer, machine learning, research engineer, …)
      │                   using word boundaries, so "Internal Platform" is
      │                   NOT treated as an internship
      │
      ├─ collapse ──────► one row per (company, title): a role listed on the
      │                   company's own board AND picked up by an aggregator
      │                   is one job, not two pings
      │
      ├─ diff ──────────► against seen.json (committed back each run), so
      │                   each posting only ever notifies once
      │
      └─ notify ────────► POST to ntfy.sh/<topic> → phone push, tap to open
                          (past `max_pings` in one run, the rest collapse
                           into a single digest ping)
```

## Setup

1. **Install ntfy** on your phone (iOS App Store / Google Play / F-Droid).
2. **Subscribe to a private topic.** Pick something unguessable, e.g.
   `rish-jobs-8f3ka92mx`. Anyone who knows the topic can read it, so treat it
   like a password.
3. **Push this repo to GitHub** (private is fine, Actions works either way).
4. **Add the topic as a secret:** repo → Settings → Secrets and variables →
   Actions → New repository secret → name `NTFY_TOPIC`, value your topic name.
5. **Enable Actions** on the repo, then run the workflow once manually
   (Actions tab → job-alerts → Run workflow).

The first run **seeds** `seen.json` with everything currently open and sends a
single "Job alerts armed" ping instead of a flood. From then on you only hear
about genuinely new postings.

## Local use

```bash
python3 poll.py --verify   # probe every feed, print counts, notify nothing
NTFY_TOPIC=your-topic python3 poll.py   # real run
```

`--verify` is the one to run after editing `sources.json` — it shows which
companies resolve and which 404, without touching state or sending pushes.

## Adding companies

Add a row to `companies` in `sources.json`:

```json
{"name": "Cartesia", "ats": "ashby", "slug": "cartesia"}
```

`ats` is one of `greenhouse`, `lever`, `ashby`, `smartrecruiters`, `rippling`,
`workday`, `eightfold`, `amazon`, or `none`. Use `none` for a company with no
public ATS feed — the name still gets matched against the aggregator listings,
and `aliases` lets an aggregator's "Google" resolve to your "Google DeepMind".

The board-slug ones all take the same shape; the two parameterized ones need a
few more fields:

```json
{"name": "Salesforce", "ats": "workday", "slug": "", "tenant": "salesforce",
 "site": "External_Career_Site", "dc": "wd12", "queries": ["intern"], "max": 80}
{"name": "Netflix", "ats": "eightfold", "slug": "",
 "host": "explore.jobs.netflix.net", "domain": "netflix.com",
 "queries": ["intern", "new grad"], "max": 100}
```

`queries`/`max` exist because those boards are enormous — you search them
rather than pulling every posting.

**Finding a company's slug:** run `python3 discover.py "Company Name"`, which
probes every board-slug ATS in parallel and prints a paste-ready row. Or probe
by hand:

- Greenhouse — `https://boards-api.greenhouse.io/v1/boards/<slug>/jobs`
- Lever — `https://api.lever.co/v0/postings/<slug>?mode=json`
- Ashby — `https://api.ashbyhq.com/posting-api/job-board/<slug>`
- SmartRecruiters — `https://api.smartrecruiters.com/v1/companies/<slug>/postings`
- Rippling — `https://api.rippling.com/platform/api/ats/v1/board/<slug>/jobs`

Then run `python3 poll.py --verify` to confirm it returns jobs.

⚠️ Slugs collide across companies — `ashby/cedar` is a mortgage startup, not
Cedar Health; `lever/neon` is a Brazilian bank, not Neon Postgres. Always eyeball
a couple of returned titles before committing a discovered slug.

## Extending to more big tech

Google, Apple, Microsoft and Meta each run a bespoke careers API (and Microsoft's
blocks non-browser clients), so they ride on the aggregator feeds instead of a
direct one. To add a direct feed, write an `adapter_<company>(co)` returning the
normalized posting dict (`id`, `company`, `title`, `url`, `location`) and
register it in `ADAPTERS`. Nothing else in the pipeline changes.

Many Fortune 500s are on Workday or Eightfold, which already have generic
adapters — you only need the tenant/site/dc (Workday) or host/domain
(Eightfold) from the careers-page URL, no new code.

## Tuning the filter

Edit `filters` in `sources.json`.

- Too noisy → tighten `role_keywords`, or drop broad ones like `2026`,
  `developer`, `junior`. Dropping a whole `"ats": "none"` company also helps:
  those match by name against every aggregator feed, so a big employer like
  TikTok contributes a lot of rows on its own.
- Missing roles → add title variants companies actually use.
- Too many pings at once → lower `max_pings` (default 10); everything past it
  arrives as one digest instead.
- Run slow → lower `max_workers` (default 12) if a host starts rate-limiting;
  raise it if you add another hundred companies.

Known tradeoff: matching is **title-only**. A role titled plain "Software
Engineer" that is secretly new-grad-friendly won't match. That's deliberate;
matching on descriptions roughly triples false positives.
