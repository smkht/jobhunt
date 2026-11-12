"""Update one tracker row: python3 mark_status.py <id> <status> "<note>"  (sets applied_on when status=applied)."""
import csv, os, sys
from datetime import date
import search, history


def main(jid, status, note=""):
    with search.tracker_lock():
        rd = csv.DictReader(open(search.TRACKER, newline=""))
        cols, rows = rd.fieldnames, list(rd)
        hit = False
        for r in rows:
            if r["id"] == jid:
                hit = True
                r["status"] = status
                if note:
                    r["notes"] = (r["notes"] + " | " + note).strip(" |")
                if status == "applied" and not r.get("applied_on"):
                    r["applied_on"] = date.today().isoformat()
        if not hit:
            sys.exit(f"no row {jid}")
        tmp = search.TRACKER.with_suffix(".tmp")
        w = csv.DictWriter(open(tmp, "w", newline=""), fieldnames=cols)
        w.writeheader(); w.writerows(rows)
        os.replace(tmp, search.TRACKER); history.sync(rows)
    print(jid, status)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
