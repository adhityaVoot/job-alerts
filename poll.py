#!/usr/bin/env python3
"""
Job-alerts poller.

Runs on a GitHub Actions cron. For every company in sources.json it fetches the
company's ATS job feed (the same data its careers page renders), plus optionally
the SimplifyJobs new-grad / internship listings, filters to new-grad + internship
roles in AI/ML or SWE, diffs against what it saw last run (seen.json), and pushes
only genuinely new postings to an ntfy.sh topic (your phone).

Stdlib only, so GitHub Actions needs no pip install.

Usage:
  python poll.py            # normal run: fetch, diff, notify, update seen.json
  python poll.py --verify   # probe every source, print how many roles each
                            #   resolves, DO NOT notify or write state
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(HERE, "sources.json")
SEEN_PATH = os.path.join(HERE, "seen.json")

UA = "Mozilla/5.0 (job-alerts bot; https://github.com/)"
TIMEOUT = 25


def http_json(url, method="GET", body=None, headers=None):
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# --------------------------------------------------------------------------
# Adapters: each returns a list of normalized postings
#   {"id": str, "company": str, "title": str, "url": str, "location": str}
# --------------------------------------------------------------------------

def adapter_greenhouse(co):
    slug = co["slug"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false"
    data = http_json(url)
    out = []
    for j in data.get("jobs", []):
        out.append({
            "id": f"greenhouse:{slug}:{j['id']}",
            "company": co["name"],
            "title": j.get("title", ""),
            "url": j.get("absolute_url", ""),
            "location": (j.get("location") or {}).get("name", ""),
        })
    return out


def adapter_lever(co):
    slug = co["slug"]
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = http_json(url)
    out = []
    for j in data:
        out.append({
            "id": f"lever:{slug}:{j.get('id')}",
            "company": co["name"],
            "title": j.get("text", ""),
            "url": j.get("hostedUrl", ""),
            "location": (j.get("categories") or {}).get("location", ""),
        })
    return out


def adapter_ashby(co):
    slug = co["slug"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"
    data = http_json(url)
    out = []
    for j in data.get("jobs", []):
        out.append({
            "id": f"ashby:{slug}:{j.get('id')}",
            "company": co["name"],
            "title": j.get("title", ""),
            "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
            "location": j.get("location", ""),
        })
    return out


def adapter_amazon(co):
    """amazon.jobs search.json. Amazon posts thousands of roles, so we query
    narrow terms rather than pulling the whole board."""
    out = []
    for q in co.get("queries", ["software development engineer intern"]):
        url = ("https://www.amazon.jobs/en/search.json?result_limit=100"
               f"&base_query={urllib.parse.quote(q)}")
        try:
            data = http_json(url)
        except Exception:
            continue
        for j in data.get("jobs", []):
            out.append({
                "id": f"amazon:{j.get('id_icims') or j.get('id')}",
                "company": co["name"],
                "title": j.get("title", ""),
                "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
                "location": j.get("location") or j.get("normalized_location", ""),
            })
    return out


def adapter_workday(co):
    """Generic Workday CXS endpoint (NVIDIA, Adobe, most big non-tech too).
    Workday paginates 20 at a time and needs a POST."""
    tenant, site, dc = co["tenant"], co["site"], co.get("dc", "wd5")
    url = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    out = []
    for q in co.get("queries", ["intern"]):
        offset = 0
        while offset < co.get("max", 100):
            try:
                data = http_json(url, method="POST", body={
                    "appliedFacets": {}, "limit": 20,
                    "offset": offset, "searchText": q,
                })
            except Exception:
                break
            posts = data.get("jobPostings", [])
            if not posts:
                break
            for j in posts:
                path = j.get("externalPath", "")
                out.append({
                    "id": f"workday:{tenant}:{path}",
                    "company": co["name"],
                    "title": j.get("title", ""),
                    "url": f"https://{tenant}.{dc}.myworkdayjobs.com/en-US/{site}{path}",
                    "location": j.get("locationsText", ""),
                })
            offset += 20
            time.sleep(0.2)
    return out


ADAPTERS = {
    "greenhouse": adapter_greenhouse,
    "lever": adapter_lever,
    "ashby": adapter_ashby,
    "amazon": adapter_amazon,
    "workday": adapter_workday,
}


def fetch_simplify(cfg, target_names):
    """Pull SimplifyJobs listings.json feeds, keep only target companies."""
    out = []
    names_lc = {n.lower() for n in target_names}
    for url in cfg.get("listings", []):
        try:
            data = http_json(url)
        except Exception as e:
            print(f"  simplify feed failed: {url} -> {e}", file=sys.stderr)
            continue
        for j in data:
            if not (j.get("active", True) and j.get("is_visible", True)):
                continue
            company = j.get("company_name", "")
            if cfg.get("match_target_companies_only", True):
                if company.lower() not in names_lc:
                    continue
            out.append({
                "id": f"simplify:{j.get('id') or j.get('url')}",
                "company": company,
                "title": j.get("title", ""),
                "url": j.get("url", ""),
                "location": ", ".join(j.get("locations", []) or []),
            })
    return out


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------

def compile_kw(keywords):
    """Word-boundary regex so 'intern' does not match 'Internal Platform'."""
    parts = [re.escape(k.lower()) for k in keywords]
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])")


def matches(title, level_re, role_re):
    t = title.lower()
    return bool(level_re.search(t)) and bool(role_re.search(t))


# --------------------------------------------------------------------------
# ntfy
# --------------------------------------------------------------------------

def ntfy(topic, title, message, url):
    endpoint = f"https://ntfy.sh/{topic}"
    headers = {
        "Title": title.encode("ascii", "ignore").decode(),
        "Priority": "high",
        "Tags": "briefcase",
    }
    if url:
        headers["Click"] = url
    req = urllib.request.Request(
        endpoint,
        data=message.encode("utf-8"),
        headers={**headers, "User-Agent": UA},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
    except Exception as e:
        print(f"  ntfy send failed: {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_seen():
    if not os.path.exists(SEEN_PATH):
        return None
    try:
        with open(SEEN_PATH) as f:
            return set(json.load(f).get("seen", []))
    except Exception:
        return None


def save_seen(seen):
    with open(SEEN_PATH, "w") as f:
        json.dump({"seen": sorted(seen)}, f, indent=0)


def gather(cfg, verify=False):
    """Fetch every source, return (all_matching_postings, per_source_report)."""
    level_re = compile_kw(cfg["filters"]["level_keywords"])
    role_re = compile_kw(cfg["filters"]["role_keywords"])
    target_names = [c["name"] for c in cfg["companies"]]
    postings = []
    report = []

    for co in cfg["companies"]:
        if co["ats"] in ("none", "simplify"):
            # No ATS feed; the name still feeds the Simplify matcher below.
            report.append((co["name"], co["ats"], "simplify-only", 0, 0))
            continue
        adapter = ADAPTERS.get(co["ats"])
        if not adapter:
            report.append((co["name"], co["ats"], "NO ADAPTER", 0, 0))
            continue
        try:
            raw = adapter(co)
        except urllib.error.HTTPError as e:
            report.append((co["name"], co["ats"], f"HTTP {e.code}", 0, 0))
            continue
        except Exception as e:
            report.append((co["name"], co["ats"], f"ERR {e}", 0, 0))
            continue
        hits = [p for p in raw if matches(p["title"], level_re, role_re)]
        postings.extend(hits)
        report.append((co["name"], co["ats"], "ok", len(raw), len(hits)))
        time.sleep(0.2)

    if cfg.get("simplify", {}).get("enabled"):
        sraw = fetch_simplify(cfg["simplify"], target_names)
        shits = [p for p in sraw if matches(p["title"], level_re, role_re)]
        postings.extend(shits)
        report.append(("SimplifyJobs", "simplify", "ok", len(sraw), len(shits)))

    # de-dup by id
    seen_ids = {}
    for p in postings:
        seen_ids[p["id"]] = p
    return list(seen_ids.values()), report


ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "amazon": "https://www.amazon.jobs/en/search.json",
    "workday": "https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
    "none": "(no public feed - covered by SimplifyJobs)",
}


def endpoint_for(co):
    tpl = ENDPOINTS.get(co["ats"], "?")
    return tpl.format(slug=co.get("slug", ""), tenant=co.get("tenant", ""),
                      dc=co.get("dc", ""), site=co.get("site", ""))


def write_report(cfg, postings, report):
    """Regenerate TRACKING.md: what is watched, where, and what matches now."""
    from collections import defaultdict
    stat = {r[0]: r for r in report}
    by_co = defaultdict(list)
    for p in postings:
        by_co[p["company"]].append(p)

    live = [c for c in cfg["companies"] if c["ats"] != "none"]
    simp = [c for c in cfg["companies"] if c["ats"] == "none"]

    L = []
    L.append("# What this repo is tracking\n")
    L.append("Auto-generated by `python3 poll.py --report`. Do not hand-edit.\n")
    L.append(f"- **{len(cfg['companies'])} companies** watched\n")
    L.append(f"- **{len(live)}** with a direct company feed, "
             f"**{len(simp)}** covered via SimplifyJobs only\n")
    L.append(f"- **{len(postings)} matching roles open right now**\n")

    L.append("\n## Filter\n")
    L.append("A title must match a **level** keyword AND a **role** keyword.\n\n")
    L.append("**Level:** " + ", ".join(f"`{k}`" for k in cfg["filters"]["level_keywords"]) + "\n\n")
    L.append("**Role:** " + ", ".join(f"`{k}`" for k in cfg["filters"]["role_keywords"]) + "\n")

    L.append("\n## Companies with a direct feed\n")
    L.append("| Company | Source | Endpoint scraped | Open roles | Matches |\n")
    L.append("|---|---|---|---:|---:|\n")
    for c in live:
        r = stat.get(c["name"], (None, None, "?", 0, 0))
        L.append(f"| {c['name']} | {c['ats']} | `{endpoint_for(c)}` | {r[3]} | {r[4]} |\n")

    L.append("\n## Covered by SimplifyJobs only\n")
    L.append("No public ATS feed found. Their listings still get caught via the "
             "SimplifyJobs feeds, filtered to these names.\n\n")
    L.append(", ".join(c["name"] for c in simp) + "\n")

    L.append("\n## Aggregator feeds\n")
    for u in cfg["simplify"]["listings"]:
        L.append(f"- `{u}`\n")

    L.append(f"\n## Currently matching roles ({len(postings)})\n")
    for co in sorted(by_co):
        L.append(f"\n### {co} ({len(by_co[co])})\n")
        for p in sorted(by_co[co], key=lambda x: x["title"]):
            loc = f" — {p['location']}" if p["location"] else ""
            L.append(f"- [{p['title']}]({p['url']}){loc}\n")

    with open(os.path.join(HERE, "TRACKING.md"), "w") as f:
        f.write("".join(L))
    print(f"Wrote TRACKING.md: {len(cfg['companies'])} companies, {len(postings)} matches.")


def main():
    verify = "--verify" in sys.argv
    do_report = "--report" in sys.argv
    with open(SOURCES_PATH) as f:
        cfg = json.load(f)

    postings, report = gather(cfg, verify=verify)

    if do_report:
        write_report(cfg, postings, report)
        return

    if verify:
        print(f"\n{'COMPANY':<20} {'ATS':<12} {'STATUS':<12} {'TOTAL':>6} {'MATCHED':>8}")
        print("-" * 62)
        for name, ats, status, total, matched in report:
            print(f"{name:<20} {ats:<12} {status:<12} {total:>6} {matched:>8}")
        print(f"\nTotal matching new-grad/intern AI-ML/SWE roles: {len(postings)}")
        return

    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC not set; refusing to run.", file=sys.stderr)
        sys.exit(1)

    seen = load_seen()
    current_ids = {p["id"] for p in postings}

    if seen is None:
        # First run: seed silently, one summary ping instead of a flood.
        save_seen(current_ids)
        ntfy(topic, "Job alerts armed",
             f"Monitoring started. Tracking {len(postings)} open "
             f"new-grad/intern AI-ML/SWE roles across your targets. "
             f"You'll get a ping when new ones drop.", "")
        print(f"Seeded {len(current_ids)} postings, no per-role notifications.")
        return

    new = [p for p in postings if p["id"] not in seen]
    for p in new:
        title = f"{p['company']}: {p['title']}"
        loc = f"\n{p['location']}" if p["location"] else ""
        ntfy(topic, title, f"New posting{loc}\n{p['url']}", p["url"])
        print(f"NOTIFY {title}")

    seen |= current_ids
    save_seen(seen)
    print(f"Run complete. {len(new)} new, {len(postings)} tracked.")


if __name__ == "__main__":
    main()
