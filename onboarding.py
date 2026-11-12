"""First-run setup wizard: CV -> profile (AI draft, user reviews) -> job preferences -> generated config, answers and first search.
Everything is written to DATA (git-ignored). Standard library only; the AI step uses the logged-in `claude` CLI or ANTHROPIC_API_KEY."""
import base64, io, json, os, re, shutil, subprocess, urllib.request, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import paths, salary

DATA = paths.DATA
EXTRA_PATH = "/opt/homebrew/bin:/usr/local/bin:" + str(Path.home() / ".local/bin") + ":" + str(Path.home() / ".claude/local")

REGIONS = {  # label -> (words that make a remote posting acceptable, words that make it unacceptable when not chosen)
    "Worldwide / anywhere": (["worldwide", "anywhere", "global"], []),
    "Europe / EU": (["europe", "emea", "eu", "european union"], ["europe only", "emea"]),
    "North America": (["usa", "u.s.", "united states", "canada", "north america"], ["usa", "u.s.", "united states", "us only", "canada", "north america"]),
    "Latin America": (["latam", "latin america", "brazil", "mexico", "argentina", "colombia"], ["latam", "latin america", "brazil", "mexico", "argentina", "colombia"]),
    "Asia-Pacific": (["apac", "asia", "india", "australia", "philippines", "singapore"], ["apac", "india", "australia", "philippines", "japan", "singapore"]),
    "Africa / Middle East": (["africa", "mena", "middle east"], ["africa", "mena", "middle east"]),
    "United Kingdom": (["united kingdom", "uk"], ["uk only", "united kingdom"]),
}
SOURCE_GROUPS = {
    "linkedin": ("LinkedIn public job search", ["linkedin"]),
    "boards": ("Company career boards (Greenhouse, Lever, Ashby, SmartRecruiters, Recruitee, Comeet)", ["greenhouse", "lever", "ashby", "smartrecruiters", "recruitee", "comeet", "ultipro"]),
    "big": ("Large employers (Workday, Amazon, Google, Microsoft)", ["workday", "bigco_workday", "amazon", "amazon_virtual", "google", "microsoft"]),
    "remote": ("Remote job boards (Remotive, RemoteOK, We Work Remotely, Working Nomads and more)", ["remotive", "remotive_all", "remoteok", "arbeitnow", "jobicy", "himalayas", "wwr", "wwr_all", "workingnomads", "remocate", "authenticjobs", "skipthedrive", "jobspresso", "generalist", "workable", "eures"]),
}
SCHEMA_HINT = """{"name": "", "contact": ["email", "phone", "City, Country", "linkedin url"], "headline_default": "short professional headline",
 "stats": [["15 yrs", "what it measures"]], "education": [["Degree or programme", "Institution · years"]], "credentials": ["licence or certificate"],
 "skills": [["Group name", "skill · skill · skill"]], "languages": ["English — Native"],
 "jobs": [{"title": "", "org": "Employer, City", "dates": "Mon YYYY – Mon YYYY", "section": "", "bullets": ["achievement with a number and the person's own role"]}],
 "other_experience": ["hobby, side skill, volunteering or own project stated in the CV that is not one of the jobs"],
 "notes_for_tailoring": "2-4 sentences: how to frame this person (their strengths, what to never claim)"}"""


# ---------------------------------------------------------------- AI backend
def backend():
    if os.environ.get("TAILOR_MOCK"):
        return "mock"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api-key"
    if shutil.which("claude", path=os.environ.get("PATH", "") + ":" + EXTRA_PATH):
        return "claude-cli"
    return "none"


