#!/usr/bin/env python3
"""Salary range for every posting.
 1. If the posting states a range, it is parsed and labelled 'stated'.
 2. Otherwise, IF you configured salary bands, an INDICATIVE estimate from the role title, labelled 'estimate'. With no bands the estimate is skipped.
Config keys (DATA/config.json): "salary_bands": [[title regex, low, high, label], ...] (first match wins, values in thousands per year),
"salary_default": [low, high, label], "salary_currency": "EUR" / "USD" / "GBP" (default EUR), "salary_premium_regex": "employer|names" (+15-20% for these payers),
"salary_region_note": "Spain gross" (text shown in the estimate basis).
Run `python3 salary.py` to fill every row in tracker.csv that has no salary yet."""
import csv, json, re
import paths

CFG = paths.load_config()
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def _bands():
    return [(rx, a, b, c) for rx, a, b, c in (CFG.get("salary_bands") or [])]


def _premium():
    rx = CFG.get("salary_premium_regex")
    return re.compile(rx, re.I) if rx else None


CUR = r"(?:€|eur|euros?|\$|usd|£|gbp)"
NUM = r"\d{2,3}(?:[.,]\d{3})?\s?k?"
RANGE = re.compile(rf"(?P<c1>{CUR})?\s?(?P<n1>{NUM})\s?(?P<c2>{CUR})?\s?(?:-|–|—|to|and|a)\s?(?P<c3>{CUR})?\s?(?P<n2>{NUM})\s?(?P<c4>{CUR})?", re.I)
HINT = re.compile(r"salary|compensation|base pay|pay range|annual|per year|gross|remuneraci|salario|\bp\.?a\.?\b|ote|eur\b|usd\b|€|\$|£", re.I)


def _num(s):
    s = s.strip().lower()
    k = s.endswith("k")
    s = s.rstrip("k").strip().replace(",", "").replace(".", "")
    try:
        v = int(s)
    except ValueError:
        return None
    return v * 1000 if (k or v < 1000) else v


def stated(desc):
    """First plausible salary range in the posting text -> '€54,000-79,200 (stated)' or None."""
    for m in RANGE.finditer(desc or ""):
        ctx = (desc or "")[max(0, m.start() - 80): m.end() + 40]
        cur = (m.group("c1") or m.group("c2") or m.group("c3") or m.group("c4") or "").lower()
        if not cur or not HINT.search(ctx):
            continue
        lo, hi = _num(m.group("n1")), _num(m.group("n2"))
        if not lo or not hi or lo >= hi or hi < 15000 or hi > 400000 or re.search(r"years|employees|countries|%|users|customers", ctx[ctx.lower().find(m.group("n1").lower()):][:60], re.I):
            continue
        sym = "€" if cur in ("€", "eur", "euro", "euros") else "$" if cur in ("$", "usd") else "£"
        return f"{sym}{lo:,}-{hi:,}/yr", "stated in posting"
    return None


def estimate(title, company="", location=""):
    """Indicative range from the configured bands; ('', '') when no bands are configured."""
    bands = _bands()
    if not bands:
        return "", ""
    t = (title or "").lower()
    d = CFG.get("salary_default") or [None, None, "default"]
    lo, hi, label = next(((a, b, c) for rx, a, b, c in bands if re.search(rx, t)), tuple(d))
    if lo is None:
        return "", ""
    prem = _premium()
    is_prem = bool(prem and prem.search(company or ""))
    if is_prem:
        lo, hi = round(lo * 1.15), round(hi * 1.2)
    sym = SYMBOLS.get((CFG.get("salary_currency") or "EUR").upper(), "€")
    note = CFG.get("salary_region_note") or "gross"
    return f"{sym}{lo}-{hi}k/yr", f"estimate: {label}, {note}{', premium employer' if is_prem else ''}"


def salary_for(title, company, location, desc):
    return stated(desc) or estimate(title, company, location)


def backfill():
    import search as S
    S.ensure_tracker()
    p = S.TRACKER
    rows = list(csv.DictReader(p.open()))
    n = 0
    for r in rows:
        if r.get("salary"):
            continue
        f = paths.OUT / "jobs" / f"{r['id']}.json"
        desc = json.loads(f.read_text()).get("desc", "") if f.exists() else ""
        r["salary"], r["salary_basis"] = salary_for(r["title"], r["company"], r["location"], desc)
        n += 1
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, S.FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"salary filled for {n} rows")


if __name__ == "__main__":
    backfill()
