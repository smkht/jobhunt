#!/usr/bin/env python3
"""Tailor CV + cover letter to a job.
Usage: tailor.py <job id from tracker>      (job saved by search.py)
       tailor.py --text job.txt "Title" "Company"   (paste any posting into a text file)
Output: out/applications/<company>-<title>/{CV.pdf, CV.html, cover_letter.md, notes.md}
Uses the `claude` CLI (your existing login), so no API key is needed."""
import csv, html, json, os, re, shutil, subprocess, sys, urllib.request
from pathlib import Path
import paths

ROOT = paths.ROOT
DATA = paths.DATA
P = {}


def refresh():
    """(Re)load DATA/profile.json into the shared dict P. Called automatically by the entry points, so edits take effect without a restart."""
    P.clear()
    P.update(paths.load_profile())
    P.setdefault("name", "Candidate")
    for k in ("contact", "education", "credentials", "languages", "skills", "jobs"):
        P.setdefault(k, [])
    P.setdefault("headline_default", "")
    return P


refresh()


def section(i):
    """Profile jobs may carry "section": "research" (shown under Education), "projects" (technical/side projects) or "extra" (earlier / other roles)."""
    return P["jobs"][i].get("section", "")

PROMPT = """You tailor a CV and cover letter for {name} for one specific job. Candidate profile (the ONLY source of facts):
{profile}

JOB: {title} at {company}
{desc}

Rules:
- Positioning: {notes}
- Use ONLY facts, numbers and employers from the profile. Never invent skills, tools, titles, degrees or metrics. If the job needs something the candidate lacks, do not claim it; list it under "gaps".
- ATS: applicant-tracking systems match the posting's own words. Mirror the posting's exact phrases (duties, tools, qualifications) in the headline, summary, skills and bullets wherever they are TRUE for the candidate; spell out acronyms once; use standard words, no clever synonyms. The headline should be the job's own title or the closest truthful variant.
- Reframe the candidate's background as an advantage for THIS role, in the posting's vocabulary.
- Bullets come from the profile bullets: reorder, trim, reword to use the posting's phrasing without changing facts. 4-6 bullets for the best-matching jobs, 3-4 for the other recent ones, 1-2 for the oldest. 6-8 jobs, always keep the most recent two. Use the page fully (about 750-900 words over up to TWO A4 pages, including the automatic earlier-experience line): a human reads this too, so show proof (numbers, scope, results), not only keywords. The jobs you leave out are listed automatically in a short 'Earlier Experience' section, so do not squeeze them in. The CV is one or two A4 pages: page 1 must carry the headline, summary, core skills and the best-matching roles; never leave a heading alone at the bottom of page 1.
- summary: 3 sentences, no clichés, no first-person pronouns, start with the candidate's years of experience (from the profile) and the role family, then 2 proof points with numbers, and use the posting's top requirements.
- skills: 10-14 short keyword phrases (the posting's exact terms first) that are true for the candidate, for a Core Skills section.
- jd_keywords: the 12-20 most important requirement phrases from the posting (as written), used for a coverage check.
- cover_letter: SHORT and warm, 90-130 words, in this shape. Greeting "Hi <Company> team," (or the hiring manager's name if the posting gives one). Line 1: a genuine, cheerful hook that names something specific from the posting or the company (product, mission, way of working) and says the candidate is genuinely interested in it. Line 2-3: "I'd love to bring ..." followed by 2 concrete things the candidate has done that match the posting's main duties, each with one number or fact from the profile, in the posting's own words. Optional last clause: one thing the candidate is keen to learn, phrased positively (never an apology, never a list of gaps). Close with a friendly, specific invitation ("Would love to chat about ..."). Sign with the candidate's first name ({first}). Tone: friendly, enthusiastic, human, confident; contractions are fine; no corporate filler ("I am writing to apply", "I believe I would be a strong fit"), no flattery, no more than one exclamation mark. VARY THE OPENER every time: do NOT start with "I'm excited" or "Being ... is work I'm excited about"; rotate between a direct observation about the product, a short question, a one-line story from the candidate's own work, a plain statement of why this role caught the candidate's eye, or a specific detail from the posting; vary sentence length and structure, mix a short sentence in, write like a real person typing a quick note (a casual aside or dash is fine), and avoid triplets and polished-sounding lists. First person. Facts only from the profile.{style}
- why_us: keep it short and upbeat (50-80 words), same voice.
- other_experience (in the profile, may be empty): side skills, hobbies, own or family projects, volunteering. Use an item ONLY when this posting asks for that skill or setting (e.g. cooking or household management for a personal-assistant role, a coding hobby for a tooling role). Then you may add it to skills, mention it in the letter, and put 0-3 short lines under "extra" for an Additional Experience section. Never present it as a job, never inflate it, and leave "extra" empty when nothing in it fits the posting.
Return ONLY JSON: {{"headline": str, "summary": str, "skills": [str], "jd_keywords": [str], "jobs": [{{"i": <index of job in profile>, "bullets": [str]}}...], "cover_letter": str, "why_us": str, "gaps": [str], "extra": [str], "fit_pitch": str (one line)}}"""

