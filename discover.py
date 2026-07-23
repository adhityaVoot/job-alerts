#!/usr/bin/env python3
"""
One-off helper: given company names, probe Greenhouse / Lever / Ashby for a
working job-board slug. Prints rows ready to paste into sources.json.

Not used by the poller. Kept in the repo because it's how sources.json gets
extended, and re-running it is how you fix a company that changed ATS.

  python3 discover.py
"""
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "Mozilla/5.0"}
EP = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}?includeCompensation=false",
}

COMPANIES = [
    # voice / TTS / STT
    "Resemble AI", "Rime AI", "LMNT", "Neuphonic", "Camb.ai", "Papla Platforms",
    "WellSaid Labs", "Murf AI", "LOVO", "Speechify", "Respeecher", "Gladia",
    "Speechmatics", "Picovoice", "Krisp", "Soniox", "aiOla",
    # voice agents
    "Retell AI", "Bland AI", "Vocode", "Daily", "Sindarin", "PolyAI", "Cresta",
    "Parloa", "Synthflow", "Phonely", "Hamming AI", "Coval", "Regal AI",
    "Replicant", "Cognigy", "Sierra", "Decagon", "Thoughtly", "Slang.ai",
    "Goodcall",
    # music
    "Spotify", "Splice", "Output", "LANDR", "Moises", "Music.AI", "Rightsify",
    "Musixmatch", "Cyanite", "Musical AI", "Kits AI", "Voice-Swap", "Sony AI",
    "Universal Music Group", "Stability AI", "Endel", "Beatoven.ai",
    # infra / dev tools
    "OpenAI", "Mistral AI", "Replicate", "LangChain", "LlamaIndex", "Pinecone",
    "Weaviate", "Qdrant", "Chroma", "Braintrust", "Portkey", "Helicone",
    # health voice
    "Hippocratic AI", "Abridge", "Ambience Healthcare", "Suki AI", "Kintsugi",
    "Ellipsis Health", "Sonde Health",
    # multimodal / stretch
    "Descript", "Wispr AI", "Twelve Labs", "Runway", "Adobe", "Samsung Research",
]


def candidates(name):
    """Plausible board slugs for a company name, most-likely first."""
    base = name.lower().strip()
    base = re.sub(r"\(.*?\)", "", base)
    base = base.replace("&", "and")
    compact = re.sub(r"[^a-z0-9]", "", base)
    hyphen = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    out = [compact, hyphen]
    # with/without a trailing "ai"
    if compact.endswith("ai"):
        out.append(compact[:-2])
    else:
        out.append(compact + "ai")
    # drop common suffix words
    stripped = re.sub(r"(labs|platforms|health|healthcare|research|group|inc)$", "", compact)
    if stripped and stripped != compact:
        out.append(stripped)
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def probe(args):
    ats, slug = args
    try:
        req = urllib.request.Request(EP[ats].format(s=slug), headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=12).read())
        jobs = d.get("jobs", d) if isinstance(d, dict) else d
        n = len(jobs) if isinstance(jobs, list) else 0
        return (ats, slug, n) if n > 0 else None
    except Exception:
        return None


def main():
    tasks, owner = [], {}
    for name in COMPANIES:
        for slug in candidates(name):
            for ats in EP:
                tasks.append((ats, slug))
                owner[(ats, slug)] = name

    results = {}
    with ThreadPoolExecutor(max_workers=24) as ex:
        for r in ex.map(probe, tasks):
            if r:
                ats, slug, n = r
                name = owner[(ats, slug)]
                # keep the hit with the most jobs
                if name not in results or n > results[name][2]:
                    results[name] = (ats, slug, n)

    print("\n=== RESOLVED ===")
    for name in COMPANIES:
        if name in results:
            ats, slug, n = results[name]
            print(f'    {{"name": "{name}", "ats": "{ats}", "slug": "{slug}"}},  // {n} jobs')

    print("\n=== NOT FOUND (simplify-only) ===")
    for name in COMPANIES:
        if name not in results:
            print(f'    {{"name": "{name}", "ats": "none", "slug": ""}},')


if __name__ == "__main__":
    main()
