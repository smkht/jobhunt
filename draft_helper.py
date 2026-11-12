"""Hand-draft helper (for scripting or an assistant): from draft_helper import make.
make(job_id, tailored_dict, note) writes CV + letter into DATA/out/applications and marks the row drafted.
`tailored_dict` has the same shape as the AI reply in tailor.py: headline, summary, skills, jd_keywords, jobs [{i, bullets}], cover_letter, why_us, gaps, fit_pitch."""
import json, csv, os
import paths, search, history, tailor, rebuild_cvs


def b(i, *k):
    """Pick bullets k of profile job i."""
    return [tailor.P["jobs"][i]["bullets"][n] for n in k]


def make(jid, t, note):
    tailor.refresh()
    job = json.load(open(paths.OUT / "jobs" / f"{jid}.json")); job.setdefault("id", jid)
    d = tailor.app_dir(job); d.mkdir(parents=True, exist_ok=True)
    pre = paths.file_prefix()
    (d / "CV.html").write_text(tailor.render(t)); tailor.to_pdf(d / "CV.html", d / f"{pre}_CV.pdf")
    (d / "cover_letter.md").write_text(t["cover_letter"])
    (d / "cover_letter.html").write_text("<!doctype html><meta charset=utf-8><style>body{font:11pt/1.5 Calibri,Arial,sans-serif;max-width:170mm;margin:25mm auto;color:#222}</style>" + "".join(f"<p>{tailor.e(p)}</p>" for p in t["cover_letter"].split("\n\n")))
    tailor.to_pdf(d / "cover_letter.html", d / f"{pre}_Cover_Letter.pdf")
    (d / "draft.json").write_text(json.dumps(t, ensure_ascii=False, indent=1)); tailor.write_notes(d, job, t)
    rebuild_cvs.run(d.name)  # fuller layout: 2 pages, page 2 at least ~65% full
    with search.tracker_lock():
        rd = csv.DictReader(open(search.TRACKER, newline="")); cols = rd.fieldnames; rows = list(rd)
        for r in rows:
            if r["id"] == jid:
                r["status"] = "drafted"; r["notes"] = (r["notes"] + " | " + note).strip(" |")
        tmp = search.TRACKER.with_suffix(".tmp")
        w = csv.DictWriter(open(tmp, "w", newline=""), fieldnames=cols); w.writeheader(); w.writerows(rows)
        os.replace(tmp, search.TRACKER); history.sync(rows)
    print(jid, d.name, len(t["cover_letter"].split()))