def ask(prompt):
    be = backend()
    if be == "none":
        raise RuntimeError("No AI connected. Install the Claude CLI and log in, or add an Anthropic API key in step 1.")
    if be == "mock":
        return json.loads(Path(paths.ROOT / "examples" / "profile.example.json").read_text())
    if be == "api-key":
        body = json.dumps({"model": os.environ.get("TAILOR_MODEL", "claude-sonnet-5-5"), "max_tokens": 6000, "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", body, {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
        out = json.loads(urllib.request.urlopen(req, timeout=180).read())["content"][0]["text"]
    else:
        env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":" + EXTRA_PATH}
        r = subprocess.run(["claude", "-p", "--model", "sonnet"], input=prompt, capture_output=True, text=True, timeout=300, env=env)
        out = r.stdout
        if r.returncode or "Failed to authenticate" in out:
            raise RuntimeError("The Claude CLI failed. Run `claude` once in Terminal and log in, then try again. " + (out or r.stderr)[:160])
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise RuntimeError("The AI reply had no JSON. Try again.")
    return json.loads(m.group(0))


def save_api_key(key):
    key = (key or "").strip()
    if not re.fullmatch(r"sk-[A-Za-z0-9_\-]{20,}", key):
        raise ValueError("That does not look like an Anthropic API key (it starts with sk-).")
    f = DATA / ".env"
    lines = [l for l in (f.read_text().splitlines() if f.exists() else []) if not l.startswith("ANTHROPIC_API_KEY=")]
    f.write_text("\n".join(lines + [f"ANTHROPIC_API_KEY={key}"]) + "\n")
    f.chmod(0o600)
    os.environ["ANTHROPIC_API_KEY"] = key


# ---------------------------------------------------------------- CV text extraction
def extract_text(filename, b64):
    """Text of an uploaded CV. Any temporary copy is removed afterwards."""
    try:
        return _extract_text(filename, b64)
    finally:
        for f in (DATA / "out").glob("cv_upload.*"):
            f.unlink(missing_ok=True)


def _extract_text(filename, b64):
    raw = base64.b64decode(b64)
    ext = Path(filename).suffix.lower()
    if ext in (".txt", ".md", ".text"):
        return raw.decode("utf-8", "ignore")
    if ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                root = ET.fromstring(z.read("word/document.xml"))
        except Exception:
            raise RuntimeError("Could not open this Word file. Save it again as .docx or PDF, or paste the text of your CV instead.")
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paras = ["".join(t.text or "" for t in p.iter(ns + "t")) for p in root.iter(ns + "p")]
        return "\n".join(paras)
    if ext == ".pdf":
        tool = shutil.which("pdftotext", path=os.environ.get("PATH", "") + ":" + EXTRA_PATH)
        if tool:
            r = subprocess.run([tool, "-layout", "-", "-"], input=raw, capture_output=True, timeout=60)
            if r.stdout.strip():
                return r.stdout.decode("utf-8", "ignore")
        tmp = DATA / "out" / "cv_upload.pdf"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(raw)
        mu = shutil.which("mutool", path=os.environ.get("PATH", "") + ":" + EXTRA_PATH)
        if mu:
            r = subprocess.run([mu, "draw", "-F", "txt", str(tmp)], capture_output=True, timeout=60)
            if r.stdout.strip():
                return r.stdout.decode("utf-8", "ignore")
        if shutil.which("osascript"):          # macOS: built-in PDFKit, nothing to install
            js = ('ObjC.import("PDFKit"); function run(a){var d=$.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(a[0]));'
                  'return d.isNil()?"":d.string.js}')
            r = subprocess.run(["osascript", "-l", "JavaScript", "-e", js, str(tmp)], capture_output=True, timeout=60)
            if r.stdout.strip():
                return r.stdout.decode("utf-8", "ignore")
        raise RuntimeError("Could not read text from this PDF (it may be a scanned image). Install poppler (`brew install poppler`), "
                           "upload a .docx, or paste the text of your CV instead.")
    if ext in (".doc", ".rtf", ".odt") and shutil.which("textutil"):   # macOS converter
        tmp = DATA / "out" / ("cv_upload" + ext)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(raw)
        r = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(tmp)], capture_output=True, timeout=60)
        if r.stdout.strip():
            return r.stdout.decode("utf-8", "ignore")
    raise RuntimeError("Use a .pdf, .docx, .doc, .rtf or .txt file, or paste the text.")


def parse_cv(text):
    text = (text or "").strip()
    if len(text) < 200:
        raise RuntimeError("The CV text is too short. Paste the full text of your CV.")
    prompt = ("Turn this CV into a JSON profile. Use ONLY facts stated in the CV: never invent employers, dates, numbers or credentials. "
              "Keep every achievement bullet that contains a number or a concrete result. Newest job first. "
              "Optional per job field \"section\": \"research\" for doctoral/academic research, \"projects\" for self-directed or side projects, \"extra\" for early or unrelated roles, otherwise empty. "
              "Also add a key \"_suggest\" with job-search suggestions for this person (these are advice, not facts, so they may go beyond the CV wording): "
              + SUGGEST_HINT + "\n"
              "Reply with ONE JSON object and nothing else, in exactly this shape:\n" + SCHEMA_HINT[:-1] + ',\n "_suggest": ' + SUGGEST_SHAPE + "}\n\nCV:\n" + text[:30000])
    p = ask(prompt)
    p.setdefault("name", "")
    for k, d in (("contact", []), ("stats", []), ("education", []), ("credentials", []), ("skills", []), ("languages", []), ("jobs", []), ("notes_for_tailoring", ""), ("headline_default", ""), ("other_experience", [])):
        p.setdefault(k, d)
    return p


SUGGEST_HINT = ("\"roles\": 6-10 job titles they can credibly apply for now, most likely first, using the plain titles recruiters post; include adjacent titles "
                "where the same day-to-day tasks are done under another name (count founder/owner work as experience in the tasks they did). "
                "\"also\": 4-8 extra adjacent or one-step-up titles worth a look. \"exclude\": title words to avoid (unrelated fields, far too senior or too junior). "
                "\"mode\": remote, hybrid or onsite (hybrid if the CV shows a home city). \"regions\": remote regions they can most likely work from, chosen ONLY from "
                "this list: " + ", ".join(REGIONS) + ". \"languages\": only languages at professional working level or better (leave out beginner/basic), "
                "including ones the CV shows through work, e.g. translating between two languages. "
                "\"why\": one sentence on the reasoning, written to the person as \"you\" / \"your\".")
SUGGEST_SHAPE = '{"roles": [], "also": [], "exclude": [], "mode": "hybrid", "regions": [], "languages": [], "why": ""}'
_LOW_LEVEL = re.compile(r"beginner|basic|elementary|a1|a2|learning|limited", re.I)


