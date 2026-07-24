#!/usr/bin/env python3
"""
One-off helper: given company names, probe every ATS we have an adapter for and
find a working job-board slug. Prints rows ready to paste into sources.json.

Not used by the poller. Kept in the repo because it's how sources.json gets
extended, and re-running it is how you fix a company that changed ATS.

  python3 discover.py                   # probe the built-in list
  python3 discover.py "Stripe" "Figma"  # probe just these
  python3 discover.py --json out.json   # also dump machine-readable results
"""
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# Each entry: url template + a function pulling the job list out of the payload.
EP = {
    "greenhouse": (
        "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
        lambda d: d.get("jobs", []),
    ),
    "lever": (
        "https://api.lever.co/v0/postings/{s}?mode=json",
        lambda d: d if isinstance(d, list) else [],
    ),
    "ashby": (
        "https://api.ashbyhq.com/posting-api/job-board/{s}?includeCompensation=false",
        lambda d: d.get("jobs", []),
    ),
    "smartrecruiters": (
        "https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=100",
        lambda d: d.get("content", []),
    ),
    "workable": (
        "https://apply.workable.com/api/v1/widget/accounts/{s}?details=false",
        lambda d: d.get("jobs", []),
    ),
}

COMPANIES = [
    # ---- voice / speech / audio (the original core) --------------------
    "Resemble AI", "Rime AI", "LMNT", "Neuphonic", "Camb.ai", "Papla Platforms",
    "WellSaid Labs", "Murf AI", "LOVO", "Speechify", "Respeecher", "Gladia",
    "Speechmatics", "Picovoice", "Krisp", "Soniox", "aiOla", "ElevenLabs",
    "Hume AI", "Cartesia", "Sesame AI", "Deepgram", "AssemblyAI",
    "Retell AI", "Bland AI", "Vocode", "Daily", "Sindarin", "PolyAI", "Cresta",
    "Parloa", "Synthflow", "Phonely", "Hamming AI", "Coval", "Regal AI",
    "Replicant", "Cognigy", "Sierra", "Decagon", "Thoughtly", "Slang.ai",
    "Goodcall", "Vapi", "LiveKit",
    # ---- music ---------------------------------------------------------
    "Suno", "Udio", "Spotify", "Splice", "Output", "LANDR", "Moises",
    "Music.AI", "Rightsify", "Musixmatch", "Cyanite", "Musical AI", "Kits AI",
    "Voice-Swap", "Sony AI", "Universal Music Group", "Endel", "Beatoven.ai",
    "SoundCloud", "Bandcamp", "Native Instruments", "iZotope", "Serato",
    "Beatport", "Epidemic Sound", "Audible",
    # ---- frontier AI labs ----------------------------------------------
    "Anthropic", "OpenAI", "xAI", "Cohere", "Character AI", "Scale AI",
    "Mistral AI", "Reflection AI", "Luma AI", "Midjourney", "World Labs",
    "Physical Intelligence", "Skild AI", "Figure AI", "Runway", "Pika",
    "Black Forest Labs", "Contextual AI", "Imbue", "Magic", "Harvey",
    "Glean", "Adept", "Liquid AI", "Thinking Machines Lab", "Perplexity",
    # ---- AI infra / dev tools -------------------------------------------
    "Together AI", "Fireworks AI", "Baseten", "Modal", "LangChain",
    "LlamaIndex", "Pinecone", "Weaviate", "Braintrust", "Replicate", "Qdrant",
    "Chroma", "Portkey", "Helicone", "Hugging Face", "Weights & Biases",
    "Anyscale", "Groq", "Cerebras", "SambaNova", "Lambda Labs", "CoreWeave",
    "Databricks", "Snowflake", "Sourcegraph", "Anysphere", "Replit", "Vercel",
    "Netlify", "Supabase", "Neon", "PlanetScale", "Render", "Railway",
    "Docker", "HashiCorp", "Grafana Labs", "Datadog", "Sentry", "LaunchDarkly",
    "Temporal", "Airbyte", "dbt Labs", "Astronomer", "Confluent", "Redpanda",
    "MongoDB", "Elastic", "Cockroach Labs", "ClickHouse", "Timescale", "Redis",
    "Postman", "Retool", "Linear", "Notion", "Figma", "Airtable", "Asana",
    "Atlassian", "Canva", "Dropbox", "Box", "Zoom", "Twilio", "Okta",
    "Cloudflare", "Fastly", "Akamai", "Amplitude", "Mixpanel", "Statsig",
    # ---- consumer / marketplace / big-name product ----------------------
    "Uber", "Lyft", "Airbnb", "DoorDash", "Instacart", "Pinterest", "Snap",
    "Reddit", "Discord", "Roblox", "Twitch", "Duolingo", "Grammarly",
    "Coursera", "Chegg", "Khan Academy", "Epic Games", "Unity", "Riot Games",
    "Niantic", "Scopely", "Etsy", "eBay", "Shopify", "Wayfair", "Chewy",
    "Peloton", "Strava", "Whoop", "Oura", "Life360", "Nextdoor", "Yelp",
    "Zillow", "Redfin", "Compass", "Opendoor", "Carvana", "Turo", "Rover",
    "Expedia", "Booking.com", "Tripadvisor", "OpenTable", "Toast",
    # ---- fintech / payments --------------------------------------------
    "Stripe", "Block", "PayPal", "Coinbase", "Robinhood", "Plaid", "Ramp",
    "Brex", "Affirm", "Chime", "Gusto", "Rippling", "Deel", "Carta",
    "Addepar", "Betterment", "Wealthfront", "SoFi", "Marqeta", "Checkr",
    "Persona", "Alloy", "Mercury", "Modern Treasury", "Column",
    "Anchorage Digital", "Circle", "Kraken", "Gemini", "Fireblocks",
    # ---- quant trading / finance ----------------------------------------
    "Jane Street", "Citadel", "Citadel Securities", "Two Sigma",
    "Hudson River Trading", "Jump Trading", "IMC Trading", "Optiver", "DRW",
    "D. E. Shaw", "Point72", "Bloomberg", "Akuna Capital",
    "Susquehanna International Group", "Old Mission Capital",
    "Squarepoint Capital", "Millennium", "Balyasny Asset Management",
    "Virtu Financial", "Tower Research Capital", "Five Rings", "Radix Trading",
    "Belvedere Trading", "Peak6", "XTX Markets", "Man Group",
    "AQR Capital Management", "Vatic Labs", "Headlands Technologies",
    # ---- security -------------------------------------------------------
    "CrowdStrike", "SentinelOne", "Wiz", "Zscaler", "Snyk", "1Password",
    "Vanta", "Drata", "Chainguard", "Tailscale", "Abnormal Security",
    "Material Security", "Socket", "Semgrep", "Rubrik", "Cohesity",
    # ---- enterprise / robotics / aero -----------------------------------
    "ServiceNow", "Workday", "Intuit", "Splunk", "Nutanix", "Pure Storage",
    "Samsara", "Verkada", "Flexport", "Palantir", "Anduril", "SpaceX",
    "Tesla", "Rivian", "Lucid Motors", "Waymo", "Zoox", "Nuro",
    "Applied Intuition", "Boston Dynamics", "Skydio", "Shield AI",
    "Neuralink", "Astranis", "Relativity Space", "Rocket Lab", "Varda Space",
    "Firefly Aerospace", "Vannevar Labs", "Saronic", "Castelion",
    # ---- health / bio ----------------------------------------------------
    "Abridge", "Ambience Healthcare", "Suki AI", "Ellipsis Health",
    "Hippocratic AI", "Kintsugi", "Sonde Health", "Tempus", "Recursion",
    "Insitro", "Color", "Komodo Health", "Included Health", "Oscar Health",
    "Cedar", "Devoted Health", "Verily", "Benchling", "Ginkgo Bioworks",
    # ---- multimodal / creative / stretch ---------------------------------
    "Descript", "Twelve Labs", "Wispr AI", "Samsung Research", "Adobe",
    "Captions", "OpusClip", "HeyGen", "Synthesia", "D-ID", "Tavus",
    "Photoroom", "Play.ht", "Fal", "Krea",
]


