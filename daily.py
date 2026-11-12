#!/usr/bin/env python3
"""Run the job-search lanes.
  python3 daily.py                 fast lane: <=2-day postings, then draft top matches
  python3 daily.py --background    weekly broad discovery; never auto-drafts
  python3 daily.py --tailor-only   skip the fast-lane search and draft existing new matches"""
import csv, json, subprocess, sys
import paths
import search, tailor, validate, kit

TRACKER = search.TRACKER


def notify(msg):
    try:
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Job Hunt"'], timeout=10)
    except Exception:
        pass


def main():
    background = "--background" in sys.argv
    search.ensure_tracker()
    before = {r["id"] for r in csv.DictReader(TRACKER.open())}
    if "--tailor-only" not in sys.argv:
        # search.py defaults to the fast lane; background is intentionally a
        # review-only lane so older/broader discoveries never auto-fill drafts.
        if background:
            import os
            os.environ["SEARCH_MODE"] = "background"
        search.main_locked()
    validate.main()
    if background:
        fresh = len({r["id"] for r in csv.DictReader(TRACKER.open())} - before)
        msg = f"{fresh} background matches ready for review (no CVs auto-drafted)"
        print(msg)
        notify(msg)
        return
    n = paths.load_config()["tailor_top_n"]
    rows = list(csv.DictReader(TRACKER.open()))
    new = [r for r in rows if r["status"] == "new"]
    new.sort(key=lambda r: -int(r["score"]))
    drafted, err = 0, ""
    for r in new[:n]:
        try:
            tailor.tailor(json.loads((paths.OUT / "jobs" / f"{r['id']}.json").read_text()))
            r["status"] = "drafted"
            drafted += 1
        except Exception as e:
            err = str(e)
            print("tailor failed for", r["id"], e, file=sys.stderr)
            if "No AI backend" in err or "claude CLI failed" in err:
                break  # same failure for every job; stop early
    # re-read so edits made in the dashboard meanwhile are not lost
    with search.tracker_lock():
        cur = {x["id"]: x for x in csv.DictReader(TRACKER.open())}
        for r in new:
            if r["status"] == "drafted" and r["id"] in cur and cur[r["id"]]["status"] == "new":
                cur[r["id"]]["status"] = "drafted"
        tmp = TRACKER.with_suffix(".tmp")
        with tmp.open("w", newline="") as f:
            w = csv.DictWriter(f, search.FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(cur.values())
        tmp.replace(TRACKER)
    fresh = len(set(cur) - before)
    try:                                    # who-to-look-for lists for new good-fit roles (no messages are drafted)
        import contacts
        have = set(contacts.load())
        contacts.ensure(list(cur.values()))
        people = len(set(contacts.load()) - have)
    except Exception as e:                  # never let this break the daily run
        people = 0
    msg = f"{fresh} new matches, {drafted} CVs drafted" + (f", {people} contact lists" if people else "") + (f" (drafting problem: {err[:60]})" if err else "")
    print(msg)
    notify(msg.replace('"', "'"))


if __name__ == "__main__":
    main()
