#!/usr/bin/env python3
"""Re-check every tracked job still marked 'new': fetch missing descriptions, drop closed postings, run the full-posting fit check
(>= min_fit % of requirements met, no hard blockers), and resolve the direct apply page so LinkedIn Easy Apply is not needed.
Sets status 'skipped' + a reason (never deletes). daily.py runs this before drafting."""
import csv, json
import ats, fit, salary
import search as S

import paths


def main():
    S.ensure_tracker()
    rows = list(csv.DictReader(S.TRACKER.open()))
    pf = paths.OUT / "pinned.json"
    pinned = set(json.loads(pf.read_text())) if pf.exists() else set()  # roles you chose to keep: never auto-skipped or re-noted
    cache, stats, todo, touched = ats.load_cache(), {"closed": 0, "skipped": 0, "kept": 0, "refetched": 0}, [], set()
    for r in rows:
        if r["status"] != "new" or r["id"] in pinned:
            continue
        f = paths.OUT / "jobs" / f"{r['id']}.json"
        touched.add(r["id"])
        job = json.loads(f.read_text()) if f.exists() else {"desc": "", "title": r["title"]}
        if len(job.get("desc", "")) < 300 and "linkedin.com" in r["url"]:
            page = S.lk_page("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/" + r["url"].rstrip("/").split("/")[-1])
            job["desc"], job["mode"], closed = S.lk_parse(page)
            stats["refetched"] += 1
            if closed:
                r["status"], r["notes"] = "skipped", "closed posting"
                stats["closed"] += 1
                continue
            f.write_text(json.dumps({**job, **{k: r[k] for k in ("id", "title", "company", "location", "url")}}, ensure_ascii=False))
        a = fit.assess(r["title"], job.get("desc", ""), location=r["location"])
        if not S.location_ok({"location": r["location"], "title": r["title"]}) and a["verdict"] == "ok":
            a = {**a, "verdict": "blocked", "fit": 0, "blockers": [f"location not {S.CFG.get('home_city') or 'an accepted location'} or remote: {r['location']}"]}
        r["fit"], r["notes"] = a["fit"], fit.note(a)
        if not r.get("salary"):
            r["salary"], r["salary_basis"] = salary.salary_for(r["title"], r["company"], r["location"], job.get("desc", ""))
        if a["verdict"] != "ok":
            r["status"] = "skipped"
            stats["skipped"] += 1
        else:
            stats["kept"] += 1
            if not r.get("apply_url"):
                todo.append(r)
    if todo:
        S.resolve_apply(todo, cache)
    ats.save_cache(cache)
    changed = {r["id"]: r for r in rows if r["id"] in touched}
    with S.tracker_lock():  # merge into the CURRENT file: never overwrite status edits made while this ran
        with S.TRACKER.open(newline="") as fh:
            disk = list(csv.DictReader(fh))
        for d in disk:
            c = changed.get(d["id"])
            if c and d["status"] == "new":
                d.update({k: c[k] for k in ("status", "notes", "fit", "salary", "salary_basis", "apply_url", "apply_via") if k in c})
        tmp = S.TRACKER.with_suffix(".tmp")
        with tmp.open("w", newline="") as fh:
            w = csv.DictWriter(fh, S.FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(disk)
        tmp.replace(S.TRACKER)
    print("validate:", stats)


if __name__ == "__main__":
    main()