CSS = """
@page{size:A4;margin:12mm 15mm}*{box-sizing:border-box}body{margin:0;font:10pt/1.32 Calibri,Arial,Helvetica,sans-serif;color:#111}
h1{margin:0;font-size:21pt}.contact{margin:2px 0 6px;color:#333}.hl{font-size:11.5pt;font-weight:bold;margin:2px 0 4px}
h2{break-after:avoid;font-size:10.5pt;text-transform:uppercase;letter-spacing:.5px;border-bottom:1.2px solid #111;margin:8px 0 4px;padding-bottom:1px}
p{margin:0 0 3px}.job{margin-bottom:7px;break-inside:avoid}.jt{font-weight:bold}.jm{color:#333}
div.ln{margin:0 0 1px}ul{margin:2px 0 0;padding-left:16px}li{margin:1.5px 0}"""


def e(s):
    return html.escape(s)


def render(t):
    """Single-column, plain-text-friendly layout: standard headings, real bullets, no tables/columns/graphics (parses cleanly in ATS)."""
    refresh()
    jobs = {j["i"]: j["bullets"] for j in t["jobs"]}
    exp = "".join(
        f'<div class="job"><div class="jt">{e(P["jobs"][i]["title"])}</div><div class="jm">{e(P["jobs"][i]["org"])} | {e(P["jobs"][i]["dates"])}</div><ul>'
        + "".join(f"<li>{e(b)}</li>" for b in jobs[i]) + "</ul></div>" for i in sorted(jobs))
    skills = ", ".join(t.get("skills") or [])
    rest = [i for i in range(len(P["jobs"])) if i not in jobs and section(i) not in ("projects", "research")]
    short = lambda s: re.sub(r"\s*\(.*?\)", "", s)
    more = ("<h2>Earlier Experience</h2>"
            + "".join(f'<div class="ln"><b>{e(short(P["jobs"][i]["title"]))}</b>, {e(P["jobs"][i]["org"].split(",")[0])} | {e(P["jobs"][i]["dates"])}</div>' for i in rest)) if rest else ""
    research = "".join(f'<div class="ln"><b>{e(short(P["jobs"][i]["title"]))}</b>, {e(P["jobs"][i]["org"])} | {e(P["jobs"][i]["dates"])}</div>'
                       for i in range(len(P["jobs"])) if section(i) == "research")
    return f"""<!doctype html><meta charset=utf-8><title>{P['name']} - CV</title><style>{CSS}</style>
<h1>{P['name']}</h1><div class="contact">{e(' | '.join(x for x in P['contact'] if x))}</div>
<div class="hl">{e(t['headline'])}</div>
<h2>Professional Summary</h2><p>{e(t['summary'])}</p>
<h2>Core Skills</h2><p>{e(skills)}</p>
<h2>Professional Experience</h2>{exp}{more}
{('<h2>Additional Experience</h2><ul>' + ''.join(f'<li>{e(x)}</li>' for x in t.get('extra', [])) + '</ul>') if t.get('extra') else ''}
<h2>Education</h2>{''.join(f'<div class="ln"><b>{e(a)}</b>, {e(b)}</div>' for a, b in P['education'])}{research}
<h2>Certifications and Languages</h2><p>{e(' | '.join(P['credentials']))}</p><p>Languages: {e(' | '.join(P['languages']))}</p>"""


def ats_report(t, job):
    """Which of the posting's key phrases appear in the CV + letter (case-insensitive)."""
    cv = (render(t) + " " + t["cover_letter"]).lower()
    cv = re.sub(r"<[^>]+>", " ", html.unescape(cv))
    hit = [k for k in t.get("jd_keywords", []) if k.lower() in cv]
    miss = [k for k in t.get("jd_keywords", []) if k.lower() not in cv]
    return hit, miss


BROWSERS = ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
EXTRA_PATH = "/opt/homebrew/bin:/usr/local/bin:" + str(Path.home() / ".local/bin") + ":" + str(Path.home() / ".claude/local")


def backend():
    if os.environ.get("TAILOR_MOCK"):
        return "mock"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if shutil.which("claude", path=os.environ.get("PATH", "") + ":" + EXTRA_PATH):
        return "cli"
    raise RuntimeError("No AI backend: install the `claude` CLI and log in, or set ANTHROPIC_API_KEY.")


def ask_llm(prompt):
    if backend() == "mock":
        return mock(prompt)
    return ask_llm_raw(prompt)


