#!/usr/bin/env python3
"""Status history: every status change of every role, so the funnel shows the real path (applied -> task -> interview -> rejected),
not only where a role ended up. File: out/status_history.csv (ts,id,old,new,note). Append-only."""
import csv, threading
from datetime import datetime
import paths

FILE = paths.OUT / "status_history.csv"
FIELDS = ["ts", "id", "old", "new", "note"]
RANK = {"applied": 1, "task": 2, "interview": 3, "offer": 4, "onboarding_failed": 4, "project_closed": 4}   # stages of a sent application
SENT = set(RANK) | {"rejected"}
_L = threading.Lock()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read():
    if not FILE.exists():
        return []
    with FILE.open(newline="") as f:
        return list(csv.DictReader(f))


def record(jid, old, new, ts=None, note=""):
    with _L:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        new_file = not FILE.exists()
        with FILE.open("a", newline="") as f:
            w = csv.DictWriter(f, FIELDS)
            if new_file:
                w.writeheader()
            w.writerow({"ts": ts or _now(), "id": jid, "old": old, "new": new, "note": note})


def last_status(hist):
    out = {}
    for h in hist:
        out[h["id"]] = h["new"]
    return out


def sync(rows):
    """Log any status that differs from the last recorded one (changes made by the dashboard, scans or scripts alike).
    First sight of a role: record its current status (backfill) dated by applied_on / found."""
    last = last_status(read())
    n = 0
    for r in rows:
        st = r["status"]
        if last.get(r["id"]) == st:
            continue
        if r["id"] not in last:
            if st in SENT and r.get("applied_on"):
                record(r["id"], "", "applied", ts=r["applied_on"] + " 00:00:00", note="backfill")
                if st != "applied":
                    record(r["id"], "applied", st, note="backfill")
            else:
                record(r["id"], "", st, ts=(r.get("applied_on") or r.get("found") or _now()[:10]) + " 00:00:00", note="backfill")
        else:
            record(r["id"], last[r["id"]], st)
        n += 1
    return n


def stages(hist=None):
    """id -> {'furthest': rank reached, 'seen': set of stage names, 'dates': {stage: first date}}"""
    hist = hist if hist is not None else read()
    out = {}
    for h in hist:
        s = h["new"]
        if s not in RANK:
            continue
        d = out.setdefault(h["id"], {"furthest": 0, "seen": set(), "dates": {}})
        d["furthest"] = max(d["furthest"], RANK[s])
        d["seen"].add(s)
        d["dates"].setdefault(s, h["ts"][:10])
    return out


def note_stage(jid, stage):
    """Used when a role is marked rejected: say how far it got (application / task / interview), so the missing steps are recorded."""
    have = stages().get(jid, {"seen": set()})["seen"]
    need = ["applied"] + ([stage] if stage in ("task", "interview") else [])
    prev = "applied" if "applied" in have else ""
    for s in need:
        if s not in have:
            record(jid, prev, s, note="stage reported when rejected")
        prev = s