def suggest_prefs(profile, s=None):
    """Clean the AI's suggestions; fall back to the profile itself when the AI gave none (or the mock is used)."""
    s = s if isinstance(s, dict) else {}
    titles = []
    for j in profile.get("jobs", []):
        t = re.sub(r"\s*[\(\[].*", "", (j.get("title") or "")).strip()
        if t and not (j.get("section") or "") and t.lower() not in [x.lower() for x in titles]:
            titles.append(t)
    langs = [re.split(r"\s*[—–:(-]\s*", l)[0].strip() for l in profile.get("languages", []) if not _LOW_LEVEL.search(l)]
    city = (profile.get("contact") or ["", "", ""])[2:3]
    roles = _lines(s.get("roles")) or titles[:6]
    return {"roles": roles[:12],
            "also": [x for x in _lines(s.get("also")) if x.lower() not in [r.lower() for r in roles]][:8],
            "exclude": _lines(s.get("exclude"))[:12],
            "mode": s.get("mode") if s.get("mode") in ("remote", "hybrid", "onsite") else ("hybrid" if city and city[0] else "remote"),
            "regions": [r for r in _lines(s.get("regions")) if r in REGIONS] or ["Worldwide / anywhere"],
            "languages": _lines(s.get("languages")) or [x for x in langs if x] or ["English"],
            "why": (s.get("why") or ("Based on the job titles in your CV." if not s else "")).strip()[:300],
            "from_ai": bool(s)}


def typical_pay(prefs, profile):
    """AI estimate of the market range for the target roles where the person will work (gross per year). Shown in step 5 and saved as salary bands,
    so every posting without a stated salary gets an indicative range."""
    roles = _lines(prefs.get("roles"))[:10]
    if not roles:
        raise ValueError("Add job titles in step 4 first.")
    if backend() == "mock":
        return {"currency": "EUR", "low": 38000, "high": 55000, "by_role": [{"role": r, "low": 38000, "high": 55000} for r in roles], "note": "Test mode: sample numbers."}
    where = ", ".join(x for x in (prefs.get("city"), prefs.get("country")) if x) or "not given"
    hist = "; ".join(f'{j.get("title", "")} ({j.get("dates", "")})' for j in (profile.get("jobs") or [])[:8])
    prompt = ("Estimate realistic typical gross annual salaries (market average, not optimistic) for these job titles: " + "; ".join(roles) +
              f". Candidate location: {where}. Work mode: {prefs.get('mode', 'remote')}. Remote regions: {', '.join(_lines(prefs.get('regions'))) or 'none'}. "
              f"Candidate background (titles and dates): {hist}. Use the local currency of the candidate's country (USD if unknown). Whole numbers per year. "
              'Reply with ONE JSON object only: {"currency": "EUR", "low": 0, "high": 0, "by_role": [{"role": "", "low": 0, "high": 0}], '
              '"note": "one short sentence on what the range is based on"}')
    r = ask(prompt)
    num = lambda v: int(float(str(v).replace(",", ""))) if str(v).replace(",", "").replace(".", "").isdigit() else 0
    out = {"currency": str(r.get("currency") or "EUR").upper()[:3], "low": num(r.get("low")), "high": num(r.get("high")), "note": str(r.get("note") or "")[:240],
           "by_role": [{"role": str(b.get("role")), "low": num(b.get("low")), "high": num(b.get("high"))} for b in (r.get("by_role") or [])
                       if isinstance(b, dict) and b.get("role") and 0 < num(b.get("low")) < num(b.get("high"))][:10]}
    if not 0 < out["low"] < out["high"]:
        raise RuntimeError("The AI did not return a usable salary range. Try again or skip it.")
    return out


# ---------------------------------------------------------------- preferences -> generated files
def _lines(v):
    if isinstance(v, str):
        v = re.split(r"[\n,;]+", v)
    if not isinstance(v, (list, tuple)):
        v = []
    return [str(x).strip() for x in v if isinstance(x, (str, int, float)) and str(x).strip()]