def candidates(name):
    """Plausible board slugs for a company name, most-likely first."""
    base = name.lower().strip()
    base = re.sub(r"\(.*?\)", "", base)
    base = base.replace("&", "and").replace(".", "")
    compact = re.sub(r"[^a-z0-9]", "", base)
    hyphen = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    out = [compact, hyphen]
    # with/without a trailing "ai"
    if compact.endswith("ai"):
        out.append(compact[:-2])
    else:
        out.append(compact + "ai")
    # drop common suffix words
    stripped = re.sub(
        r"(labs|lab|platforms|health|healthcare|research|group|inc|technologies"
        r"|capital|trading|securities|company|corp|motors|space|systems)$",
        "", compact)
    if stripped and stripped != compact:
        out.append(stripped)
    # first word only, for multiword names ("Jane Street" -> "jane")
    first = re.split(r"[^a-z0-9]+", base)[0]
    if first and first != compact:
        out.append(first)
    seen, uniq = set(), []
    for c in out:
        if c and len(c) > 2 and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def probe(args):
    ats, slug = args
    url, extract = EP[ats]
    try:
        req = urllib.request.Request(url.format(s=slug), headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=12).read())
        jobs = extract(d)
        n = len(jobs) if isinstance(jobs, list) else 0
        return (ats, slug, n) if n > 0 else None
    except Exception:
        return None


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    json_out = None
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        json_out = sys.argv[i + 1] if len(sys.argv) > i + 1 else "discovered.json"
        argv = [a for a in argv if a != json_out]
    names = argv or COMPANIES

    tasks, owner = [], {}
    for name in names:
        for slug in candidates(name):
            for ats in EP:
                tasks.append((ats, slug))
                owner[(ats, slug)] = name

    results = {}
    with ThreadPoolExecutor(max_workers=32) as ex:
        for r in ex.map(probe, tasks):
            if r:
                ats, slug, n = r
                name = owner[(ats, slug)]
                # keep the hit with the most jobs
                if name not in results or n > results[name][2]:
                    results[name] = (ats, slug, n)

    print("\n=== RESOLVED ===")
    for name in names:
        if name in results:
            ats, slug, n = results[name]
            print(f'    {{"name": "{name}", "ats": "{ats}", "slug": "{slug}"}},  // {n} jobs')

    print("\n=== NOT FOUND (simplify-only) ===")
    for name in names:
        if name not in results:
            print(f'    {{"name": "{name}", "ats": "none", "slug": ""}},')

    if json_out:
        with open(json_out, "w") as f:
            json.dump({n: {"ats": v[0], "slug": v[1], "jobs": v[2]}
                       for n, v in results.items()}, f, indent=2)
        print(f"\nWrote {json_out} ({len(results)}/{len(names)} resolved).")


if __name__ == "__main__":
    main()
