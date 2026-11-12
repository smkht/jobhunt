#!/usr/bin/env python3
"""Apply kit, follow-ups, referrals and funnel stats. Pure functions over tracker.csv and the data files; no network."""
import csv, json, re
from datetime import date, datetime, timedelta
import paths

OUT = paths.OUT
FOLLOWUP_AFTER_DAYS = 7
REPLIED = ("task", "interview", "offer", "onboarding_failed", "project_closed", "rejected")


def load(name, default):
    return paths.read_json(name, default)


def read_tracker():
    import search
    search.ensure_tracker()
    return list(csv.DictReader(search.TRACKER.open()))


def _who():
    """(full name, first name, contact line) from the profile."""
    p = paths.load_profile()
    return p.get("name") or "", paths.first_name(), " | ".join(p.get("contact", []))


def app_dir(r):
    import tailor
    return tailor.app_dir(r)


def draft_of(r):
    f = app_dir(r) / "draft.json"
    return json.loads(f.read_text()) if f.exists() else None


def referral(company):
    c = (company or "").lower()
    for p in load("network.json", {}).get("contacts", []):
        if not isinstance(p, dict) or not p.get("contact"):
            continue
        words = [str(m).lower().strip() for m in (p.get("match") or [p.get("company", "")]) if str(m).strip()]   # 'match' = company words; falls back to 'company'
        if c and any(m in c for m in words):
            p.setdefault("relation", "")
            return p
    return None


def outreach(r, contact):
    return (f"Hi {contact['contact'].split()[0]},\n\nI hope you are well. I am applying for the {r['title']} role at {r['company']} "
            f"and would be grateful if you could refer me or point me to the right person. I can send my CV and a short note on why I fit the role.\n\n"
            f"Thank you,\n{_who()[1]}")


def answers_for(r, d):
    why = (d or {}).get("why_us") or ""
    summ = (d or {}).get("summary", "")
    out = []
    for it in load("answers.json", {}).get("items", []):
        a = it["a"].replace("{company}", r["company"]).replace("{role}", r["title"]).replace("{why_us}", why).replace("{summary}", summ)
        if a.strip().upper().startswith("TODO"):
            a = ""  # no filler text in the kit: an empty orange block means "you fill this in"
        need = it.get("needs_input", False) or (not a.strip() and "{why_us}" in it["a"])  # no draft yet: ask the person instead of showing filler text
        out.append({"q": it["q"].replace("{company}", r["company"]), "a": a, "needs_input": need})
    return out


def followup_text(r):
    name, _, contact = _who()
    return (f"Subject: Following up - {r['title']}\n\nHello,\n\nI applied for the {r['title']} position at {r['company']} on {r.get('applied_on') or 'recently'} "
            f"and wanted to confirm that my application was received. I remain very interested in the role and would be glad to share more about how my experience "
            f"fits the team's needs.\n\nThank you for your time,\n{name}\n{contact}".rstrip())


def due_followups(rows=None):
    today = date.today()
    out = []
    for r in rows if rows is not None else read_tracker():
        if r["status"] != "applied" or not r.get("applied_on") or r.get("followup_on"):
            continue
        try:
            age = (today - datetime.strptime(r["applied_on"], "%Y-%m-%d").date()).days
        except ValueError:
            continue
        if age >= FOLLOWUP_AFTER_DAYS:
            out.append({**r, "age": age})
    return out


def linkedin_note(r):
    """Connection / InMail note, kept under LinkedIn's 300-character limit."""
    t = (f"Hi, I'm applying for the {r['title'][:60]} role at {r['company'][:40]}. "
         f"Could you point me to the hiring manager or share who reviews applications? Thanks, {_who()[1]}")
    return t[:299]


def _other_language(text):
    import fit
    fit.sync()
    langs = [x for x in fit.LANG_UNIVERSE if x not in fit.SPOKEN]
    return bool(langs) and bool(re.search(r"\b(" + "|".join(langs) + r")\b", text, re.I))


