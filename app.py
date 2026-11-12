#!/usr/bin/env python3
"""Job Hunt dashboard. Local: python3 app.py -> http://127.0.0.1:8765 (no login on localhost).
Runs on this computer only (127.0.0.1). If you ever expose it through a tunnel, set APP_PASSWORD and ALLOWED_HOSTS in data/.env.
Standard library only."""
import history, csv, hashlib, hmac, html, json, os, re, secrets, subprocess, sys, threading, time, urllib.request, webbrowser
from datetime import datetime, date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import paths  # noqa: E402
DATA = paths.DATA

def load_env():
    p = DATA / ".env"
    env = {}
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("\"'")
    return env


for _k, _v in load_env().items():
    os.environ.setdefault(_k, _v)
import contacts, kit, search, onboarding  # noqa: E402

TRACKER, OUT = DATA / "tracker.csv", DATA / "out"
FIELDS = search.FIELDS
STATUSES = ["new", "drafted", "applied", "task", "interview", "offer", "onboarding_failed", "project_closed", "rejected", "skipped"]
EDITABLE = ["config.json", "profile.json", "answers.json", "stories.json", "network.json", "outreach_templates.json"]
LOCK = threading.Lock()
PROCS = {}  # name -> dict(proc, log)
FAILS = {}  # ip -> [count, locked_until]



ENV = load_env()
PASSWORD = ENV.get("APP_PASSWORD", "")
ALLOWED_HOSTS = [h.strip().lower() for h in ENV.get("ALLOWED_HOSTS", "").split(",") if h.strip()]


def secret():
    s = ENV.get("APP_SECRET")
    if s:
        return s.encode()
    f = DATA / "out" / ".session_secret"
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        f.chmod(0o600)
    return f.read_text().strip().encode()


def make_cookie(days=30):
    exp = str(int(time.time()) + days * 86400)
    return exp + "." + hmac.new(secret(), exp.encode(), hashlib.sha256).hexdigest()


def cookie_ok(val):
    try:
        exp, sig = val.split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, hmac.new(secret(), exp.encode(), hashlib.sha256).hexdigest())
    except Exception:
        return False


# ---------------- tracker
def read_tracker():
    return kit.read_tracker()


