#!/usr/bin/env python3
"""Find the DIRECT apply page for a job found on a job board (usually LinkedIn), so you can skip LinkedIn Easy Apply.
Order: (1) company's own ATS board (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Workable, Workday) matched by company + title;
(2) otherwise flagged 'unresolved' with ready-made search links. Results are cached in out/ats_map.json (company -> ATS + slug)."""
import difflib, json, re, time, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
import paths

CACHE = paths.OUT / "ats_map.json"
UA = {"User-Agent": "Mozilla/5.0"}
SUFFIX = r"\b(inc|ltd|llc|gmbh|sl|slu|s\.?a\.?|plc|group|holding|holdings|international|españa|espana|spain|iberia|europe|emea|corp|corporation|company|technologies|technology|solutions|services|consulting|the)\b"


def http(url, body=None, timeout=15):
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, {**UA, **({"Content-Type": "application/json"} if body is not None else {})})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower())


def slugs(company):
    base = re.sub(SUFFIX, " ", norm(company)).split()
    if not base:
        return []
    out = ["".join(base), "-".join(base), base[0]]
    return list(dict.fromkeys(x for x in out if len(x) > 2))


# each lister returns [(title, location, url)] or raises
def l_greenhouse(slug):
    return [(j["title"], (j.get("location") or {}).get("name", ""), j["absolute_url"]) for j in http(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")["jobs"]]


def l_lever(slug):
    d = http(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    return [(j["text"], (j.get("categories") or {}).get("location", ""), j["hostedUrl"]) for j in d]


def l_ashby(slug):
    return [(j["title"], j.get("location", ""), j["jobUrl"]) for j in http(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")["jobs"]]


def l_smartrecruiters(slug):
    d = http(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")["content"]
    return [(j["name"], ", ".join(x for x in [j["location"].get("city"), j["location"].get("country")] if x), f"https://jobs.smartrecruiters.com/{slug}/{j['id']}") for j in d]


def l_recruitee(slug):
    return [(j["title"], j.get("location", ""), j["careers_url"]) for j in http(f"https://{slug}.recruitee.com/api/offers/")["offers"]]


def l_workable(slug):
    d = http(f"https://apply.workable.com/api/v3/accounts/{slug}/jobs", {"query": "", "location": [], "department": [], "worktype": [], "remote": []})
    return [(j["title"], ", ".join(x for x in [(j.get("location") or {}).get("city"), (j.get("location") or {}).get("country")] if x), f"https://apply.workable.com/{slug}/j/{j['shortcode']}/") for j in d.get("results", [])]


LISTERS = {"greenhouse": l_greenhouse, "lever": l_lever, "ashby": l_ashby, "smartrecruiters": l_smartrecruiters, "recruitee": l_recruitee, "workable": l_workable}


def load_cache():
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def save_cache(c):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, indent=1))


def detect(company, cache):
    """Which ATS hosts this company? Cached; negative results are retried after 14 days."""
    key = norm(company).strip()
    hit = cache.get(key)
    if hit is not None and (hit.get("ats") or datetime.fromisoformat(hit["checked"]) > datetime.now() - timedelta(days=14)):
        return hit
    found = {"ats": None, "slug": None, "checked": datetime.now().isoformat(timespec="seconds")}
    # known configured boards first
    for s in slugs(company):
        for ats, fn in LISTERS.items():
            try:
                if fn(s):
                    found.update(ats=ats, slug=s)
                    cache[key] = found
                    return found
            except Exception:
                continue
    cache[key] = found
    return found


def title_match(a, b):
    ta, tb = set(norm(a).split()), set(norm(b).split())
    ratio = difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(ratio, overlap * 0.9)


def find_direct(company, title, location="", cache=None):
    """-> dict(url, via) or dict(url='', via='unresolved')"""
    cache = cache if cache is not None else load_cache()
    d = detect(company, cache)
    if d.get("ats"):
        try:
            best = max(LISTERS[d["ats"]](d["slug"]), key=lambda j: title_match(title, j[0]) + (0.05 if any(w in norm(location) for w in norm(j[1]).split() if len(w) > 3) else 0), default=None)
            if best and title_match(title, best[0]) >= 0.8:
                return {"url": best[2], "via": f"company ATS ({d['ats']})"}
            return {"url": "", "via": f"unresolved (company uses {d['ats']}, but this exact role was not listed)"}
        except Exception:
            pass
    return {"url": "", "via": "unresolved"}


def search_links(company, title):
    q = lambda s: urllib.parse.quote_plus(s)
    return {
        "Google: company careers page": f"https://www.google.com/search?q={q(company + ' careers ' + title)}",
        "Google: direct ATS postings": f"https://www.google.com/search?q={q(chr(34) + title + chr(34) + ' ' + company + ' (site:greenhouse.io OR site:lever.co OR site:ashbyhq.com OR site:myworkdayjobs.com OR site:smartrecruiters.com OR site:workable.com)')}",
        "LinkedIn: recruiters at company": f"https://www.linkedin.com/search/results/people/?keywords={q(company + ' talent acquisition recruiter')}",
        "LinkedIn: hiring manager": f"https://www.linkedin.com/search/results/people/?keywords={q(company + ' ' + title.split(',')[0] + ' head OR director OR manager')}",
    }


def linkedin_mode(html):
    """'offsite' = LinkedIn's Apply button leaves LinkedIn (company ATS exists); otherwise Easy Apply only."""
    return "offsite" if "offsite-apply" in html else "easy-apply"


if __name__ == "__main__":
    import sys
    c = load_cache()
    print(find_direct(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "", c))
    save_cache(c)