def kit(jid):
    r = next((x for x in read_tracker() if x["id"] == jid), None)
    if not r:
        return {"error": "unknown job"}
    d, ad = draft_of(r), app_dir(r)
    letter = (ad / "cover_letter.md").read_text() if (ad / "cover_letter.md").exists() else ""
    notes = (ad / "notes.md").read_text() if (ad / "notes.md").exists() else ""
    files = {k: str((ad / n).relative_to(OUT)) for k, n in [("cv", f"{paths.file_prefix()}_CV.pdf"), ("letter", f"{paths.file_prefix()}_Cover_Letter.pdf"), ("notes", "notes.md"), ("prep", "interview_prep.md")] if (ad / n).exists()}
    ref = referral(r["company"])
    checklist = [
        {"t": "CV and cover letter reviewed by you", "ok": bool(files.get("cv"))},
        {"t": "Posting still open (check the link)", "ok": None},
        {"t": "Answers marked 'needs input' filled in", "ok": not any(a["needs_input"] for a in answers_for(r, d)) and False},
        {"t": "No language you do not speak is required (see config languages_spoken)", "ok": not _other_language(notes + letter) or None},
    ]
    import ats
    direct = bool(r.get("apply_url")) and "linkedin.com" not in r.get("apply_url", "")
    seen = [
        {"t": ("Apply on the company's own page (not LinkedIn Easy Apply): " + r["apply_url"]) if direct else "Direct company page NOT found yet: use the search links below, then apply there", "ok": direct},
        {"t": "Apply within 24-48 h of the posting (fresh applications get read first)", "ok": None},
        {"t": "Message a recruiter or the hiring manager (links and note below)", "ok": None},
        {"t": "Ask your referral contact" if ref else "No referral contact known for this company (add one in data/network.json if you have one)", "ok": None},
        {"t": "Follow up after 7 days (the kit prepares the text)", "ok": None},
    ]
    tmpl = [{"id": t["id"], "use": t["use"], "text": t["text"].replace("{company}", r["company"]).replace("{role}", r["title"])} for t in load("outreach_templates.json", {}).get("templates", [])]
    return {"job": r, "templates": tmpl, "fit": r.get("fit", ""), "apply_url": r.get("apply_url", ""), "apply_via": r.get("apply_via", ""), "direct": direct,
            "search_links": ats.search_links(r["company"], r["title"]), "linkedin_note": linkedin_note(r), "seen": seen, "files": files, "letter": letter, "notes": notes, "answers": answers_for(r, d), "followup": followup_text(r),
            "referral": ({**ref, "message": outreach(r, ref)} if ref else None), "checklist": checklist, "has_draft": bool(d)}


def stats(rows=None):
    rows = rows if rows is not None else read_tracker()
    by = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    applied = [r for r in rows if r.get("applied_on")]
    today = date.today()
    wk = [r for r in applied if (today - datetime.strptime(r["applied_on"], "%Y-%m-%d").date()).days < 7]
    mature = [r for r in applied if (today - datetime.strptime(r["applied_on"], "%Y-%m-%d").date()).days >= 7]
    replied = [r for r in applied if r["status"] in REPLIED]
    src = {}
    for r in applied:
        s = src.setdefault(r["source"], {"applied": 0, "replied": 0})
        s["applied"] += 1
        s["replied"] += r["status"] in REPLIED
    return {"by_status": by, "applied_total": len(applied), "applied_7d": len(wk), "replies": len(replied),
            "response_rate": round(100 * len([r for r in mature if r["status"] in REPLIED]) / len(mature)) if mature else None,
            "by_source": src, "followups_due": len(due_followups(rows))}


if __name__ == "__main__":
    import sys
    print(json.dumps(stats() if len(sys.argv) < 2 else kit(sys.argv[1]), indent=1, ensure_ascii=False)[:3000])