def write_tracker(rows):
    tmp = TRACKER.with_suffix(".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(TRACKER)


def update_row(jid, **kv):
    with LOCK, search.tracker_lock():
        rows = read_tracker()
        for r in rows:
            if r["id"] == jid:
                r.update(kv)
        write_tracker(rows)
        history.sync(rows)


def set_status(jid, status, stage=None):
    kv = {"status": status}
    row = next((r for r in read_tracker() if r["id"] == jid), None)
    if status == "applied" and row and not row.get("applied_on"):
        kv["applied_on"] = date.today().isoformat()
    if status == "rejected" and row:
        history.sync(read_tracker())              # make sure the path so far is on record before the rejection
        history.note_stage(jid, stage or "application")
    update_row(jid, **kv)



FAMILIES = [("Legal / compliance", r"legal|counsel|compliance|policy|attorney|contract"), ("Product", r"product"),
            ("Partnerships / BD / sales", r"partnership|business development|account|sales|growth|alliance|customer success"),
            ("Procurement / supply / vendor", r"procurement|supply|vendor|logistics|sourcing|purchas"),
            ("Strategy / chief of staff", r"strategy|strategic|chief of staff"), ("Project / programme / delivery", r"project|programme|program manager|pmo|delivery"),
            ("Analyst / data", r"analyst|analytics|data|research"), ("Operations", r"operations|ops\b|operational")]


def family(r):
    if (r.get("source") or "") == "alignerr":
        return "Gig / AI training"
    t = (r.get("title") or "").lower()
    for name, rx in FAMILIES:
        if re.search(rx, t):
            return name
    return "Other"


def funnel_payload(ghost_days=14, exclude=()):
    """Funnel from the status history: every sent role counted by the furthest stage it reached and how it ended.
    'Ghosted' = applied, nothing since, older than ghost_days (computed, not stored)."""
    rows = read_tracker()
    with LOCK, search.tracker_lock():
        history.sync(rows)
    st = history.stages()
    today = date.today()

    def age(r):
        try:
            return (today - datetime.strptime(r["applied_on"], "%Y-%m-%d").date()).days
        except Exception:
            return None
    sent = [r for r in rows if r["status"] in history.SENT and (r["source"] or "other") not in exclude]
    groups = {k: [] for k in ("offer", "onboarding_failed", "project_closed", "interview", "task", "rejected", "ghosted", "waiting")}
    F = dict.fromkeys(("rej_app", "waiting", "ghosted", "task", "rej_task", "task_act", "interview", "rej_int", "int_act", "offer", "offer_act", "offer_failed", "offer_closed", "sent_int"), 0)
    for r in sent:
        info = st.get(r["id"], {"furthest": 1, "seen": {"applied"}})
        f, seen, cur = max(info["furthest"], 1), info["seen"], r["status"]
        item = {"s": r["source"] or "other", "t": r["title"][:60], "c": r["company"][:30], "d": r.get("applied_on") or "", "age": age(r), "id": r["id"],
                "path": " > ".join(x for x in ("applied", "task", "interview", "offer") if x in seen) + {"rejected": " > rejected", "onboarding_failed": " > failed onboarding", "project_closed": " > project closed"}.get(cur, "")}
        if cur == "rejected":
            groups["rejected"].append(item)
        elif cur == "applied":
            groups["ghosted" if (age(r) or 0) >= ghost_days else "waiting"].append(item)
        else:
            groups[cur if cur in groups else "waiting"].append(item)
        if f == 1:
            F["rej_app" if cur == "rejected" else ("ghosted" if (age(r) or 0) >= ghost_days else "waiting")] += 1
            continue
        if "task" in seen:
            F["task"] += 1
        if f == 2:
            F["rej_task" if cur == "rejected" else "task_act"] += 1
            continue
        if "task" in seen:
            F["sent_int"] += 0
        else:
            F["sent_int"] += 1                       # went straight from application to interview
        F["interview"] += 1
        if f == 3:
            F["rej_int" if cur == "rejected" else "int_act"] += 1
        else:
            F["offer"] += 1
            F["offer_failed" if cur in ("onboarding_failed", "rejected") else ("offer_closed" if cur == "project_closed" else "offer_act")] += 1
    reviewed = len(rows)
    skipped = sum(1 for r in rows if r["status"] == "skipped")
    open_ = sum(1 for r in rows if r["status"] in ("new", "drafted"))
    per_day = {}
    for r in sent:
        if r.get("applied_on"):
            per_day[r["applied_on"]] = per_day.get(r["applied_on"], 0) + 1
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    by_src = {}
    for r in sent:
        s = by_src.setdefault(r["source"] or "other", {"sent": 0, "replied": 0})
        s["sent"] += 1
        s["replied"] += (st.get(r["id"], {"furthest": 1})["furthest"] > 1) or r["status"] == "rejected"
    # timing and quality metrics
    first_reply = {}
    for h in history.read():
        if h["new"] in ("task", "interview", "offer", "onboarding_failed", "project_closed", "rejected") and h["id"] not in first_reply:
            first_reply[h["id"]] = h["ts"][:10]
    waits = []
    for r in sent:
        d1, d0 = first_reply.get(r["id"]), r.get("applied_on")
        if d1 and d0:
            try:
                waits.append(max((datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d0, "%Y-%m-%d")).days, 0))
            except ValueError:
                pass
    waits.sort()
    med = (waits[len(waits) // 2] if len(waits) % 2 else (waits[len(waits) // 2 - 1] + waits[len(waits) // 2]) / 2) if waits else None

    def fscore(r):
        try:
            return float(r["score"])
        except (ValueError, TypeError):
            return None
    sc_all = [x for x in (fscore(r) for r in sent) if x is not None]
    sc_int = [x for x in (fscore(r) for r in sent if st.get(r["id"], {"furthest": 1})["furthest"] >= 3) if x is not None]
    weeks = []
    for w in range(7, -1, -1):
        a, b = today - timedelta(days=7 * w + 6), today - timedelta(days=7 * w)
        weeks.append({"d": b.isoformat()[5:], "n": sum(1 for r in sent if r.get("applied_on") and a.isoformat() <= r["applied_on"] <= b.isoformat())})
    fam_stats = {}
    for r in sent:
        info = st.get(r["id"], {"furthest": 1})
        x = fam_stats.setdefault(family(r), {"f": family(r), "sent": 0, "replied": 0, "interview": 0, "offer": 0})
        x["sent"] += 1
        x["replied"] += int(info["furthest"] > 1 or r["status"] == "rejected")
        x["interview"] += int(info["furthest"] >= 3)
        x["offer"] += int(info["furthest"] >= 4)
    cohorts = []
    for w in range(9, -1, -1):
        mon = today - timedelta(days=today.weekday() + 7 * w)
        sun = mon + timedelta(days=6)
        grp = [r for r in sent if r.get("applied_on") and mon.isoformat() <= r["applied_on"] <= sun.isoformat()]
        fs = [st.get(r["id"], {"furthest": 1})["furthest"] for r in grp]
        cohorts.append({"d": mon.isoformat()[5:], "sent": len(grp), "replied": sum(1 for r, f in zip(grp, fs) if f > 1 or r["status"] == "rejected"),
                        "interview": sum(1 for f in fs if f >= 3), "offer": sum(1 for f in fs if f >= 4)})
    applied_days = sorted({r["applied_on"] for r in sent if r.get("applied_on")})
    streak, d0 = 0, today
    if today.isoformat() not in applied_days:
        d0 = today - timedelta(days=1)
    while d0.isoformat() in applied_days:
        streak += 1
        d0 -= timedelta(days=1)
    def _days(r):
        try:
            return (datetime.strptime(r["applied_on"], "%Y-%m-%d") - datetime.strptime((r.get("posted") or "")[:10], "%Y-%m-%d")).days
        except (ValueError, TypeError, KeyError):
            return None
    def _bucket(items, fn, labels):
        out = {k: {"sent": 0, "replied": 0, "interview": 0} for k in labels}
        for r in items:
            k = fn(r)
            if k is None:
                continue
            f = st.get(r["id"], {"furthest": 1})["furthest"]
            out[k]["sent"] += 1
            out[k]["replied"] += int(f > 1 or r["status"] == "rejected")
            out[k]["interview"] += int(f >= 3)
        return [{"k": k, **v} for k, v in out.items()]
    sc_lab = ["fit 70+", "fit 55-69", "fit under 55"]
    sp_lab = ["within 2 days of posting", "3-7 days", "8+ days"]
    predictors = {
        "score": _bucket(sent, lambda r: None if fscore(r) is None else (sc_lab[0] if fscore(r) >= 70 else sc_lab[1] if fscore(r) >= 55 else sc_lab[2]), sc_lab),
        "channel": _bucket(sent, lambda r: "via referral" if "via referral" in (r.get("notes") or "").lower() else "cold application", ["cold application", "via referral"]),
        "speed": _bucket(sent, lambda r: None if _days(r) is None else (sp_lab[0] if _days(r) <= 2 else sp_lab[1] if _days(r) <= 7 else sp_lab[2]), sp_lab)}
    gig_rows = [r for r in rows if (r["source"] or "") in ("alignerr", "outlier", "mercor", "telus") and r["status"] in history.SENT]
    gigs = {"sent": len(gig_rows), "interview": sum(1 for r in gig_rows if st.get(r["id"], {"furthest": 1})["furthest"] >= 3),
            "offer": sum(1 for r in gig_rows if st.get(r["id"], {"furthest": 1})["furthest"] >= 4),
            "rejected": sum(1 for r in gig_rows if r["status"] == "rejected"),
            "failed": sum(1 for r in gig_rows if r["status"] == "onboarding_failed"), "closed": sum(1 for r in gig_rows if r["status"] == "project_closed"),
            "items": [{"t": r["title"][:50], "s": r["status"], "pay": r.get("salary", "")} for r in gig_rows]}
    metrics = {"predictors": predictors, "gigs": gigs, "families": sorted(fam_stats.values(), key=lambda x: -x["sent"]), "cohorts": cohorts, "streak_days": streak,
               "best_week": max([w["n"] for w in weeks] or [0]), "first_applied": applied_days[0] if applied_days else None,
               "active_days": len(applied_days), "median_reply_days": med, "replies_timed": len(waits),
               "avg_score_sent": round(sum(sc_all) / len(sc_all), 1) if sc_all else None,
               "avg_score_interview": round(sum(sc_int) / len(sc_int), 1) if sc_int else None,
               "sent_7d": weeks[-1]["n"], "sent_prev_7d": weeks[-2]["n"], "weeks": weeks, "excluded": list(exclude)}
    return {"reviewed": reviewed, "skipped": skipped, "open": open_, "sent": len(sent), "groups": groups, "ghost_days": ghost_days,
            "flow": F, "per_day": [{"d": d, "n": per_day.get(d, 0)} for d in days], "by_source": by_src, "metrics": metrics}


def jobs_payload():
    out = []
    due = {r["id"] for r in kit.due_followups()}
    for r in read_tracker():
        d = kit.app_dir(r)
        files = {}
        for k, name in [("cv", paths.file_prefix() + "_CV.pdf"), ("letter", paths.file_prefix() + "_Cover_Letter.pdf"), ("notes", "notes.md"), ("prep", "interview_prep.md")]:
            if (d / name).exists():
                files[k] = str((d / name).relative_to(OUT))
        busy = [k for k in PROCS if k.endswith(r["id"]) and PROCS[k]["proc"].poll() is None]
        out.append({**r, "files": files, "busy": busy[0].split("-")[0] if busy else "", "ref": bool(kit.referral(r["company"])), "fdue": r["id"] in due})
    tier = {"new": 0, "drafted": 0, "applied": 1, "task": 1, "interview": 1, "offer": 1, "onboarding_failed": 2, "project_closed": 2, "rejected": 2, "skipped": 2}
    cdata = contacts.ensure(out)
    for r in out:
        r["contacts"] = cdata.get(r["id"])

    def num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0
    # not yet applied first, best fit first; applied / in-process next; rejected and skipped last
    out.sort(key=lambda r: (r.get("posted") or r.get("found") or ""), reverse=True)            # freshest first ...
    out.sort(key=lambda r: (tier.get(r["status"], 0), -(max(num(r.get("fit")), 0) // 10)))      # ... within the same open/applied tier and 10-point fit band
    return out


# ---------------- background work
def start(name, cmd, on_done=None):
    cur = PROCS.get(name)
    if cur and cur["proc"].poll() is None:
        return False
    OUT.mkdir(exist_ok=True)
    f = (OUT / f"{name}.log").open("w")
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUNBUFFERED": "1"})  # live log in the dashboard
    PROCS[name] = {"proc": p, "log": OUT / f"{name}.log"}
    if on_done:
        threading.Thread(target=lambda: (p.wait(), on_done(p.returncode)), daemon=True).start()
    return True


def draft(jid):
    def done(rc):
        row = next((r for r in read_tracker() if r["id"] == jid), None)
        if rc == 0 and row and row["status"] == "new":
            set_status(jid, "drafted")
    return start("draft-" + jid, [sys.executable, "tailor.py", jid], done)


def strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", " ", s, flags=re.S))).strip()


def add_manual(d):
    title, company, url, text = d.get("title", "").strip(), d.get("company", "").strip(), d.get("url", "").strip(), d.get("text", "").strip()
    if not text and url:
        if not re.match(r"https?://", url):
            return {"error": "link must start with http(s)"}
        try:
            text = strip(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read().decode("utf-8", "ignore"))[:8000]
        except Exception as e:
            return {"error": f"could not fetch the link ({e}); paste the posting text instead"}
    if not (title and company and text):
        return {"error": "title, company and posting text (or a fetchable link) are required"}
    jid = hashlib.sha1(f"{company.lower()}|{title.lower()}".encode()).hexdigest()[:8]
    row = dict(id=jid, found=date.today().isoformat(), score=100, status="new", title=title, company=company, location="", source="manual", posted="", url=url, notes="", applied_on="", followup_on="")
    with LOCK:
        rows = read_tracker()
        if not any(r["id"] == jid for r in rows):
            rows.append(row)
            write_tracker(rows)
    (OUT / "jobs").mkdir(parents=True, exist_ok=True)
    (OUT / "jobs" / f"{jid}.json").write_text(json.dumps({**row, "desc": text[:6000]}, ensure_ascii=False))
    draft(jid)
    return {"id": jid}


# ---------------- page
PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Job Hunt</title>
<style>
:root{--bg:#f6f7f6;--card:#fff;--fg:#1d2420;--mut:#6b756f;--line:#e2e6e3;--acc:#1f5c3a;--accfg:#fff;--warn:#b45309;--warnbg:#fef3c7}
@media(prefers-color-scheme:dark){:root{--bg:#141815;--card:#1c221e;--fg:#e8ece9;--mut:#93a09a;--line:#2b332e;--acc:#4caf7a;--accfg:#08140d;--warn:#f59e0b;--warnbg:#3a2a07}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--line);background:var(--card);position:sticky;top:0;z-index:5}
h1{font-size:17px;margin:0 12px 0 0}button,select,input,textarea{font:inherit;color:inherit}
button{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 11px;cursor:pointer}button:hover{border-color:var(--acc)}
button.p{background:var(--acc);color:var(--accfg);border-color:var(--acc)}button:disabled{opacity:.5;cursor:wait}
main{padding:16px 20px;max-width:1300px;margin:auto}.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
.bar input[type=text],.bar input:not([type]){border:1px solid var(--line);background:var(--card);border-radius:8px;padding:6px 10px;min-width:200px}select{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:5px 8px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 14px;min-width:110px}.stat b{display:block;font-size:20px;color:var(--acc)}.stat span{font-size:12px;color:var(--mut)}
.chips button{border-radius:99px;padding:4px 12px;font-size:13px}.chips button.on{background:var(--acc);color:var(--accfg);border-color:var(--acc)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
tr:last-child td{border-bottom:0}.sc{font-weight:700;color:var(--acc);width:44px}.mut{color:var(--mut);font-size:12.5px}a{color:var(--acc)}
.t a{font-weight:600;text-decoration:none}.act{white-space:nowrap}.act button,.act a.b{margin:0 4px 4px 0;font-size:12.5px;padding:4px 9px}a.b{border:1px solid var(--line);border-radius:8px;text-decoration:none;display:inline-block}
.new{background:color-mix(in srgb,var(--acc) 7%,transparent)}.tag{font-size:11px;border:1px solid var(--line);border-radius:99px;padding:1px 7px;color:var(--mut)}.tag.w{border-color:var(--warn);color:var(--warn)}.tag.g{border-color:var(--acc);color:var(--acc)}
pre{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;max-height:200px;overflow:auto;font-size:12px;white-space:pre-wrap}
dialog{border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--fg);width:min(820px,95vw);max-height:92vh}dialog::backdrop{background:#0006}
dialog label{display:block;margin:8px 0 3px;font-size:12.5px;color:var(--mut)}dialog input,dialog textarea{width:100%;border:1px solid var(--line);background:var(--bg);border-radius:8px;padding:7px 9px}
.blk{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:10px 0}.blk h4{margin:0 0 6px;display:flex;justify-content:space-between;align-items:center;font-size:13px}.blk pre{max-height:none;margin:6px 0 0;background:var(--bg)}
.need{background:var(--warnbg);border-color:var(--warn)}.ok{color:var(--acc)}.today{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}.tc{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 14px;border-top:4px solid var(--c)}.tc b{display:block;font-size:24px;line-height:1.15}.tc span{font-size:12.5px;color:var(--mut)}.tc i{display:block;height:6px;border-radius:9px;background:var(--line);margin-top:6px;overflow:hidden}.tc i u{display:block;height:100%;background:var(--c)}
.tabs button{border:0;border-bottom:3px solid transparent;border-radius:0;background:none;padding:6px 14px;font-size:15px;font-weight:600;color:var(--mut)}.tabs button.on{color:var(--fg);border-bottom-color:var(--acc)}
.ppl{margin-top:6px;border:1px solid var(--line);border-radius:10px;padding:6px 10px;background:color-mix(in srgb,var(--acc) 4%,transparent)}.ppl summary{cursor:pointer;font-size:12.5px;color:var(--mut)}.pp{display:grid;grid-template-columns:minmax(150px,1.2fr) auto 130px minmax(90px,1fr) minmax(110px,1.3fr);gap:6px;align-items:center;padding:4px 0;border-top:1px solid var(--line);font-size:12.5px}.pp:first-of-type{border-top:0}.pp input{border:1px solid var(--line);background:var(--card);border-radius:7px;padding:3px 7px;min-width:0}.pp select{padding:3px 5px}
@media(max-width:900px){.pp{grid-template-columns:1fr 1fr}}
.empty{padding:40px;text-align:center;color:var(--mut)}@media(max-width:760px){.hide-s{display:none}}
</style></head><body>
<header><h1>Job Hunt</h1><button class="p" id="run" onclick="runAll()">Search + draft now</button><button id="srch" onclick="runSearch()">Search only</button>
<button onclick="addDlg.showModal()">+ Add posting</button><button onclick="openEd()">Settings</button><a class="b" href="/funnel" style="padding:6px 11px">Funnel</a><span class="mut" id="msg"></span></header>
<main><div class="today" id="today"></div>
<div class="bar tabs" id="tabs"></div><div class="bar chips" id="chips"></div>
<div class="bar"><input id="q" placeholder="Filter title / company…" oninput="render()"><select id="st" onchange="render()"><option value="">All statuses</option></select>
<select id="src" onchange="render()"><option value="">All sources</option></select><label class="mut"><input type="checkbox" id="fresh" onchange="render()"> last 3 days</label><span class="mut" id="cnt"></span></div>
<table><thead><tr><th>Score</th><th>Role</th><th class="hide-s">Where</th><th>Status</th><th>Actions</th></tr></thead><tbody id="rows"></tbody></table>
<h3 class="mut" style="margin:18px 0 6px;font-size:13px">Last run log</h3><pre id="log">—</pre></main>
<dialog id="addDlg"><h3 style="margin-top:0">Add a posting (LinkedIn, company site…)</h3>
<label>Job title</label><input id="a_t"><label>Company</label><input id="a_c"><label>Link (optional)</label><input id="a_u"><label>Posting text (paste it; or leave empty to fetch the link)</label><textarea id="a_x" rows="9"></textarea>
<p style="text-align:right"><button onclick="addDlg.close()">Cancel</button> <button class="p" onclick="addJob()">Add and draft CV</button></p></dialog>
<dialog id="edDlg"><h3 style="margin-top:0">Settings and data</h3><select id="edf" onchange="loadEd()">%EDITABLE%</select>
<p class="mut">config: search rules · profile: facts the CV may use · answers: form answers · stories: interview story bank · network: contacts for referrals</p><textarea id="edt" rows="18" style="font-family:ui-monospace,monospace;font-size:12px"></textarea>
<p id="edm" class="mut"></p><p style="text-align:right"><button onclick="edDlg.close()">Close</button> <button class="p" onclick="saveEd()">Save</button></p></dialog>
<dialog id="kitDlg"><div id="kit"></div><p style="text-align:right"><button onclick="kitDlg.close()">Close</button></p></dialog>
<script>
const ST=%STATUSES%;let jobs=[],chip='';const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(p,b){const r=await fetch(p,b?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(b)}:{});if(r.status==401){location.href='/login';return {}}return r.json()}
function fill(sel,vals){const cur=sel.value;const first=sel.options[0].outerHTML;sel.innerHTML=first+vals.map(v=>`<option>${esc(v)}</option>`).join('');sel.value=cur}
const CH=[['','All'],['drafted','Apply queue'],['applied','Applied'],['task','Task'],['interview','Interview'],['ref','Referral'],['closed','Closed']];
const CST=%CSTATUSES%;let tab=(()=>{try{return localStorage.getItem('jhTab')||'jobs'}catch(e){return 'jobs'}})();
const isGig=j=>j.source=='alignerr';
async function load(){jobs=await api('/api/jobs');fill($('st'),ST);fill($('src'),[...new Set(jobs.map(j=>j.source))]);render();today()}
function today(){const J=jobs.filter(j=>!isGig(j)),cut3=new Date(Date.now()-3*864e5).toISOString().slice(0,10),cut7=new Date(Date.now()-7*864e5).toISOString().slice(0,10);
 const sc=j=>Math.max(+j.fit||0,+j.score||0),open=J.filter(j=>['new','drafted'].includes(j.status));
 const newFits=open.filter(j=>sc(j)>=60&&(j.posted||j.found)>=cut3).length,ready=J.filter(j=>j.status=='drafted'&&j.files.cv).length;
 const ppl=J.filter(j=>['new','drafted','applied'].includes(j.status)).reduce((a,j)=>a+((j.contacts&&j.contacts.people)||[]).filter(p=>p.status=='not contacted').length,0);
 const sentSt=['applied','task','interview','offer','onboarding_failed','project_closed','rejected'],sent=J.filter(j=>sentSt.includes(j.status)),rep=sent.filter(j=>j.status!='applied').length;
 const wk=J.filter(j=>sentSt.includes(j.status)&&j.applied_on&&j.applied_on>=cut7).length;let goal=10;try{goal=+(localStorage.getItem('jhGoal')||10)}catch(e){}
 const pct=Math.min(100,Math.round(100*wk/goal)),C={a:'#3b82f6',b:'#30a46c',c:'#7c5cff',d:'#d4a017',e:'#f5a524'};
 $('today').innerHTML=[['New good fits (3 days)',newFits,C.b,''],['Ready to apply',ready,C.a,''],['People to reach',ppl,C.c,''],['This week (jobs)',wk+' / '+goal,wk>=goal?C.b:C.d,`<i><u style="width:${pct}%"></u></i>`],['Reply rate (jobs)',sent.length?Math.round(100*rep/sent.length)+'%':'–',C.e,'']].map(x=>`<div class="tc" style="--c:${x[2]}"><b>${x[1]}</b><span>${x[0]}</span>${x[3]}</div>`).join('')}
function setTab(t){tab=t;try{localStorage.setItem('jhTab',t)}catch(e){}render()}
function peopleHtml(j){const c=j.contacts;if(!c||!c.people||!c.people.length)return '';const todo=c.people.filter(p=>p.status=='not contacted').length;
 return `<details class="ppl"><summary>People to look for (${c.people.length})${todo?' · '+todo+' not contacted':''}</summary>${c.people.map(p=>`<div class="pp"><b>${esc(p.label)}</b><span><a class="b" style="padding:2px 8px" target="_blank" rel="noopener" href="${esc(p.li)}">LinkedIn</a> <a class="b" style="padding:2px 8px" target="_blank" rel="noopener" href="${esc(p.g)}">Google</a></span>
<select onchange="setPerson('${j.id}','${p.id}',{status:this.value})">${CST.map(s=>`<option ${s==p.status?'selected':''}>${s}</option>`).join('')}</select>
<input placeholder="name" value="${esc(p.name)}" onchange="setPerson('${j.id}','${p.id}',{name:this.value},1)"><input placeholder="note" value="${esc(p.note)}" onchange="setPerson('${j.id}','${p.id}',{note:this.value},1)"></div>`).join('')}</details>`}
async function setPerson(id,pid,kv,quiet){await api('/api/contact',{id,pid,...kv});if(!quiet)await load()}
function render(){$('tabs').innerHTML=[['jobs','Jobs',jobs.filter(j=>!isGig(j)).length],['gigs','Gigs',jobs.filter(isGig).length]].map(t=>`<button class="${tab==t[0]?'on':''}" onclick="setTab('${t[0]}')">${t[1]} <span class="mut">${t[2]}</span></button>`).join('');
$('chips').innerHTML=CH.map(c=>`<button class="${chip==c[0]?'on':''}" onclick="chip='${c[0]}';render()">${c[1]}</button>`).join('');
const q=$('q').value.toLowerCase(),st=$('st').value,src=$('src').value,fresh=$('fresh').checked&&!chip;const cut=new Date(Date.now()-3*864e5).toISOString().slice(0,10);
const inChip=j=>!chip||(chip=='ref'?j.ref||/via referral/i.test(j.notes||''):chip=='closed'?['rejected','skipped','onboarding_failed','project_closed'].includes(j.status):j.status==chip);
const l=jobs.filter(j=>(tab=='gigs')==isGig(j)&&(!q||(j.title+j.company).toLowerCase().includes(q))&&(!st||j.status==st)&&(!src||j.source==src)&&(!fresh||(j.posted||j.found)>=cut||['applied','task','interview','offer'].includes(j.status))&&inChip(j)&&(st||chip||!['skipped','rejected','onboarding_failed','project_closed'].includes(j.status)));
$('cnt').textContent=l.length+' shown';
$('rows').innerHTML=l.length?l.map(j=>`<tr class="${j.status=='new'?'new':''}"><td class="sc">${j.fit?j.fit+'%':(j.score||'')}<div class="mut" style="font-weight:400">${j.fit?'fit':''}</div></td>
<td class="t"><a href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title)}</a><div class="mut">${esc(j.company)}${j.salary?' · <span title="'+esc(j.salary_basis)+'">💶 '+esc(j.salary)+(String(j.salary_basis).startsWith('stated')?'':' (est.)')+'</span>':''} · found ${esc(j.found)}${j.applied_on?' · applied '+esc(j.applied_on):''}${j.posted?' · posted '+esc(j.posted):''}</div>
<div>${j.apply_url&&!/linkedin\.com/.test(j.apply_url)?'<span class="tag g">direct apply</span> ':(j.source=='linkedin'?'<span class="tag w">LinkedIn only</span> ':'')}${j.ref?'<span class="tag g">referral</span> ':''}${j.notes?'<span class="mut">'+esc(j.notes)+'</span>':''}</div>${peopleHtml(j)}</td>
<td class="hide-s mut">${esc(j.location)}<div><span class="tag">${esc(j.source)}</span></div></td>
<td><select onchange="setSt('${j.id}',this.value)">${ST.map(s=>`<option ${s==j.status?'selected':''}>${s}</option>`).join('')}</select></td>
<td class="act">${j.busy?`<span class="mut">${j.busy}…</span>`:(j.files.cv?`<button class="p" onclick="openKit('${j.id}')">Apply kit</button><a class="b" target="_blank" href="/file?p=${encodeURIComponent(j.files.cv)}">CV</a><a class="b" target="_blank" href="/file?p=${encodeURIComponent(j.files.letter||'')}">Letter</a>${prepBtns(j)}<button onclick="draft('${j.id}')">Redo</button>`:`<button class="p" onclick="draft('${j.id}')">Draft CV</button>${prepBtns(j)}`)}</td></tr>`).join(''):'<tr><td colspan="5" class="empty">Nothing here.</td></tr>'}
async function setSt(id,s){let stage;if(s=='rejected'){const a=prompt('How far did this get before the rejection?\n1 = application only\n2 = task / test\n3 = interview','1');if(a===null){await load();return}stage={'1':'application','2':'task','3':'interview'}[a.trim()]||'application'}await api('/api/status',{id,status:s,stage});await load()}
async function draft(id){await api('/api/draft',{id});poll()}
async function prep(id){await api('/api/prep',{id});poll()}
function prepBtns(j){return j.files.prep?`<a class="b" target="_blank" href="/file?p=${encodeURIComponent(j.files.prep)}">Prep</a><button onclick="prep('${j.id}')" title="Write the interview prep again">Redo prep</button>`:`<button onclick="prep('${j.id}')">Prep</button>`}
async function runAll(){$('msg').textContent='Searching…';await api('/api/run',{draft:true});poll()}
async function runSearch(){$('msg').textContent='Searching…';await api('/api/run',{draft:false});poll()}
let timer;function poll(){clearTimeout(timer);timer=setTimeout(async()=>{const s=await api('/api/status');$('log').textContent=s.log||'—';$('log').scrollTop=1e9;
$('run').disabled=$('srch').disabled=s.running;$('msg').textContent=s.running?'Working… (search)':'';await load();if(s.running||s.drafting)poll()},1500)}
async function addJob(){const r=await api('/api/manual',{title:$('a_t').value,company:$('a_c').value,url:$('a_u').value,text:$('a_x').value});if(r.error){alert(r.error);return}addDlg.close();['a_t','a_c','a_u','a_x'].forEach(i=>$(i).value='');poll()}
async function loadEd(){const r=await api('/api/file?name='+$('edf').value);$('edt').value=r.text;$('edm').textContent=''}
function openEd(){loadEd();edDlg.showModal()}
async function saveEd(){const r=await api('/api/file',{name:$('edf').value,text:$('edt').value});$('edm').textContent=r.error?('Not saved: '+r.error):'Saved.'}
function cp(i){navigator.clipboard.writeText(window.__k[i]).then(()=>{const b=document.getElementById('cp'+i);b.textContent='Copied';setTimeout(()=>b.textContent='Copy',1200)})}
function blk(i,title,text,cls=''){window.__k[i]=text;return `<div class="blk ${cls}"><h4><span>${title}</span><button id="cp${i}" onclick="cp(${i})">Copy</button></h4><pre>${esc(text)}</pre></div>`}
async function openKit(id){const k=await api('/api/kit?id='+id);if(k.error){alert(k.error);return}window.__k=[];const j=k.job;let n=0,h=`<h3 style="margin-top:0">${esc(j.title)} <span class="mut">· ${esc(j.company)}</span></h3>
<p class="mut">Fit ${esc(k.fit)}% · ${esc(k.job.salary||'')} (${esc(k.job.salary_basis||'')}) · ${esc(k.apply_via||'')}</p><p>${k.direct?`<a class="b" style="padding:6px 12px;background:var(--acc);color:var(--accfg)" href="${esc(k.apply_url)}" target="_blank" rel="noopener">Apply on company page</a> `:''}<a class="b" style="padding:6px 12px" href="${esc(j.url)}" target="_blank" rel="noopener">View posting</a> <a class="b" style="padding:6px 12px" target="_blank" href="/file?p=${encodeURIComponent(k.files.cv||'')}">CV (PDF)</a> <a class="b" style="padding:6px 12px" target="_blank" href="/file?p=${encodeURIComponent(k.files.letter||'')}">Letter (PDF)</a>
<button onclick="reveal('${id}')">Show files in Finder</button> <button class="p" onclick="markApplied('${id}')">Mark applied</button></p>`;
h+=`<div class="blk"><h4>Get seen (skip LinkedIn Easy Apply)</h4>${k.seen.map(c=>`<div>${c.ok?'<span class="ok">✔</span>':'☐'} ${esc(c.t)}</div>`).join('')}<div style="margin-top:8px">${Object.entries(k.search_links).map(([n,u])=>`<a class="b" style="padding:4px 10px;margin:2px" target="_blank" rel="noopener" href="${esc(u)}">${esc(n)}</a>`).join('')}</div></div>`;
h+=blk(n++,'LinkedIn note to a recruiter or hiring manager (under 300 characters)',k.linkedin_note);
h+=`<div class="blk"><h4>Before you submit</h4>${k.checklist.map(c=>`<div>${c.ok?'<span class="ok">✔</span>':'☐'} ${esc(c.t)}</div>`).join('')}</div>`;
if(k.referral)h+=blk(n++,`Referral: ${esc(k.referral.contact)} (${esc(k.referral.relation)})`,k.referral.message,'need');
if(k.letter)h+=blk(n++,'Cover letter',k.letter);
h+=`<h4 style="margin:14px 0 0">Form answers</h4><p class="mut">Orange = you must fill these in yourself (edit in Settings → answers.json).</p>`;
k.answers.forEach(a=>{h+=blk(n++,esc(a.q),a.a,a.needs_input?'need':'')});
h+=`<p>${k.files.prep?`<a class="b" style="padding:6px 12px" target="_blank" href="/file?p=${encodeURIComponent(k.files.prep)}">Interview prep</a> <button onclick="prep('${id}');kitDlg.close()">Redo prep</button>`:`<button onclick="prep('${id}');kitDlg.close()">Generate interview prep</button>`}</p>`;
if(k.notes)h+=`<div class="blk"><h4>Fit notes, ATS coverage and gaps</h4><pre>${esc(k.notes)}</pre></div>`;
$('kit').innerHTML=h;kitDlg.showModal()}
async function markApplied(id){await api('/api/status',{id,status:'applied'});kitDlg.close();load()}
async function followSent(id){await api('/api/followup_sent',{id});kitDlg.close();load()}
async function reveal(id){const r=await api('/api/reveal',{id});if(r.error)alert(r.error)}
load();poll();
</script></body></html>"""

FUNNEL = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Job Hunt - Funnel</title>
<style>
:root{--bg:#f6f7f6;--card:#fff;--fg:#1d2420;--mut:#6b756f;--line:#e2e6e3;--acc:#1f5c3a;--sent:#3b82f6;--rej:#e5484d;--int:#30a46c;--gho:#8b8d98;--wait:#f5a524;--task:#7c5cff;--off:#d4a017;--skip:#a1a9a4}
@media(prefers-color-scheme:dark){:root{--bg:#141815;--card:#1c221e;--fg:#e8ece9;--mut:#93a09a;--line:#2b332e;--acc:#4caf7a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:12px 20px;border-bottom:1px solid var(--line);background:var(--card)}h1{font-size:17px;margin:0 12px 0 0}a{color:var(--acc)}
main{max-width:1100px;margin:auto;padding:16px 16px 40px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;border-top:4px solid var(--c)}.card b{display:block;font-size:26px}.card span{color:var(--mut);font-size:12.5px}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin-bottom:16px}.box h2{font-size:14px;margin:0 0 8px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}
svg{width:100%;height:auto;display:block}svg text{fill:var(--fg);font:14px -apple-system,Segoe UI,sans-serif}.lnk{opacity:.38}.lnk:hover{opacity:.7}
label{color:var(--mut);font-size:13px}input[type=range]{vertical-align:middle}details{border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin:6px 0;background:var(--card)}summary{cursor:pointer;font-weight:600}
li{margin:3px 0}.mut{color:var(--mut)}.pill{display:inline-block;width:10px;height:10px;border-radius:99px;margin-right:6px;background:var(--c)}table{width:100%;border-collapse:collapse}td,th{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left}
.gg{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}.gauge{text-align:center}.gauge svg{max-width:230px;margin:auto}.gauge b{display:block;font-size:13.5px;margin-top:2px}.gauge span{color:var(--mut);font-size:12px}
.steps{display:flex;flex-direction:column;gap:8px}.st{display:grid;grid-template-columns:150px 1fr 120px;gap:10px;align-items:center;font-size:13.5px}.bar{height:16px;background:var(--line);border-radius:99px;overflow:hidden}.bar i{display:block;height:100%;border-radius:99px;background:var(--c)}.st em{font-style:normal;color:var(--mut);font-size:12.5px;text-align:right}
.verdict{margin-top:12px;padding:10px 12px;border-radius:10px;background:var(--bg);border:1px solid var(--line);font-size:13.5px}.note{color:var(--mut);font-size:12px;margin-top:8px}
@media(max-width:600px){.st{grid-template-columns:100px 1fr 80px}}
</style></head><body><header><h1>Job Hunt - Funnel</h1><a href="/">Back to jobs</a><span class="mut" id="when"></span></header>
<main><div class="cards" id="cards"></div>
<div class="box"><h2>Where every role went</h2><svg id="sk" viewBox="0 0 1180 560" role="img" aria-label="Sankey diagram of applications"></svg>
<div style="margin-top:6px"><label><input type="checkbox" id="all"> include every role I reviewed &nbsp;&nbsp;</label><label>Count as ghosted after <b id="gd">14</b> days without a reply <input type="range" id="rg" min="5" max="45" value="14"></label></div></div>
<div class="box"><h2>Conversion: how far applications get</h2><div class="steps" id="steps"></div><div class="note">Each bar is the share of ALL applications sent that reached that stage; the right side is the step-to-step rate.</div></div>
<div class="box"><h2>How you are doing</h2><div class="gg" id="gauges"></div><div class="verdict" id="verdict"></div><div class="note">Gauge zones are rough rules of thumb for online applications, not a benchmark for your market. Small numbers move a lot: judge trends over 30+ applications. <label><input type="checkbox" id="nogig"> jobs only (gig platforms like Alignerr shown separately below)</label> &nbsp; <a href="#" id="tgt">change weekly goal</a></div></div>
<div class="box"><h2>Output game</h2><div class="cards" id="game"></div><div id="goalbar"></div><div class="note" id="gamenote"></div></div>
<div class="box"><h2>Averages: you vs typical job seekers</h2><table id="bench"></table><div class="note">Typical ranges are collected from public job-search studies and recruiter reports (secondary sources, definitions differ). Directional only; they are not specific to your sector or country.</div></div>
<div class="box"><h2>By role family (your own data)</h2><table id="fams"></table><div class="note">Families are guessed from the job title. Compare each row to your overall rate, not to the benchmark: the numbers are small.</div></div>
<div class="box"><h2>Trend: rates by the week you applied</h2><svg id="trend" viewBox="0 0 920 200"></svg><div class="note">Each point is the share of that week's applications that got a reply (blue) or reached an interview (green). Recent weeks are still maturing, because answers take days to arrive, so they read low.</div></div>
<div class="box"><h2>What predicts a reply (your own data)</h2><table id="pred"></table><div class="note">Do fit score and speed to apply change your reply rate? Small samples: re-read this after 30+ applications.</div></div>
<div class="box"><h2>Gigs (Alignerr and similar)</h2><div id="gigs"></div></div>
<div class="box"><h2>Applications sent per week (last 8 weeks)</h2><svg id="weeks" viewBox="0 0 920 150"></svg></div>
<div class="box"><h2>Applications sent per day (last 14 days)</h2><svg id="bars" viewBox="0 0 920 150"></svg></div>
<div class="box"><h2>Reply rate by source</h2><table id="src"></table></div>
<div class="box"><h2>The roles</h2><div id="lists"></div></div></main>
<script>
const C={sent:'#3b82f6',rejected:'#e5484d',interview:'#30a46c',offer:'#d4a017',offer_failed:'#c0392b',offer_closed:'#9a8f6a',ghosted:'#8b8d98',waiting:'#f5a524',skipped:'#a1a9a4',reviewed:'#64748b',open:'#8b5cf6',task:'#7c5cff'};
const COL={reviewed:C.reviewed,skipped:C.skipped,open:C.open,sent:C.sent,waiting:C.waiting,ghosted:C.ghosted,rej_app:C.rejected,rej_task:C.rejected,rej_int:C.rejected,task:C.task,task_act:C.task,interview:C.interview,int_act:C.interview,offer:C.offer,offer_act:C.offer,offer_failed:C.rejected,offer_closed:C.skipped};
let D=null;const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(g){const r=await fetch('/api/funnel?ghost='+g+($('nogig').checked?'&ex=alignerr':''));if(r.status==401){location='/login';return}D=await r.json();draw()}
function draw(){
 const G=D.groups,n=k=>G[k].length;
 $('cards').innerHTML=[['Roles reviewed',D.reviewed,C.reviewed],['Applications sent',D.sent,C.sent],['Reached task / test',D.flow.task,C.task],['Reached interview',D.flow.interview,C.interview],['Offers',D.flow.offer,C.offer],['Failed onboarding',D.flow.offer_failed,C.offer_failed],['Project closed',D.flow.offer_closed,C.offer_closed],['Rejected',n('rejected'),C.rejected],['Ghosted',n('ghosted'),C.ghosted],['Waiting',n('waiting'),C.waiting]].map(x=>`<div class="card" style="--c:${x[2]}"><b>${x[1]}</b><span>${x[0]}</span></div>`).join('');
 $('gd').textContent=D.ghost_days;
 sankey();bars();extras();extras2();extras3();
 $('src').innerHTML='<tr><th>Source</th><th>Sent</th><th>Moved past application or answered</th><th>Rate</th></tr>'+Object.entries(D.by_source).sort((a,b)=>b[1].sent-a[1].sent).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v.sent}</td><td>${v.replied}</td><td>${v.sent?Math.round(100*v.replied/v.sent)+'%':'-'}</td></tr>`).join('');
 const names={offer:'Offers (active)',onboarding_failed:'Offer, failed onboarding',project_closed:'Offer accepted, project closed',interview:'Interview stage',task:'Task / test stage',rejected:'Rejected (path shown)',ghosted:'Ghosted (no reply)',waiting:'Waiting for a reply'};
 $('lists').innerHTML=Object.keys(names).filter(k=>n(k)).map(k=>`<details ${['interview','offer','task'].includes(k)?'open':''}><summary><span class="pill" style="--c:${C[k]}"></span>${names[k]} (${n(k)})</summary><ul>${G[k].map(x=>`<li>${esc(x.c)} - ${esc(x.t)} <span class="mut">${x.path?'['+esc(x.path)+'] ':''}${x.d?x.d+(x.age!=null?' ('+x.age+'d ago)':''):''}</span></li>`).join('')}</ul></details>`).join('');
 $('when').textContent=' updated '+new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
}
function sankey(){
 const F=D.flow,all=$('all').checked;
 const taskN=F.task, intN=F.interview, offN=F.offer;
 const t2i=Math.max(taskN-F.rej_task-F.task_act,0);
 let nodes=[{id:'reviewed',col:0,v:D.reviewed,t:'Roles reviewed'},{id:'skipped',col:1,v:D.skipped,t:'Skipped after reading'},{id:'open',col:1,v:D.open,t:'Open: new / drafted'},{id:'sent',col:1,v:D.sent,t:'Applications sent'},
  {id:'waiting',col:2,v:F.waiting,t:'Waiting'},{id:'ghosted',col:2,v:F.ghosted,t:'Ghosted'},{id:'rej_app',col:2,v:F.rej_app,t:'Rejected at application'},{id:'task',col:2,v:taskN,t:'Task / test'},
  {id:'rej_task',col:3,v:F.rej_task,t:'Rejected after task'},{id:'task_act',col:3,v:F.task_act,t:'Task in progress'},{id:'interview',col:3,v:intN,t:'Interview'},
  {id:'rej_int',col:4,v:F.rej_int,t:'Rejected after interview'},{id:'int_act',col:4,v:F.int_act,t:'Interview in progress'},{id:'offer',col:4,v:offN,t:'Offer'},{id:'offer_act',col:5,v:F.offer_act,t:'Offer: active'},{id:'offer_failed',col:5,v:F.offer_failed,t:'Failed onboarding'},{id:'offer_closed',col:5,v:F.offer_closed,t:'Project closed'}].filter(x=>x.v>0);
 if(!all){nodes=nodes.filter(x=>!['reviewed','skipped','open'].includes(x.id));nodes.forEach(x=>x.col-=1)}
 const has=id=>nodes.find(x=>x.id===id);
 const L=[['reviewed','skipped',D.skipped],['reviewed','open',D.open],['reviewed','sent',D.sent],['sent','waiting',F.waiting],['sent','ghosted',F.ghosted],['sent','rej_app',F.rej_app],['sent','task',taskN],['sent','interview',F.sent_int],
  ['task','rej_task',F.rej_task],['task','task_act',F.task_act],['task','interview',t2i],['interview','rej_int',F.rej_int],['interview','int_act',F.int_act],['interview','offer',offN],['offer','offer_act',F.offer_act],['offer','offer_failed',F.offer_failed],['offer','offer_closed',F.offer_closed]];
 const links=L.map(([s,t,v])=>({s,t,v})).filter(l=>l.v>0&&has(l.s)&&has(l.t));
 const nc=all?6:5,W=1180,H=all?640:560,top=18,gap=26,nw=18,xs=[...Array(nc).keys()].map(i=>12+i*((W-24-nw-230)/(nc-1)));
 const cols=[...Array(nc).keys()];
 const colTot=cols.map(c=>nodes.filter(x=>x.col===c).reduce((a,x)=>a+x.v,0));
 const cnt=cols.map(c=>nodes.filter(x=>x.col===c).length);
 const scale=Math.min(...cols.filter(c=>cnt[c]).map(c=>(H-2*top-gap*(cnt[c]-1))/Math.max(colTot[c],1)));
 const minh=24;
 cols.forEach(c=>{const ns=nodes.filter(x=>x.col===c);const used=ns.reduce((a,x)=>a+Math.max(x.v*scale,minh),0)+gap*Math.max(ns.length-1,0);let y=top+(H-2*top-used)/2;ns.forEach(x=>{x.h=Math.max(x.v*scale,minh);x.y=y;x.x=xs[c];y+=x.h+gap;x.o=0;x.i=0;x.color=COL[x.id]||'#888'})});
 let out='';
 links.forEach(l=>{const s=has(l.s),t=has(l.t);const w=Math.max(l.v*scale,2);const y0=s.y+s.o+w/2,y1=t.y+t.i+w/2;s.o+=w;t.i+=w;const x0=s.x+nw,x1=t.x,xm=(x0+x1)/2;
  out+=`<path class="lnk" d="M${x0},${y0} C${xm},${y0} ${xm},${y1} ${x1},${y1}" fill="none" stroke="${t.color}" stroke-width="${w}"><title>${s.t} -> ${t.t}: ${l.v}</title></path>`});
 nodes.forEach(x=>{out+=`<rect x="${x.x}" y="${x.y}" width="${nw}" height="${x.h}" rx="3" fill="${x.color}"/>`;
  out+=`<text x="${x.x+nw+10}" y="${x.y+x.h/2+5}" text-anchor="start" font-size="14"><tspan font-weight="700">${x.v}</tspan> ${x.t}</text>`});
 $('sk').setAttribute('viewBox','0 0 '+W+' '+H);$('sk').innerHTML=out;
}
function bars(){
 const d=D.per_day,mx=Math.max(1,...d.map(x=>x.n)),bw=920/d.length;let o='';
 d.forEach((x,i)=>{const h=x.n/mx*100;o+=`<rect x="${i*bw+6}" y="${112-h}" width="${bw-12}" height="${Math.max(h,1)}" rx="3" fill="${C.sent}" opacity="${x.n?1:.25}"><title>${x.d}: ${x.n}</title></rect><text x="${i*bw+bw/2}" y="${108-h}" text-anchor="middle" font-size="12">${x.n||''}</text><text x="${i*bw+bw/2}" y="134" text-anchor="middle" font-size="11" fill="#888">${x.d.slice(5)}</text>`});
 $('bars').innerHTML=o;
}

function gauge(label,val,max,zones,fmt,sub){
 // semicircle gauge: zones = [[upTo,color],...] on a 0..max scale
 const R=80,cx=100,cy=100,pt=(f,r)=>{const a=Math.PI*(1-f);return[cx+r*Math.cos(a),cy-r*Math.sin(a)]};
 let o='',prev=0;
 zones.forEach(([up,col])=>{const a=pt(prev/max,R),b=pt(Math.min(up,max)/max,R);o+=`<path d="M${a[0]},${a[1]} A${R},${R} 0 0 1 ${b[0]},${b[1]}" fill="none" stroke="${col}" stroke-width="14" opacity=".85"/>`;prev=up});
 const f=Math.max(0,Math.min(val/max,1)),n=pt(f,R-22),b1=pt(f,6);
 o+=`<line x1="${cx}" y1="${cy}" x2="${n[0]}" y2="${n[1]}" stroke="var(--fg)" stroke-width="3" stroke-linecap="round"/><circle cx="${cx}" cy="${cy}" r="6" fill="var(--fg)"/>`;
 o+=`<text x="${cx}" y="${cy+26}" text-anchor="middle" font-size="22" font-weight="700">${fmt(val)}</text>`;
 return `<div class="gauge"><svg viewBox="0 0 200 135">${o}</svg><b>${label}</b><span>${sub}</span></div>`;
}
const pc=(a,b)=>b?Math.round(1000*a/b)/10:0;
function extras(){
 const F=D.flow,M=D.metrics,S=D.sent;
 if(!S){$('steps').innerHTML='<span class="mut">No applications sent yet.</span>';$('gauges').innerHTML='';$('verdict').textContent='';return}
 const replied=S-F.waiting-F.ghosted,task=F.task,intv=F.interview,off=F.offer;
 const steps=[['Applications sent',S,S,C.sent,''],['Got any reply',replied,S,C.waiting,pc(replied,S)+'% of sent'],['Reached a task / test',task,S,C.task,'step: '+pc(task,replied)+'% of replies'],['Reached an interview',intv,S,C.interview,'step: '+pc(intv,replied)+'% of replies'],['Received an offer',off,S,C.offer,'step: '+pc(off,intv)+'% of interviews']];
 $('steps').innerHTML=steps.map(x=>`<div class="st"><span>${x[0]} <b>${x[1]}</b></span><div class="bar"><i style="--c:${x[3]};width:${Math.max(pc(x[1],S),x[1]?2:0)}%"></i></div><em>${x[4]||'100%'}</em></div>`).join('');
 const goal=+(localStorage.getItem('jhGoal')||10),replyR=pc(replied,S),intR=pc(intv,S),pace=M.sent_7d;
 const red='#e5484d',amb='#f5a524',grn='#30a46c';
 $('gauges').innerHTML=gauge('Reply rate',replyR,50,[[10,red],[25,amb],[50,grn]],v=>v+'%',replied+' of '+S+' answered (incl. rejections)')
  +gauge('Interview rate',intR,15,[[2,red],[5,amb],[15,grn]],v=>v+'%',intv+' of '+S+' reached an interview; typical 2-5% broad, 10-15% targeted')
  +gauge('This week',pace,Math.max(goal*1.5,1),[[goal*.5,red],[goal,amb],[goal*1.5,grn]],v=>v+'',pace+' sent in 7 days; goal '+goal+'/wk (last week '+M.sent_prev_7d+')')
  +gauge('Reply speed',M.median_reply_days==null?0:Math.min(M.median_reply_days,30),30,[[7,grn],[14,amb],[30,red]],v=>M.median_reply_days==null?'n/a':M.median_reply_days+' d',M.median_reply_days==null?'no replies yet':'median days to a first answer ('+M.replies_timed+' replies)');
 const tips=[];
 if(S<30)tips.push('Only '+S+' applications so far: rates are noisy, keep sending.');
 if(replyR<10&&S>=20)tips.push('Few replies: tighten the match (roles you clearly fit) before sending more.');
 if(replyR>=10&&intR<2&&S>=20)tips.push('Replies come but few interviews: look at which stage rejects you and adjust the CV or letter for those roles.');
 if(F.ghosted>F.waiting&&F.ghosted>3)tips.push('Many ghosted: portal applications rarely answer; favour roles with a visible team or contact.');
 if(pace<goal*.5)tips.push('Pace is under half your weekly goal ('+pace+' vs '+goal+').');
 if(M.avg_score_interview!=null&&M.avg_score_sent!=null)tips.push('Average fit score: '+M.avg_score_sent+' for everything sent, '+M.avg_score_interview+' for roles that reached an interview.');
 $('verdict').innerHTML=tips.length?tips.map(t=>'• '+esc(t)).join('<br>'):'Looking healthy: nothing to flag.';
 const mx=Math.max(1,...M.weeks.map(x=>x.n)),bw=920/M.weeks.length;let o='';
 M.weeks.forEach((x,i)=>{const h=x.n/mx*100;o+=`<rect x="${i*bw+10}" y="${112-h}" width="${bw-20}" height="${Math.max(h,1)}" rx="3" fill="${C.sent}" opacity="${x.n?1:.25}"><title>week ending ${x.d}: ${x.n}</title></rect><text x="${i*bw+bw/2}" y="${108-h}" text-anchor="middle" font-size="12">${x.n||''}</text><text x="${i*bw+bw/2}" y="134" text-anchor="middle" font-size="11" fill="#888">to ${x.d}</text>`});
 o+=`<line x1="0" x2="920" y1="${112-Math.min(goal/mx,1)*100}" y2="${112-Math.min(goal/mx,1)*100}" stroke="${C.offer}" stroke-dasharray="5 4" opacity=".7"/>`;
 $('weeks').innerHTML=o;
}

function extras2(){
 const F=D.flow,M=D.metrics,S=D.sent,goal=+(localStorage.getItem('jhGoal')||10);
 if(!S){return}
 const replied=S-F.waiting-F.ghosted,intv=F.interview,off=F.offer;
 const apo=intv&&off?null:null;
 // --- benchmark table
 const row=(a,b,c,d)=>`<tr><td>${a}</td><td><b>${b}</b></td><td>${c}</td><td>${d}</td></tr>`;
 const intR=pc(intv,S),i2o=intv?pc(off,intv):null,perOffer=(intv&&off)?Math.round(S/off):(intR>0?Math.round(S/(intR/100*0.25)):null);
 const verdict=(v,lo,hi)=>v==null?'-':(v<lo?'<span style="color:#e5484d">below typical</span>':(v>hi?'<span style="color:#30a46c">above typical</span>':'within typical'));
 const days=M.first_applied?Math.round((Date.now()-new Date(M.first_applied))/86400000):null;
 $('bench').innerHTML='<tr><th>Measure</th><th>You</th><th>Typical</th><th>Read</th></tr>'
  +row('Applications to interview',intR+'%','2-5% broad, 10-15% targeted',verdict(intR,2,15))
  +row('Interview to offer',i2o==null?'-':i2o+'%','about 20-33%',verdict(i2o,20,33))
  +row('Applications per offer',off?Math.round(S/off):'~'+(perOffer||'n/a')+' (projected at 25% interview-to-offer)','about 30-200+',perOffer==null?'-':(perOffer<30?'<span style="color:#30a46c">better than typical</span>':(perOffer>200?'<span style="color:#e5484d">worse than typical</span>':'within typical')))
  +row('Weekly applications',M.sent_7d+' this week','about 16 a week (Huntr, Q3 2025)',M.sent_7d>=16?'<span style="color:#30a46c">at or above typical</span>':'below typical')
  +row('Days since first application',days==null?'-':days+' d','median 108 d to a first offer (Huntr, Q1 2026)',days==null?'-':(days<108?'still inside the typical window':'past the typical window'));
 // --- families
 $('fams').innerHTML='<tr><th>Role family</th><th>Sent</th><th>Replies</th><th>Interviews</th><th>Offers</th><th>Reply</th><th>Interview</th></tr>'+M.families.map(x=>`<tr><td>${esc(x.f)}</td><td>${x.sent}</td><td>${x.replied}</td><td>${x.interview}</td><td>${x.offer}</td><td>${pc(x.replied,x.sent)}%</td><td>${pc(x.interview,x.sent)}%</td></tr>`).join('');
 // --- trend lines
 const CO=M.cohorts,W=920,Hh=150,pad=34,xs=i=>pad+i*((W-pad-20)/(CO.length-1)),ys=v=>Hh-6-Math.min(v,60)/60*(Hh-24);
 let o='';[0,15,30,45,60].forEach(v=>{o+=`<line x1="${pad}" x2="${W-10}" y1="${ys(v)}" y2="${ys(v)}" stroke="var(--line)"/><text x="${pad-6}" y="${ys(v)+4}" text-anchor="end" font-size="11" fill="#888">${v}%</text>`});
 const line=(key,col)=>{const pts=CO.map((c,i)=>c.sent?[xs(i),ys(pc(c[key],c.sent))]:null);let p='',pen=false;pts.forEach(q=>{if(q){p+=(pen?'L':'M')+q[0]+','+q[1]+' ';pen=true}else pen=false});o+=`<path d="${p}" fill="none" stroke="${col}" stroke-width="2.5"/>`;pts.forEach((q,i)=>{if(q)o+=`<circle cx="${q[0]}" cy="${q[1]}" r="3.5" fill="${col}"><title>week of ${CO[i].d}: ${CO[i][key]}/${CO[i].sent}</title></circle>`})};
 line('replied',C.sent);line('interview',C.interview);
 CO.forEach((c,i)=>{o+=`<text x="${xs(i)}" y="${Hh+16}" text-anchor="middle" font-size="11" fill="#888">${c.d}</text><text x="${xs(i)}" y="${Hh+30}" text-anchor="middle" font-size="10" fill="#aaa">n=${c.sent}</text>`});
 $('trend').setAttribute('viewBox','0 0 920 195');$('trend').innerHTML=o;
 // --- output game
 const lvl=[25,50,100,150,200,300],next=lvl.find(x=>x>S)||(Math.ceil((S+1)/100)*100),prev=lvl.filter(x=>x<=S).pop()||0;
 const wk=M.sent_7d,wp=Math.min(100,Math.round(100*wk/goal));
 $('game').innerHTML=[['Total sent',S,C.sent],['Active streak (days)',M.streak_days,C.offer],['Best 7 days',M.best_week,C.interview],['Days with an application',M.active_days,C.task],['Next milestone',next+' ('+(next-S)+' to go)',C.reviewed]].map(x=>`<div class="card" style="--c:${x[2]}"><b>${x[1]}</b><span>${x[0]}</span></div>`).join('');
 $('goalbar').innerHTML=`<div class="st" style="grid-template-columns:150px 1fr 120px;margin-top:8px"><span>This week <b>${wk}</b> / ${goal}</span><div class="bar"><i style="--c:${wk>=goal?'#30a46c':'#f5a524'};width:${wp}%"></i></div><em>${wp}%</em></div><div class="st" style="grid-template-columns:150px 1fr 120px;margin-top:6px"><span>To ${next} applications</span><div class="bar"><i style="--c:${C.sent};width:${Math.round(100*(S-prev)/(next-prev))}%"></i></div><em>${S}/${next}</em></div>`;
 $('gamenote').textContent=perOffer?`At your current rates you need roughly ${perOffer} applications per offer; you are at ${S}. Output is the lever you control: keep the weekly goal, keep them targeted.`:'Keep the weekly goal; rates need a few more interviews to project.';
}

function extras3(){
 const M=D.metrics,P=M.predictors,G=M.gigs;
 const rows=(t,a)=>a.filter(x=>x.sent).map(x=>`<tr><td>${t}</td><td>${x.k}</td><td>${x.sent}</td><td>${pc(x.replied,x.sent)}%</td><td>${pc(x.interview,x.sent)}%</td></tr>`).join('');
 $('pred').innerHTML='<tr><th></th><th>Group</th><th>Sent</th><th>Reply</th><th>Interview</th></tr>'+rows('Channel',P.channel)+rows('Fit score',P.score)+rows('Speed to apply',P.speed);
 $('gigs').innerHTML=`<div class="mut" style="margin-bottom:6px">${G.sent} applied, ${G.interview} reached an interview, ${G.offer} got an offer, ${G.failed} failed onboarding, ${G.closed} project closed, ${G.rejected} rejected. Gigs are paid by the hour and kept out of the job-search rates above.</div><table><tr><th>Gig</th><th>Status</th><th>Pay</th></tr>${G.items.map(x=>`<tr><td>${esc(x.t)}</td><td>${esc(x.s)}</td><td>${esc(x.pay)}</td></tr>`).join('')}</table>`;
}
$('nogig').onchange=()=>load($('rg').value);
$('tgt').onclick=e=>{e.preventDefault();const v=parseInt(prompt('Applications per week goal?',localStorage.getItem('jhGoal')||10),10);if(v>0){localStorage.setItem('jhGoal',v);extras()}};
$('all').onchange=sankey;$('rg').oninput=e=>{$('gd').textContent=e.target.value};$('rg').onchange=e=>load(e.target.value);
load(14);setInterval(()=>load($('rg').value),60000);
</script></body></html>"""


LOGIN = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Job Hunt - sign in</title>
<style>body{font:15px -apple-system,Segoe UI,sans-serif;background:#f6f7f6;color:#1d2420;display:grid;place-items:center;min-height:100vh;margin:0}@media(prefers-color-scheme:dark){body{background:#141815;color:#e8ece9}}
form{background:#fff;border:1px solid #e2e6e3;border-radius:12px;padding:24px;width:min(340px,90vw)}@media(prefers-color-scheme:dark){form{background:#1c221e;border-color:#2b332e}}
input,button{width:100%;font:inherit;padding:9px 11px;border-radius:8px;border:1px solid #c9d0cb;margin-top:10px;box-sizing:border-box}button{background:#1f5c3a;color:#fff;border-color:#1f5c3a;cursor:pointer}h1{font-size:18px;margin:0}.e{color:#b91c1c;font-size:13px;margin-top:8px}</style>
<form method=post action=/login><h1>Job Hunt</h1><input type=password name=password placeholder=Password autofocus autocomplete=current-password><button>Sign in</button>%ERR%</form>"""


class H(BaseHTTPRequestHandler):
    server_version = "JobHunt"

    def log_message(self, *a):
        pass

    # ---- request classification
    def remote(self):
        h = self.headers
        host = h.get("Host", "").split(":")[0].lower()
        return host not in ("127.0.0.1", "localhost") or any(h.get(x) for x in ("Cf-Connecting-Ip", "Cf-Ray", "X-Forwarded-For", "X-Forwarded-Host"))

    def client_ip(self):
        return self.headers.get("Cf-Connecting-Ip") or self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]

    def authed(self):
        if not self.remote():
            return True
        if not PASSWORD:
            return False
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "jh_session" and cookie_ok(v):
                return True
        return False

    def send(self, code, body, ctype="application/json", extra=None):
        b = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or ctype.endswith("json") else ""))
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if self.remote():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        if ctype == "text/html":
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; form-action 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def gate(self):
        """Return True if the request may proceed. Remote requests need an allowed host and a valid session."""
        if not self.remote():
            return True
        host = self.headers.get("Host", "").split(":")[0].lower()
        if host not in ALLOWED_HOSTS:
            self.send(403, {"error": "host not allowed"})
            return False
        return True

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(min(n, 2_000_000)) or b"{}")

    def do_GET(self):
        if not self.gate():
            return
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/login":
            if self.authed():
                return self.send(302, b"", "text/plain", {"Location": "/"})
            return self.send(200, LOGIN.replace("%ERR%", ""), "text/html")
        if u.path == "/logout":
            return self.send(302, b"", "text/plain", {"Location": "/login", "Set-Cookie": "jh_session=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Strict"})
        if not self.authed():
            return self.send(302, b"", "text/plain", {"Location": "/login"}) if u.path == "/" else self.send(401, {"error": "login required"})
        if u.path == "/setup":
            return self.send(200, onboarding.PAGE, "text/html")
        if u.path == "/api/setup/state":
            return self.send(200, onboarding.state())
        if u.path == "/" and not paths.has_profile():
            return self.send(302, b"", "text/plain", {"Location": "/setup"})
        if u.path == "/":
            return self.send(200, PAGE.replace("%STATUSES%", json.dumps(STATUSES)).replace("%CSTATUSES%", json.dumps(contacts.STATUSES)).replace("%EDITABLE%", "".join(f"<option>{n}</option>" for n in EDITABLE)), "text/html")
        if u.path == "/funnel":
            return self.send(200, FUNNEL, "text/html")
        if u.path == "/api/funnel":
            try:
                g = max(3, min(90, int(q.get("ghost", ["14"])[0])))
            except ValueError:
                g = 14
            ex = tuple(x for x in q.get("ex", [""])[0].split(",") if x)
            return self.send(200, funnel_payload(g, ex))
        if u.path == "/api/jobs":
            return self.send(200, jobs_payload())
        if u.path == "/api/stats":
            return self.send(200, kit.stats())
        if u.path == "/api/kit":
            return self.send(200, kit.kit(q.get("id", [""])[0]))
        if u.path == "/api/status":
            run = PROCS.get("run")
            running = bool(run and run["proc"].poll() is None)
            drafting = any(k.split("-")[0] in ("draft", "prep") and v["proc"].poll() is None for k, v in PROCS.items())
            log = ""
            for k in ["run"] + [k for k in PROCS if k.split("-")[0] in ("draft", "prep")]:
                if k in PROCS and PROCS[k]["log"].exists():
                    log += (f"[{k}]\n" if k != "run" else "") + PROCS[k]["log"].read_text()[-4000:]
            return self.send(200, {"running": running, "drafting": drafting, "log": log[-6000:]})
        if u.path == "/api/file":
            name = q.get("name", [""])[0]
            if name not in EDITABLE:
                return self.send(400, {"error": "bad name"})
            return self.send(200, {"text": (DATA / name).read_text() if (DATA / name).exists() else "{}"})
        if u.path == "/file":
            p = (OUT / unquote(q.get("p", [""])[0])).resolve()
            if OUT.resolve() not in p.parents or not p.is_file() or p.name.startswith("."):
                return self.send(404, {"error": "not found"})
            ct = {".pdf": "application/pdf", ".md": "text/plain", ".html": "text/html"}.get(p.suffix)
            if not ct:
                return self.send(404, {"error": "not found"})
            return self.send(200, p.read_bytes(), ct, {"Content-Security-Policy": "sandbox"} if ct == "text/html" else None)
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.gate():
            return
        u = urlparse(self.path)
        if u.path == "/login":
            ip = self.client_ip()
            fails = FAILS.get(ip, [0, 0])
            if fails[1] > time.time():
                return self.send(429, LOGIN.replace("%ERR%", "<div class=e>Too many attempts. Try again in a few minutes.</div>"), "text/html")
            n = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(min(n, 4096)).decode())
            pw = form.get("password", [""])[0]
            if PASSWORD and hmac.compare_digest(pw.encode(), PASSWORD.encode()):
                FAILS.pop(ip, None)
                return self.send(302, b"", "text/plain", {"Location": "/", "Set-Cookie": f"jh_session={make_cookie()}; Max-Age={30 * 86400}; Path=/; HttpOnly; Secure; SameSite=Strict"})
            fails[0] += 1
            if fails[0] >= 5:
                fails = [0, time.time() + 900]
            FAILS[ip] = fails
            time.sleep(1)
            return self.send(401, LOGIN.replace("%ERR%", "<div class=e>Wrong password.</div>"), "text/html")
        if not self.authed():
            return self.send(401, {"error": "login required"})
        origin = self.headers.get("Origin")
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json" or (origin and urlparse(origin).netloc != self.headers.get("Host")):
            return self.send(403, {"error": "forbidden"})
        d = self.body()
        if u.path.startswith("/api/setup/"):
            try:
                return self.send(200, onboarding.post(u.path, d))
            except Exception as e:
                return self.send(200, {"error": str(e)})
        if u.path == "/api/status" and d.get("status") in STATUSES:
            set_status(d["id"], d["status"], d.get("stage"))
            return self.send(200, {"ok": True})
        if u.path == "/api/contact":
            e = contacts.update(d.get("id", ""), d.get("pid", ""), **{k: d[k] for k in ("status", "name", "note") if k in d})
            if e is None:
                return self.send(404, {"error": "unknown contact"})
            if d.get("status") == "referral given":
                row = next((r for r in read_tracker() if r["id"] == d["id"]), None)
                if row and "via referral" not in (row.get("notes") or "").lower():
                    update_row(d["id"], notes="via referral | " + (row.get("notes") or ""))
            return self.send(200, {"ok": True, "contacts": e})
        if u.path == "/api/draft":
            return self.send(200, {"started": draft(d["id"])})
        if u.path == "/api/prep":
            return self.send(200, {"started": start("prep-" + d["id"], [sys.executable, "prep.py", d["id"]])})
        if u.path == "/api/run":
            return self.send(200, {"started": start("run", [sys.executable, "daily.py"] if d.get("draft") else [sys.executable, "search.py"])})
        if u.path == "/api/manual":
            return self.send(200, add_manual(d))
        if u.path == "/api/followup_sent":
            update_row(d["id"], followup_on=date.today().isoformat())
            return self.send(200, {"ok": True})
        if u.path == "/api/reveal":
            if self.remote():
                return self.send(403, {"error": "only available on the Mac itself"})
            r = next((x for x in read_tracker() if x["id"] == d["id"]), None)
            if r and (kit.app_dir(r)).exists():
                subprocess.run(["open", str(kit.app_dir(r))])
                return self.send(200, {"ok": True})
            return self.send(200, {"error": "no files yet"})
        if u.path == "/api/file":
            if d.get("name") not in EDITABLE:
                return self.send(400, {"error": "bad name"})
            try:
                json.loads(d["text"])
            except Exception as e:
                return self.send(200, {"error": f"invalid JSON: {e}"})
            (DATA / d["name"]).write_text(d["text"])
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


def main():
    port = int(os.environ.get("PORT", 8765))
    srv = None
    for p in range(port, port + 10):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), H)
            port = p
            break
        except OSError:
            continue
    if not srv:
        sys.exit("no free port")
    url = f"http://127.0.0.1:{port}"
    print("Job Hunt dashboard:", url, "(Ctrl+C to stop)", flush=True)
    if "--no-open" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
