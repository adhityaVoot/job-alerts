# job-alerts

Pings my phone within ~30 min when a **new grad or internship** role in
**AI/ML or SWE** drops at one of my target companies.

Free to run: GitHub Actions cron + [ntfy.sh](https://ntfy.sh). No server, no
subscriptions, no API keys.

## How it works

```
every 30 min (GitHub Actions cron)
      │
      ├─ ATS feeds ─────► Greenhouse / Lever / Ashby JSON, one per company
      │                   (the same data the company careers page renders,
      │                    updated the instant the company publishes)
      │
      ├─ SimplifyJobs ──► community new-grad + internship listings.json
      │                   (broad safety net, filtered to my target companies)
      │
      ├─ filter ────────► title must match a LEVEL keyword (intern, new grad,
      │                   entry level, …) AND a ROLE keyword (software
      │                   engineer, machine learning, research engineer, …)
      │                   using word boundaries, so "Internal Platform" is
      │                   NOT treated as an internship
      │
      ├─ diff ──────────► against seen.json (committed back each run), so
      │                   each posting only ever notifies once
      │
      └─ notify ────────► POST to ntfy.sh/<topic> → phone push, tap to open
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

`ats` is one of `greenhouse`, `lever`, `ashby`, or `none`. Use `none` for a
company with no public ATS feed — the name still gets matched against the
SimplifyJobs listings.

**Finding a company's slug:** open its careers page and look at the job link
URLs, or probe the endpoints directly:

- Greenhouse — `https://boards-api.greenhouse.io/v1/boards/<slug>/jobs`
- Lever — `https://api.lever.co/v0/postings/<slug>?mode=json`
- Ashby — `https://api.ashbyhq.com/posting-api/job-board/<slug>`

Then run `python3 poll.py --verify` to confirm it returns jobs.

## Extending to big tech

Amazon, Google, Apple, Microsoft, Meta and NVIDIA don't use a standard ATS;
each has its own JSON careers API. To add one, write a
`adapter_<company>(co)` function that returns the normalized posting dict
(`id`, `company`, `title`, `url`, `location`) and register it in `ADAPTERS`.
Nothing else in the pipeline changes.

## Tuning the filter

Edit `filters` in `sources.json`.

- Too noisy → tighten `role_keywords`, or drop broad ones like `2026`.
- Missing roles → add title variants companies actually use.

Known tradeoff: matching is **title-only**. A role titled plain "Software
Engineer" that is secretly new-grad-friendly won't match. That's deliberate;
matching on descriptions roughly triples false positives.
