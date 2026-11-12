#!/usr/bin/env python3
"""Rebuild drafted CVs in a fuller layout: tailored bullets first, then more bullets from the profile itself (ranked by relevance to the posting),
tuned so the CV is 2 pages with page 2 at least ~65% full. Usage: rebuild_cvs.py <app_dir_name> [...]  |  --all-drafted
Uses only facts in DATA/profile.json (see PROFILE_SCHEMA.md): jobs with section "research" appear under Education, "projects" under Projects,
"extra" as a last role; everything else is normal experience in the order listed."""
import csv, html, json, re, subprocess, sys, tempfile
from pathlib import Path
import paths, tailor

P = tailor.P
e = html.escape

CSS = """
@page{size:A4;margin:12mm 15mm}*{box-sizing:border-box}html{-webkit-print-color-adjust:exact}
body{margin:0;font:10pt/1.36 Calibri,Arial,Helvetica,sans-serif;color:#111}
h1{margin:0;font-size:22pt;letter-spacing:.3px}.contact{margin:3px 0 7px;color:#333;font-size:9.6pt}
.hl{font-size:11.5pt;font-weight:bold;margin:0 0 3px}
h2{break-after:avoid;font-size:10.5pt;text-transform:uppercase;letter-spacing:.6px;border-bottom:1.3px solid #111;margin:10px 0 5px;padding-bottom:1px}
p{margin:0 0 3px}.job{margin-bottom:8px;break-inside:avoid}.jh{break-after:avoid}.hi{margin:1px 0 2px}.jt{font-weight:bold;font-size:10.3pt}.jm{color:#333;margin-bottom:1px}
div.ln{margin:0 0 2px}ul{margin:2px 0 0;padding-left:16px}li{margin:1.6px 0}.sk b{font-weight:bold}li{break-inside:avoid}"""




def indexes():
    """Job indexes by section: (experience, extra, projects, research)."""
    tailor.refresh()
    ex, extra, proj, res = [], [], [], []
    for i, j in enumerate(P["jobs"]):
        sec = j.get("section", "")
        {"extra": extra, "projects": proj, "research": res}.get(sec, ex).append(i)
    return ex, extra, proj, res


def min_bullets(rank):
    return 3 if rank == 0 else 2 if rank < 3 else 1


def clean_b(b):
    return re.sub(r"\s*\((?:stated|confirmed|per|see)[^)]*\)", "", b).strip()


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def similar(a, b):
    wa, wb = set(norm(a).split()), set(norm(b).split())
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) > 0.72


def words(t):
    txt = " ".join(t.get("jd_keywords", []) + t.get("skills", []) + [t.get("headline", ""), t.get("summary", "")])
    return {w for w in norm(txt).split() if len(w) > 3}


def pool(t):
    """Per job: tailored bullets (kept first, in order) then further profile bullets ranked by relevance to the posting."""
    ex, extra, proj, res = indexes()
    kw = words(t)
    out = {}
    base = {j["i"]: [clean_b(b) for b in j["bullets"]] for j in t["jobs"]}
    for i in ex + extra:
        have = list(base.get(i, []))
        extras = []
        for b in [clean_b(x) for x in P["jobs"][i].get("bullets", [])]:
            if not any(similar(b, h) for h in have + extras):
                extras.append(b)
        extras.sort(key=lambda b: -len(kw & set(norm(b).split())))
        out[i] = (have, extras)
    return out


def render(t, sel):
    """sel: {job index: [bullets]}."""
    ex, extra, proj, res = indexes()
    block = lambda j, bl: (f'<div class="job"><div class="jh"><div class="jt">{e(j["title"])}</div><div class="jm">{e(j["org"])} | {e(j["dates"])}</div></div><ul>'
                           + "".join(f"<li>{e(b)}</li>" for b in bl) + "</ul></div>")
    exp = "".join(block(P["jobs"][i], sel[i]) for i in ex if sel.get(i))
    more = "".join(block(P["jobs"][i], sel[i]) for i in extra if sel.get(i))
    techh = ""
    pb = [b for i in proj for b in (sel.get(i) or [])]
    if pb:
        techh = "<h2>Projects</h2><ul>" + "".join(f"<li>{e(b)}</li>" for b in pb) + "</ul>"
    skills = " · ".join(t.get("skills") or [])
    edu = "".join(f'<div class="ln"><b>{e(a)}</b>, {e(b)}</div>' for a, b in P["education"])
    txt = " ".join(t.get("jd_keywords", []) + t.get("skills", []) + [t.get("headline", "")]).lower()
    research = any(k in txt for k in ("research", "legal", "compliance", "policy", "editor", "analyst", "paralegal", "publish", "content", "writing", "evaluat", "academic", "science"))
    resh = ""
    for i in res:
        j = P["jobs"][i]
        resh += f'<div class=jm style=margin-top:2px>{e(j["title"])}, {e(j["org"])} ({e(j["dates"])}):</div>'
        if research and j.get("bullets"):
            resh += "<ul>" + "".join(f"<li>{e(b)}</li>" for b in j["bullets"][:3]) + "</ul>"
    stats = [" ".join(map(str, x)) if isinstance(x, (list, tuple)) else str(x) for x in P.get("stats", [])]
    hi = f'<p class="hi"><b>Highlights:</b> {e(" · ".join(stats))}</p>' if stats else ""

    return f"""<!doctype html><meta charset=utf-8><title>{e(P['name'])} - CV</title><style>{CSS}</style><div id="cv">
<h1>{e(P['name'])}</h1><div class="contact">{e(' | '.join(P['contact']))}</div>
<div class="hl">{e(t['headline'])}</div>
<h2>Professional Summary</h2><p>{e(t['summary'])}</p>
{hi}<h2>Core Skills</h2><p>{e(skills)}</p>
<h2>Professional Experience</h2>{exp}{more}{techh}
<h2>Education</h2>{edu}{resh}
<h2>Certifications and Languages</h2><p>{e(' | '.join(P['credentials']))}</p><p>Languages: {e(' | '.join(P['languages']))}</p></div>"""


