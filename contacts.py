#!/usr/bin/env python3
"""Referral contacts finder: for each NEW good-fit role, suggest who to look for (the recruiter, then employees in the same or an adjacent role
at the company), with LinkedIn and Google search links. No messages are drafted and nothing is ever sent or scraped:
the links open in the user's own browser, and the user picks the people. State file: out/contacts.json."""
import json, re, threading
from datetime import date
from urllib.parse import quote
import paths

FILE = paths.OUT / "contacts.json"
_CFG = paths.load_config()
START = _CFG.get("contacts_since") or date.today().isoformat()  # new positions only: roles found on or after this day (config "contacts_since", default today)
MIN_FIT = 60
STATUSES = ["not contacted", "messaged", "replied", "chat done", "referral asked", "referral given", "no reply"]
_L = threading.Lock()

SENIORITY = r"\b(senior|sr\.?|junior|jr\.?|lead|principal|staff|associate|head of|director of|vp of|global|regional|emea|apac|remote|hybrid|fully remote)\b"
ADJACENT = [  # (pattern on the role title, [adjacent titles to look for])
    (r"legal|counsel|compliance|contract", ["Legal Counsel", "Head of Legal", "Legal Operations Manager"]),
    (r"product", ["Product Manager", "Head of Product", "Product Operations Manager"]),
    (r"partnership|business development|alliance|account", ["Partnerships Manager", "Business Development Manager", "Head of Partnerships"]),
    (r"procure|supply|vendor|logistics|sourcing", ["Procurement Manager", "Supply Chain Manager", "Head of Procurement"]),
    (r"strategy|strategic|chief of staff", ["Strategy Manager", "Chief of Staff", "Head of Strategy"]),
    (r"project|programme|program manager|pmo|delivery", ["Project Manager", "Programme Manager", "Head of PMO"]),
    (r"analyst|analytics|data|research", ["Business Analyst", "Senior Analyst", "Head of Analytics"]),
    (r"operations|ops\b|operational", ["Operations Manager", "Head of Operations", "Business Operations Manager"]),
    (r"marketing|brand|content|growth|seo|social media|communications", ["Marketing Manager", "Head of Marketing", "Growth Manager"]),
    (r"sales|sdr|bdr|account executive|revenue", ["Sales Manager", "Head of Sales", "Sales Development Manager"]),
    (r"customer success|customer support|customer experience|support", ["Customer Success Manager", "Head of Customer Success", "Support Manager"]),
    (r"\bhr\b|people|talent|recruit", ["People Manager", "Head of People", "HR Business Partner"]),
    (r"finance|accounting|controller|fp&a", ["Finance Manager", "Financial Controller", "Head of Finance"]),
    (r"event|conference|community", ["Events Manager", "Community Manager", "Head of Events"]),
    (r"assistant|executive assistant|office manager|\bpa\b", ["Executive Assistant", "Office Manager", "Chief of Staff"]),
]
RECRUITER = "Recruiter / Talent Acquisition"   # always suggested: usually the first person who reads the application


def _core(title):
    t = re.sub(r"\([^)]*\)", " ", title or "")
    t = re.split(r"\s[|\-–—@]\s|,\s", t)[0]
    t = re.sub(SENIORITY, " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -,")


def _links(company, title):
    if title == RECRUITER:
        return {"li": "https://www.linkedin.com/search/results/people/?keywords=" + quote(f"recruiter {company}"),
                "g": "https://www.google.com/search?q=" + quote(f'site:linkedin.com/in "{company}" (recruiter OR "talent acquisition")')}
    kw = f"{title} {company}"
    return {"li": "https://www.linkedin.com/search/results/people/?keywords=" + quote(kw),
            "g": "https://www.google.com/search?q=" + quote(f'site:linkedin.com/in "{company}" "{title}"')}


def suggest(row):
    """Who to look for: the company's recruiter first, then up to 3 titles (the same role, then adjacent roles in the same function)."""
    company = re.sub(r"\s+(inc|ltd|llc|gmbh|sl|s\.l\.|plc)\.?$", "", (row.get("company") or "").strip(), flags=re.I)
    core = _core(row.get("title"))
    titles = [core] if core else []
    low = (row.get("title") or "").lower()
    for rx, alts in ADJACENT:
        if re.search(rx, low):
            titles += [a for a in alts if a.lower() != core.lower()]
            break
    seen, out = set(), []
    for t in titles:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return [{"id": f"p{i + 1}", "label": t, **_links(company, t), "status": "not contacted", "name": "", "note": ""} for i, t in enumerate([RECRUITER] + out[:3])]


def load():
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save(d):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False))
    tmp.replace(FILE)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0


def eligible(r):
    if (r.get("source") or "") in (_CFG.get("contacts_skip_sources") or []) or (r.get("found") or "") < START:
        return False
    if r.get("status") not in ("new", "drafted", "applied"):
        return False
    return max(_num(r.get("fit")), _num(r.get("score"))) >= MIN_FIT


def ensure(rows):
    """Create a contacts entry for every eligible role that has none yet (idempotent)."""
    with _L:
        d = load()
        changed = False
        for r in rows:
            if r["id"] not in d and eligible(r):
                p = suggest(r)
                if p:
                    d[r["id"]] = {"created": date.today().isoformat(), "people": p}
                    changed = True
        if changed:
            _save(d)
        return d


def update(jid, pid, **kv):
    """Set status / name / note of one suggested person. Returns the entry, or None if unknown."""
    with _L:
        d = load()
        for p in (d.get(jid) or {}).get("people", []):
            if p["id"] == pid:
                if kv.get("status") in STATUSES:
                    p["status"] = kv["status"]
                for k in ("name", "note"):
                    if k in kv:
                        p[k] = str(kv[k])[:300]
                _save(d)
                return d[jid]
    return None