def build_config(prefs, profile):
    roles = [r.lower() for r in _lines(prefs.get("roles"))]
    if not roles:
        raise ValueError("Add at least one job title you are looking for.")
    mode = prefs.get("mode", "remote")  # remote | hybrid | onsite
    city, country = (prefs.get("city") or "").strip(), (prefs.get("country") or "").strip()
    regions = [r for r in _lines(prefs.get("regions")) if r in REGIONS] or (["Worldwide / anywhere"] if mode == "remote" else [])
    langs = _lines(prefs.get("languages")) or ["English"]
    ok = {"remote", "worldwide", "anywhere"} if mode != "onsite" else set()
    bad = set()
    for label, (good, no) in REGIONS.items():
        if label in regions:
            ok.update(good)
        else:
            bad.update(no)
    bad -= ok
    onsite = [x.lower() for x in (city, country) if x] if mode in ("hybrid", "onsite") else []
    ok.update(onsite)
    skills = [s.strip().lower() for g in profile.get("skills", []) for s in re.split(r"[·;,]", g[1] if isinstance(g, list) and len(g) > 1 else str(g)) if s.strip()]
    exclude = _lines(prefs.get("exclude")) + ["intern", "internship", "unpaid", "volunteer"]
    searches = []
    for r in roles:
        if onsite and city:
            searches.append({"keywords": r, "location": f"{city}, {country}".strip(", ")})
        if mode != "onsite":
            for reg in regions:
                searches.append({"keywords": r, "location": {"Worldwide / anywhere": "Worldwide", "Europe / EU": "Europe", "North America": "United States", "Latin America": "Latin America", "Asia-Pacific": "Asia", "Africa / Middle East": "Middle East", "United Kingdom": "United Kingdom"}[reg], "remote": True})
    sel = set(prefs.get("sources") or SOURCE_GROUPS)
    wanted = {s for g in sel if g in SOURCE_GROUPS for s in SOURCE_GROUPS[g][1]}
    default = json.loads((paths.ROOT / "defaults" / "config.json").read_text())
    cfg = {
        "title_keywords": {r: 40 for r in roles},
        "title_exclude": sorted(set(x.lower() for x in exclude)),
        "description_keywords": {s: 3 for s in skills[:20]},
        "location_ok": sorted(ok),
        "location_bad": sorted(bad),
        "onsite_ok_cities": onsite,
        "linkedin_searches": searches,
        "workable_queries": roles, "workable_locations": [x for x in (city, country, "Remote") if x],
        "workday_queries": roles, "bigco_queries": roles,
        "workday_location_words": [x for x in (city.lower(), country.lower(), "remote") if x],
        "fast_sources": [s for s in default["fast_sources"] if s in wanted],
        "background_sources": [s for s in default["background_sources"] if s in wanted],
        "home_city": city, "home_country": country, "languages_spoken": langs,
        "timezone_note": (prefs.get("timezone") or "").strip(),
        "allow_remote_worldwide": "Worldwide / anywhere" in regions,
    }
    rng = prefs.get("salary_range") or {}
    if rng.get("low") and rng.get("high"):   # typical pay -> indicative ranges for postings that state none
        cfg["salary_currency"] = rng.get("currency") or "EUR"
        cfg["salary_bands"] = [[re.escape(b["role"].lower()), round(b["low"] / 1000), round(b["high"] / 1000), "typical for " + b["role"]] for b in rng.get("by_role", [])]
        cfg["salary_default"] = [round(rng["low"] / 1000), round(rng["high"] / 1000), "typical for your target roles"]
        cfg["salary_region_note"] = (", ".join(x for x in (city, country) if x) or "your region") + " gross, AI estimate"
    exp = salary.to_yearly(prefs.get("salary"))
    if exp:
        cfg["salary_expectation"] = exp   # ranks well-paid roles higher; lower-paid ones are flagged for review, never hidden
    return cfg


def build_answers(prefs, profile):
    name = profile.get("name", "")
    items = [
        {"q": "Why are you interested in this role / company?", "a": "{why_us}", "needs_input": False},
        {"q": "Tell us about yourself (short summary)", "a": "{summary}", "needs_input": False},
    ]

    def add(q, key, label):
        v = (prefs.get(key) or "").strip()
        items.append({"q": q, "a": v, "needs_input": not v, **({} if v else {"hint": f"Fill in: {label}"})})
    add("Salary expectations", "salary", "your salary expectation, with currency and period")
    add("Earliest start date / notice period", "start", "your start date or notice period")
    add("Work authorisation: are you legally allowed to work in the country of the role?", "authorisation", "your work authorisation")
    add("Do you need visa sponsorship?", "sponsorship", "yes or no")
    add("Where are you based?", "location_answer", "where you live")
    return {"_help": "Reusable answers for application forms. {company},{role},{why_us},{summary} are filled per job. needs_input=true means YOU must fill it in; the app never guesses personal or legal details.", "items": items}


def save_all(profile, prefs):
    if not (profile.get("name") or "").strip():
        raise ValueError("Add your name.")
    if not profile.get("jobs"):
        raise ValueError("Add at least one job.")
    cfg = build_config(prefs, profile)
    prefs = dict(prefs)
    prefs.setdefault("location_answer", ", ".join(x for x in (prefs.get("city"), prefs.get("country")) if x))
    DATA.mkdir(parents=True, exist_ok=True)
    profile = {k: v for k, v in profile.items() if not k.startswith("_")}
    (DATA / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=1))
    (DATA / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1))
    prefs = {k: v for k, v in prefs.items() if not k.startswith("_")}
    (DATA / "preferences.json").write_text(json.dumps(prefs, ensure_ascii=False, indent=1))
    (DATA / "answers.json").write_text(json.dumps(build_answers(prefs, profile), ensure_ascii=False, indent=1))
    for n, d in (("stories.json", {"stories": []}), ("network.json", {"contacts": []}), ("outreach_templates.json", {"templates": []})):
        if not (DATA / n).exists():
            (DATA / n).write_text(json.dumps(d))
    try:
        import tailor, fit
        tailor.refresh(); fit.sync(force=True)
    except Exception:
        pass
    return {"ok": True, "sources": len(cfg["fast_sources"]), "searches": len(cfg["linkedin_searches"])}


def _draft():
    try:
        return paths.read_json("setup_draft.json", None)
    except Exception:          # a damaged draft must never block the wizard
        return None


def state():
    prefs = paths.read_json("preferences.json", {})
    return {"ai": backend(), "has_profile": paths.has_profile(), "draft": _draft(), "profile": paths.load_profile() if paths.has_profile() else None, "prefs": prefs,
            "regions": list(REGIONS), "sources": {k: v[0] for k, v in SOURCE_GROUPS.items()}}