def browser():
    for b in tailor.BROWSERS[1:] + tailor.BROWSERS[:1]:
        if Path(b).exists():
            return b


def measure(h):
    """Content height of the CV in A4 print units (fraction of one A4 page body); >1 means it spills to page 2."""
    h2 = h.replace("<style>", "<style>html,body{width:180mm}#cv{width:180mm}</style><style>", 1) + \
        "<script>addEventListener('load',()=>{document.documentElement.setAttribute('data-h',document.getElementById('cv').getBoundingClientRect().height)})</script>"
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(h2)
    r = subprocess.run([browser(), "--headless=new", "--disable-gpu", "--no-sandbox", "--window-size=900,2400", "--virtual-time-budget=4000", "--dump-dom", "file://" + f.name],
                       capture_output=True, text=True, timeout=60)
    m = re.search(r'data-h="([\d.]+)"', r.stdout)
    Path(f.name).unlink(missing_ok=True)
    return float(m.group(1)) / (273 / 25.4 * 96) if m else None


def build(t):
    ex, extra, proj, res = indexes()
    pl = pool(t)
    sel = {i: list(pl[i][0]) for i in pl}
    # minimum sensible detail per job (most recent jobs get more)
    for rank, i in enumerate(ex):
        while len(sel[i]) < min_bullets(rank) and pl[i][1]:
            sel[i].append(pl[i][1].pop(0))
    # self-directed projects only for roles that mention tools / AI / software
    if proj and any(k in " ".join(t.get("jd_keywords", []) + t.get("skills", [])).lower() for k in ("automation", " ai", "ai-", "software", "data", "digital transformation", "technical")):
        for i in proj:
            sel[i] = P["jobs"][i].get("bullets", [])[:2]
    fill = measure(render(t, sel))
    # grow by relevance until page 2 holds at least ~65% (total 1.65 pages), never beyond 1.95
    pool_order = [(i, b) for i in ex + extra for b in pl[i][1]]
    kw = words(t)
    pool_order.sort(key=lambda x: -len(kw & set(norm(x[1]).split())))
    k = 0
    added = []
    while fill is not None and fill < 1.8 and k < len(pool_order):
        i, b = pool_order[k]
        k += 1
        sel[i].append(b)
        f2 = measure(render(t, sel))
        if f2 is None or f2 > 1.95:
            sel[i].pop()
            continue
        fill = f2
        added.append((i, b))
    # real PDF check: page breaks can push it to 3 pages; drop the last-added extras until it is at most 2 pages
    tmp = Path(tempfile.mkdtemp())
    while True:
        h = render(t, sel)
        (tmp / "c.html").write_text(h)
        tailor.to_pdf(tmp / "c.html", tmp / "c.pdf")
        if app_pdf_pages(tmp / "c.pdf") <= 2 or not added:
            break
        i, b = added.pop()
        sel[i].remove(b)
        fill = measure(render(t, sel))
    return render(t, sel), fill, sel


def app_pdf_pages(p):
    return len(re.findall(rb"/Type\s*/Page[^s]", p.read_bytes())) if Path(p).exists() else 0


def run(name):
    tailor.refresh()
    d = paths.OUT / "applications" / name
    t = json.loads((d / "draft.json").read_text())
    if not browser():
        print(f"{name}: no Chromium-based browser found; kept the plain CV (open CV.html and print to PDF)")
        return 0, None
    h, fill, sel = build(t)
    (d / "CV.html").write_text(h)
    pdf = d / f"{paths.file_prefix()}_CV.pdf"
    tailor.to_pdf(d / "CV.html", pdf)
    pages = app_pdf_pages(pdf)
    n = sum(len(v) for v in sel.values())
    print(f"{name}: bullets={n} fill={fill and round(fill,2)} pages={pages}")
    if pages != 2 or fill is None or fill < 1.5 or fill > 1.95:
        print(f"  WARNING {name}: CV should be 2 pages with page 2 at least half full (fill 1.5-1.95); check before applying")
    return pages, fill


if __name__ == "__main__":
    names = sys.argv[1:]
    if names == ["--all-drafted"]:
        import search
        search.ensure_tracker()
        rows = [r for r in csv.DictReader(open(search.TRACKER)) if r["status"] == "drafted"]
        names = []
        for r in rows:
            jf = paths.OUT / "jobs" / f"{r['id']}.json"
            job = json.loads(jf.read_text()) if jf.exists() else r
            names.append(tailor.app_dir(job).name)
    for nm in names:
        run(nm)