def ask_llm_raw(prompt):
    be = backend()
    if be == "api":
        body = json.dumps({"model": os.environ.get("TAILOR_MODEL", "claude-sonnet-5-5"), "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", body, {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
        out = json.loads(urllib.request.urlopen(req, timeout=180).read())["content"][0]["text"]
    else:
        env = {**os.environ, "PATH": os.environ.get("PATH", "") + ":" + EXTRA_PATH}
        r = subprocess.run(["claude", "-p", "--model", "sonnet"], input=prompt, capture_output=True, text=True, timeout=300, env=env)
        out = r.stdout
        if "Failed to authenticate" in out or r.returncode:
            raise RuntimeError("claude CLI failed (are you logged in? run `claude` once in Terminal): " + (out or r.stderr)[:200])
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        raise RuntimeError("no JSON in model reply: " + out[:200])
    return json.loads(m.group(0))


def mock(prompt):
    """Offline stand-in (TAILOR_MOCK=1) so the pipeline, PDF and dashboard can be tested without an AI."""
    return {"headline": P.get("headline_default", ""), "summary": "MOCK DRAFT - no AI was used. Replace by running with a real backend.",
            "jobs": [{"i": i, "bullets": j["bullets"][:3]} for i, j in enumerate(P["jobs"][:5])], "skills": ["(mock)"], "jd_keywords": [],
            "cover_letter": "MOCK cover letter.\n\n" + (P.get("name") or "Candidate").split()[0], "why_us": "MOCK why-us answer.", "gaps": ["(mock) nothing analysed"], "fit_pitch": "(mock)"}


def clean(t):
    n = len(P["jobs"])
    t["jobs"] = [{"i": int(j["i"]), "bullets": [str(b) for b in j["bullets"]]} for j in t["jobs"] if 0 <= int(j["i"]) < n and j["bullets"]]
    t["skills"] = [str(x) for x in t.get("skills", [])][:16]
    t["jd_keywords"] = [str(x) for x in t.get("jd_keywords", [])]
    for k in ("headline", "summary", "cover_letter", "fit_pitch", "why_us"):
        t[k] = str(t.get(k, ""))
    t["gaps"] = [str(g) for g in t.get("gaps", [])]
    t["extra"] = [str(x) for x in t.get("extra", []) if str(x).strip()][:3]
    if not t["jobs"] or not t["summary"]:
        raise RuntimeError("model reply missing jobs/summary")
    return t


def load_job(argv):
    if argv[0] == "--text":
        return dict(title=argv[2], company=argv[3], desc=Path(argv[1]).read_text(), url="", id="manual")
    return json.loads((paths.OUT / "jobs" / f"{argv[0]}.json").read_text())


def app_dir(job):
    slug = re.sub(r"[^a-z0-9]+", "-", f"{job['company']}-{job['title']}".lower()).strip("-")[:60]
    return paths.OUT / "applications" / slug


def to_pdf(html_path, pdf_path):
    b = next((x for x in BROWSERS if Path(x).exists()), None)
    if not b:
        print("  (no Chromium-based browser found; HTML saved, open it and print to PDF)")
        return False
    subprocess.run([b, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf_path.resolve()}", html_path.resolve().as_uri()], capture_output=True, timeout=90)
    return pdf_path.exists()


def write_notes(d, job, t):
    hit, miss = ats_report(t, job)
    (d / "notes.md").write_text(f"# {job['title']} — {job['company']}\n{job.get('url','')}\n\n**Pitch:** {t['fit_pitch']}\n\n"
                                f"**ATS keyword coverage:** {len(hit)}/{len(hit) + len(miss)}\n- Covered: {', '.join(hit) or '-'}\n- Not covered (not claimed, or not true for you): {', '.join(miss) or '-'}\n\n"
                                "**Gaps (don't claim these):**\n" + "\n".join(f"- {g}" for g in t["gaps"]) + "\n")


def tailor(job):
    refresh()
    if not P.get("jobs"):
        raise RuntimeError("No profile yet: finish the onboarding (data/profile.json is empty).")
    cfg = paths.load_config()
    style = (" Style notes from the candidate: " + cfg["cover_letter_style"]) if cfg.get("cover_letter_style") else ""
    prompt = PROMPT.format(name=P["name"], first=paths.first_name() or P["name"], style=style, profile=json.dumps({k: v for k, v in P.items() if k != "notes_for_tailoring"}, ensure_ascii=False),
                           notes=P.get("notes_for_tailoring", ""), title=job["title"], company=job["company"], desc=job["desc"][:6000])
    try:
        t = clean(ask_llm(prompt))
    except (ValueError, KeyError, RuntimeError) as err:
        if "claude CLI failed" in str(err) or "No AI backend" in str(err):
            raise
        t = clean(ask_llm(prompt))  # one retry on malformed output
    d = app_dir(job)
    d.mkdir(parents=True, exist_ok=True)
    (d / "CV.html").write_text(render(t))
    to_pdf(d / "CV.html", d / f"{paths.file_prefix()}_CV.pdf")
    (d / "cover_letter.md").write_text(t["cover_letter"])
    (d / "cover_letter.html").write_text(f"<!doctype html><meta charset=utf-8><style>body{{font:11pt/1.5 Calibri,Arial,sans-serif;max-width:170mm;margin:25mm auto;color:#222}}</style>"
                                         + "".join(f"<p>{e(p)}</p>" for p in t["cover_letter"].split("\n\n")))
    to_pdf(d / "cover_letter.html", d / f"{paths.file_prefix()}_Cover_Letter.pdf")
    (d / "draft.json").write_text(json.dumps(t, ensure_ascii=False, indent=1))  # source of truth; lets you re-render after hand edits
    write_notes(d, job, t)
    print("OK", d)
    return d


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    tailor(load_job(sys.argv[1:]))