# ---------------------------------------------------------------- HTTP glue (called from app.py)
def post(path, d):
    """Returns a JSON-able dict. Raises on user errors (app.py turns them into {"error": ...})."""
    if path == "/api/setup/key":
        save_api_key(d.get("key"))
        return {"ai": backend()}
    if path == "/api/setup/extract":
        return {"text": extract_text(d["name"], d["data"])}
    if path == "/api/setup/parse":
        p = parse_cv(d.get("text"))
        return {"profile": p, "suggest": suggest_prefs(p, p.pop("_suggest", None))}
    if path == "/api/setup/save":
        out = save_all(d["profile"], d["prefs"])
        (DATA / "setup_draft.json").unlink(missing_ok=True)
        return out
    if path == "/api/setup/draft":   # every Next/Back stores what was typed so far, so a reload or a crash loses nothing
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "setup_draft.json").write_text(json.dumps({k: d.get(k) for k in ("step", "P", "PR", "SG", "CV")}, ensure_ascii=False))
        return {"ok": True}
    if path == "/api/setup/salary":
        return {"range": typical_pay(d.get("prefs") or {}, d.get("profile") or {})}
    raise KeyError(path)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Job Hunt setup</title>
<style>
.help{margin-top:28px;font-size:13px;border-top:1px solid var(--line);padding-top:10px}
.sug{border:1px solid var(--acc);border-radius:10px;padding:10px 12px;margin:8px 0 4px}.chip{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:14px;padding:3px 10px;margin:4px 6px 0 0;cursor:pointer;font-size:13px}
:root{--bg:#f6f7f6;--card:#fff;--ink:#1d2420;--mut:#5d6a62;--line:#dfe4e0;--acc:#1f5c3a;--warn:#b4540a}
@media(prefers-color-scheme:dark){:root{--bg:#14181a;--card:#1c2224;--ink:#e8ece9;--mut:#9aa7a0;--line:#2b3337;--acc:#4fa578;--warn:#e8a15a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 80px}h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:0 0 6px}p.mut,.mut{color:var(--mut)}
.steps{display:flex;gap:6px;margin:16px 0}.steps i{flex:1;height:5px;border-radius:3px;background:var(--line)}.steps i.on{background:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin:14px 0}
label{display:block;font-weight:600;margin:12px 0 4px}input,textarea,select{width:100%;font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
textarea{min-height:90px;resize:vertical}.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:560px){.row{grid-template-columns:1fr}}
button{font:inherit;border-radius:8px;border:1px solid var(--acc);background:var(--acc);color:#fff;padding:9px 16px;cursor:pointer}button.sec{background:transparent;color:var(--acc)}button:disabled{opacity:.5}
.nav{display:flex;justify-content:space-between;gap:10px;margin-top:16px}.err{color:var(--warn);margin-top:8px;white-space:pre-wrap}.ok{color:var(--acc)}
.chk{display:flex;gap:8px;align-items:flex-start;font-weight:400;margin:6px 0}.chk input{width:auto;margin-top:4px}.job{border:1px solid var(--line);border-radius:10px;padding:12px;margin:10px 0}
small{color:var(--mut)}
</style></head><body><main>
<h1>Set up Job Hunt</h1><p class="mut">Five short steps. Everything stays on your computer in the <code>data</code> folder.</p>
<div class="steps" id="bar"></div><div id="view"></div>
<p class="mut help">Stuck? Open this folder in Claude (Claude Code, or the Code tab in the Claude desktop app) and say &quot;help me set up jobhunt&quot;. The folder has a guide for Claude, so it can finish the setup with you or fix what failed.</p></main>
<script>
const $=s=>document.querySelector(s);const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let S={},step=0,P=null,PR={roles:'',mode:'remote',regions:['Worldwide / anywhere'],languages:'English',exclude:'',sources:['linkedin','boards','big','remote']},CV='',SG=null;
async function post(p,b){const r=await fetch(p,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(b)});return r.json()}
const NAMES=['Connect AI','Your CV','Check your profile','What you want','Answers and sources'];
function bar(){$('#bar').innerHTML=NAMES.map((n,i)=>`<i class="${i<=step?'on':''}" title="${n}"></i>`).join('')}
function err(m){const e=$('#err');if(!e)return;e.textContent=m||'';if(m){const h=document.createElement('div');h.className='mut';h.style.marginTop='6px';h.textContent='If this keeps happening, open this folder in Claude and ask it to fix it (see the note at the bottom of the page).';e.appendChild(h)}}
function nav(back,next,label){return `<div class="nav"><button class="sec" onclick="go(${step-1})" ${back?'':'style="visibility:hidden"'}>Back</button><button id="nx" onclick="${next}">${label||'Next'}</button></div><div class="err" id="err"></div>`}
function collect(){if($('#cv'))CV=$('#cv').value;readProfile();readPrefs();readAnswers()}
let _sd;function saveDraft(){clearTimeout(_sd);_sd=setTimeout(()=>post('/api/setup/draft',{step,P,PR,SG,CV}),150)}
function go(n){collect();step=n;saveDraft();draw();window.scrollTo(0,0)}
function draw(){bar();[s0,s1,s2,s3,s4][step]()}
function s0(){const ok=S.ai!=='none';$('#view').innerHTML=`<div class="card"><h2>1. Connect an AI</h2>
<p class="mut">The app uses Claude to read your CV and to tailor a CV and cover letter for each job. Nothing is ever sent to an employer for you.</p>
<p>${ok?`<span class="ok">Connected: ${S.ai==='claude-cli'?'Claude CLI (logged in on this computer)':S.ai==='api-key'?'Anthropic API key':'test mode'}.</span>`:'<b>No AI found yet.</b> Either install the Claude CLI and log in (run <code>claude</code> once in Terminal), or paste an Anthropic API key below.'}</p>
<label>Anthropic API key (optional)</label><input id="key" type="password" placeholder="sk-ant-..." autocomplete="off"><small>Saved only in <code>data/.env</code> on this computer.</small>
<div style="margin-top:8px"><button class="sec" onclick="saveKey()">Save key</button></div></div>`+nav(false,'go(1)','Next')}
async function saveKey(){err();const r=await post('/api/setup/key',{key:$('#key').value});if(r.error)return err(r.error);S.ai=r.ai;s0()}
function s1(){$('#view').innerHTML=`<div class="card"><h2>2. Your CV</h2><p class="mut">Upload a PDF, Word or text file, or paste the text. The AI drafts your profile; you check it in the next step. Only facts in your CV are used.</p>
<input type="file" id="f" accept=".pdf,.docx,.doc,.rtf,.odt,.txt,.md" onchange="upload(this)"><label>or paste the text of your CV</label><textarea id="cv" style="min-height:220px">${esc(CV)}</textarea></div>`+nav(true,'readCv()','Read my CV')}
function upload(el){const f=el.files[0];if(!f)return;const rd=new FileReader();rd.onload=async()=>{err();const r=await post('/api/setup/extract',{name:f.name,data:rd.result.split(',')[1]});if(r.error)return err(r.error);CV=r.text;$('#cv').value=CV};rd.readAsDataURL(f)}
async function readCv(){CV=$('#cv').value;if(P&&P._cv===CV.trim())return go(2);   // same CV as before: keep the checked profile, no new AI call
 const b=$('#nx');b.disabled=true;b.textContent='Reading... (about a minute)';err();
 const r=await post('/api/setup/parse',{text:CV});b.disabled=false;b.textContent='Read my CV';if(r.error)return err(r.error);const old=P;P=r.profile;P._cv=CV.trim();
 if(old){P.other_experience=[...new Set([...(old.other_experience||[]),...(P.other_experience||[])])];if(!(P.languages||[]).length)P.languages=old.languages||[];if(old.notes_for_tailoring&&!P.notes_for_tailoring)P.notes_for_tailoring=old.notes_for_tailoring}
 SG=r.suggest||null;if(SG)applySuggest(true);go(2)}
function applySuggest(onlyEmpty){if(!SG)return;const set=(k,v)=>{if(!onlyEmpty||!PR[k]||(k=='languages'&&PR[k]=='English'))PR[k]=v};
 set('roles',SG.roles.join('\n'));set('exclude',SG.exclude.join(', '));set('languages',SG.languages.join(', '));PR.mode=SG.mode;PR.regions=[...SG.regions]}
function addRole(t,btn){const e=$('#ro');const have=e.value.split(/[\n,]/).map(x=>x.trim().toLowerCase());if(!have.includes(t.toLowerCase()))e.value=(e.value.trim()?e.value.trim()+'\n':'')+t;btn.remove()}
function resetSuggest(){PR.roles=PR.exclude='';applySuggest(false);s3()}
function skillsText(p){return (p.skills||[]).map(g=>Array.isArray(g)?g[1]:g).join(' · ')}
function s2(){if(!P&&S.profile)P=S.profile;if(!P){P={name:'',contact:['','','',''],headline_default:'',education:[],credentials:[],skills:[],languages:[],jobs:[],stats:[],notes_for_tailoring:''}}
 const c=P.contact||[];$('#view').innerHTML=`<div class="card"><h2>3. Check your profile</h2><p class="mut">This is the only source of facts for your CVs and letters. Fix anything wrong, delete what you do not want used.</p>
<div class="row"><div><label>Full name</label><input id="nm" value="${esc(P.name)}"></div><div><label>Headline</label><input id="hl" value="${esc(P.headline_default)}"></div></div>
<div class="row"><div><label>Email</label><input id="em" value="${esc(c[0])}"></div><div><label>Phone</label><input id="ph" value="${esc(c[1])}"></div></div>
<div class="row"><div><label>City, country</label><input id="lo" value="${esc(c[2])}"></div><div><label>LinkedIn</label><input id="li" value="${esc(c[3])}"></div></div>
<label>Skills (separate with · or commas)</label><textarea id="sk">${esc(skillsText(P))}</textarea>
<label>Languages (one per line, e.g. English — Fluent)</label><textarea id="la" style="min-height:70px">${esc((P.languages||[]).join('\n'))}</textarea>
<label>Education (one per line: programme | institution and years)</label><textarea id="ed" style="min-height:70px">${esc((P.education||[]).map(e=>e.join(' | ')).join('\n'))}</textarea>
<label>Certificates and licences (one per line)</label><textarea id="cr" style="min-height:60px">${esc((P.credentials||[]).join('\n'))}</textarea>
<label>Other experience that could help for some roles (optional, one per line)</label><textarea id="ox" style="min-height:90px" placeholder="e.g. Cook daily for a family of five: meal planning, shopping, budgets&#10;Built my own online shop and coded the website&#10;Volunteer translator for a local charity">${esc((P.other_experience||[]).join('\n'))}</textarea><small>Hobbies, side skills, family or household work, volunteering, things you built for yourself. They are never shown as jobs: the AI only uses one when a posting asks for that skill.</small>
<label>How should the AI frame you? (optional notes, also what it must never claim)</label><textarea id="nt">${esc(P.notes_for_tailoring)}</textarea></div>
<h2 style="margin-top:18px">Jobs</h2><div id="jobs"></div><button class="sec" onclick="addJob()">+ Add a job</button>`+nav(true,'saveProfile()','Next');drawJobs()}
function drawJobs(){$('#jobs').innerHTML=(P.jobs||[]).map((j,i)=>`<div class="job"><div class="row"><div><label>Title</label><input data-i="${i}" data-k="title" value="${esc(j.title)}"></div><div><label>Employer, city</label><input data-i="${i}" data-k="org" value="${esc(j.org)}"></div></div>
<div class="row"><div><label>Dates</label><input data-i="${i}" data-k="dates" value="${esc(j.dates)}"></div><div><label>Type</label><select data-i="${i}" data-k="section">${[['','Normal job'],['research','Academic research'],['projects','Own projects'],['extra','Early or other role']].map(o=>`<option value="${o[0]}" ${(j.section||'')==o[0]?'selected':''}>${o[1]}</option>`).join('')}</select></div></div>
<label>Achievements (one per line, numbers and your own role)</label><textarea data-i="${i}" data-k="bullets">${esc((j.bullets||[]).join('\n'))}</textarea><button class="sec" style="margin-top:8px" onclick="delJob(${i})">Remove this job</button></div>`).join('')}
function syncJobs(){document.querySelectorAll('#jobs [data-k]').forEach(e=>{const j=P.jobs[+e.dataset.i];j[e.dataset.k]=e.dataset.k=='bullets'?e.value.split('\n').map(x=>x.trim()).filter(Boolean):e.value})}
function addJob(){syncJobs();P.jobs.push({title:'',org:'',dates:'',section:'',bullets:[]});drawJobs()}
function delJob(i){syncJobs();P.jobs.splice(i,1);drawJobs()}
function lines(id){return $(id).value.split('\n').map(x=>x.trim()).filter(Boolean)}
function readProfile(){if(!$('#nm'))return;syncJobs();P.name=$('#nm').value.trim();P.headline_default=$('#hl').value.trim();P.contact=[$('#em').value,$('#ph').value,$('#lo').value,$('#li').value].map(x=>x.trim());
 P.skills=[['Skills',$('#sk').value.split(/[·,\n]/).map(x=>x.trim()).filter(Boolean).join(' · ')]];P.languages=lines('#la');P.education=lines('#ed').map(l=>{const p=l.split('|');return [p[0].trim(),(p[1]||'').trim()]});P.credentials=lines('#cr');P.notes_for_tailoring=$('#nt').value.trim();P.other_experience=lines('#ox')}
function saveProfile(){readProfile();
 if(!P.name)return err('Add your name.');if(!P.jobs.length||!P.jobs.some(j=>j.title))return err('Add at least one job.');
 if(!PR.city){const l=$('#lo').value.split(',');PR.city=(l[0]||'').trim();PR.country=(l[1]||'').trim()}
 const low=/beginner|basic|elementary|\ba1\b|\ba2\b|learning|limited/i,work=[...new Set([...(SG?SG.languages:[]),...(P.languages||[]).filter(l=>!low.test(l)).map(x=>x.split(/\s*[—–:(-]\s*/)[0].trim()).filter(Boolean)])];
 if(work.length&&!PR._lgEdited)PR.languages=work.join(', ');go(3)}
function s3(){const R=S.regions||[];$('#view').innerHTML=`<div class="card"><h2>4. What you are looking for</h2>
${SG?`<div class="sug"><b>${SG.from_ai?'Suggested from your CV':'Filled in from the job titles in your CV'}</b>${SG.why?`<div class="mut">${esc(SG.why)}</div>`:''}<div class="mut">Everything below is a starting point: edit, delete or add freely.</div>${SG.also.length?`<label>Also worth a look (click to add)</label><div>${SG.also.map(t=>`<button type="button" class="chip" data-t="${esc(t)}" onclick="addRole(this.dataset.t,this)">+ ${esc(t)}</button>`).join('')}</div>`:''}<button type="button" class="sec" style="margin-top:8px" onclick="resetSuggest()">Reset to suggestions</button></div>`:''}
<label>Job titles to search for (one per line or comma-separated)</label><textarea id="ro" placeholder="project manager&#10;operations manager&#10;customer success manager">${esc(PR.roles)}</textarea>
<label>Titles or words to avoid (optional)</label><input id="ex" value="${esc(PR.exclude)}" placeholder="e.g. engineer, nurse, driver, sales">
<label>How do you want to work?</label><select id="md"><option value="remote" ${PR.mode=='remote'?'selected':''}>Remote only</option><option value="hybrid" ${PR.mode=='hybrid'?'selected':''}>Remote or hybrid near me</option><option value="onsite" ${PR.mode=='onsite'?'selected':''}>On-site or hybrid near me only</option></select>
<div class="row"><div><label>Your city</label><input id="ci" value="${esc(PR.city||'')}"></div><div><label>Your country</label><input id="co" value="${esc(PR.country||'')}"></div></div>
<label>Remote jobs from which regions can you take?</label>${R.map(r=>`<label class="chk"><input type="checkbox" name="rg" value="${esc(r)}" ${PR.regions.includes(r)?'checked':''}>${esc(r)}</label>`).join('')}
<label>Languages you can work in (comma-separated)</label><input id="lg" value="${esc(PR.languages)}"><small>Jobs that require another language are ranked down or skipped.</small>
<label>Time-zone or hours note (optional)</label><input id="tz" value="${esc(PR.timezone||'')}" placeholder="e.g. fine with US hours until midnight"></div>`+nav(true,'savePrefs()','Next')}
function readPrefs(){if(!$('#ro'))return;if($('#lg').value!==PR.languages)PR._lgEdited=true;PR.roles=$('#ro').value;PR.exclude=$('#ex').value;PR.mode=$('#md').value;PR.city=$('#ci').value.trim();PR.country=$('#co').value.trim();PR.regions=[...document.querySelectorAll('[name=rg]:checked')].map(x=>x.value);PR.languages=$('#lg').value;PR.timezone=$('#tz').value}
function savePrefs(){readPrefs();
 if(!PR.roles.trim())return err('Add at least one job title.');if(PR.mode!='remote'&&!PR.city)return err('Add your city for hybrid or on-site search.');go(4)}
function s4(){$('#view').innerHTML=`<div class="card"><h2>5. Answers and sources</h2><p class="mut">These fill the common application questions. Leave blank anything you want to answer yourself each time; the app never guesses personal or legal details.</p>
<div class="sug" id="pay">${payBox()}</div>
<div class="row"><div><label>Your salary expectation</label><input id="sa" value="${esc(PR.salary||'')}" placeholder="e.g. EUR 45,000 per year"><small>Roles paying at or above this are ranked higher. Lower-paid roles are never hidden: they are marked "below your expectation" for you to review.</small></div><div><label>Start date / notice</label><input id="st" value="${esc(PR.start||'')}" placeholder="e.g. Immediately"></div></div>
<div class="row"><div><label>Work authorisation</label><input id="au" value="${esc(PR.authorisation||'')}" placeholder="e.g. Authorised to work in Spain"></div><div><label>Need visa sponsorship?</label><input id="sp" value="${esc(PR.sponsorship||'')}" placeholder="No"></div></div>
<label>Where to look</label>${Object.entries(S.sources||{}).map(([k,v])=>`<label class="chk"><input type="checkbox" name="sr" value="${k}" ${PR.sources.includes(k)?'checked':''}>${esc(v)}</label>`).join('')}</div>`+nav(true,'finish()','Save and find jobs');loadPay()}
function payBox(){const r=PR.salary_range;if(!r)return '<span class="mut">Looking up typical pay for your target roles...</span>';if(r.error)return `<span class="mut">Could not estimate typical pay: ${esc(r.error)}</span>`;
 const f=n=>Number(n).toLocaleString();return `<b>Typical pay for your target roles: ${esc(r.currency)} ${f(r.low)} – ${f(r.high)} gross per year</b>${r.note?`<div class="mut">${esc(r.note)}</div>`:''}`+
 (r.by_role||[]).map(b=>`<div class="mut">${esc(b.role)}: ${f(b.low)} – ${f(b.high)}</div>`).join('')+'<div class="mut">AI estimate of the market average; check a local salary site before relying on it.</div>'}
async function loadPay(){if(PR.salary_range&&!PR.salary_range.error&&PR._payFor==PR.roles+PR.city+PR.mode)return;PR.salary_range=null;const el=$('#pay');if(el)el.innerHTML=payBox();
 const r=await post('/api/setup/salary',{prefs:PR,profile:P});PR.salary_range=r.error?{error:r.error}:r.range;if(!r.error)PR._payFor=PR.roles+PR.city+PR.mode;saveDraft();const e2=$('#pay');if(e2)e2.innerHTML=payBox()}
function readAnswers(){if(!$('#sa'))return;PR.salary=$('#sa').value;PR.start=$('#st').value;PR.authorisation=$('#au').value;PR.sponsorship=$('#sp').value;PR.sources=[...document.querySelectorAll('[name=sr]:checked')].map(x=>x.value)}
async function finish(){readAnswers();if(PR.salary_range&&PR.salary_range.error)delete PR.salary_range;
 const b=$('#nx');b.disabled=true;err();const r=await post('/api/setup/save',{profile:P,prefs:PR});if(r.error){b.disabled=false;return err(r.error)}
 await post('/api/run',{});$('#view').innerHTML=`<div class="card"><h2>All set</h2><p>Saved. The first search is running now; it takes a few minutes. Opening your board...</p></div>`;setTimeout(()=>location='/',1800)}
(async()=>{S=await (await fetch('/api/setup/state')).json();if(S.prefs&&S.prefs.roles)PR={...PR,...S.prefs};
 const d=S.draft;if(d){if(d.P)P=d.P;if(d.PR)PR={...PR,...d.PR};SG=d.SG||null;CV=d.CV||'';step=Math.min(+d.step||0,P?4:1)}draw()})();
</script></body></html>"""
