#!/usr/bin/env python3
"""Fetch jobs from free sources, filter + score them, add new ones to tracker.csv, write out/digest.md."""
import ats, fit, salary, paths
import os, csv, hashlib, html, json, re, sys, time, urllib.parse, urllib.request, concurrent.futures as cf
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = paths.ROOT
CFG = paths.load_config()
TRACKER = paths.DATA / "tracker.csv"
FIELDS = ["id", "found", "score", "status", "title", "company", "location", "source", "posted", "url", "notes", "applied_on", "followup_on", "fit", "apply_url", "apply_via", "salary", "salary_basis"]
UA = {"User-Agent": "Mozilla/5.0"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def strip(s):
    s = html.unescape(s or "")  # some APIs (Greenhouse) return entity-encoded HTML: decode first, then remove tags
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def iso(s):
    try:
        if str(s).isdigit():
            n = int(s)
            return datetime.fromtimestamp(n / 1000 if n > 1e11 else n, timezone.utc)
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ---- sources: each returns list of dicts {title, company, location, url, desc, posted(datetime|None), source}
def remotive():
    out = []
    for q in ["project manager", "operations", "program manager", "coordinator", "delivery"]:
        for j in json.loads(get(f"https://remotive.com/api/remote-jobs?search={urllib.request.quote(q)}&limit=100"))["jobs"]:
            out.append(dict(title=j["title"], company=j["company_name"], location=j["candidate_required_location"],
                            url=j["url"], desc=strip(j["description"]), posted=iso(j["publication_date"]), source="remotive"))
    return out


def remoteok():
    out = []
    for j in json.loads(get("https://remoteok.com/api"))[1:]:
        out.append(dict(title=j.get("position", ""), company=j.get("company", ""), location=j.get("location") or "Remote",
                        url=j.get("url", ""), desc=strip(j.get("description")), posted=iso(j.get("date")), source="remoteok"))
    return out


def arbeitnow():
    out = []
    for page in (1, 2, 3):
        for j in json.loads(get(f"https://www.arbeitnow.com/api/job-board-api?page={page}"))["data"]:
            loc = j["location"] + (" (remote)" if j.get("remote") else "")
            out.append(dict(title=j["title"], company=j["company_name"], location=loc, url=j["url"],
                            desc=strip(j["description"]), posted=iso(j["created_at"]), source="arbeitnow"))
    return out


def jobicy():
    out = []
    for tag in ["project", "operations", "manager", "coordinator"]:
        for j in json.loads(get(f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={tag}")).get("jobs", []):
            out.append(dict(title=j["jobTitle"], company=j["companyName"], location=j.get("jobGeo", ""), url=j["url"],
                            desc=strip(j.get("jobDescription")), posted=iso(j["pubDate"]), source="jobicy"))
    return out


def himalayas():
    out = []
    for off in range(0, 200, 20):
        for j in json.loads(get(f"https://himalayas.app/jobs/api?limit=20&offset={off}")).get("jobs", []):
            lr = j.get("locationRestrictions") or []
            out.append(dict(title=j["title"], company=j["companyName"], location=", ".join(lr) or "Worldwide",
                            url=j.get("applicationLink") or j["guid"], desc=strip(j.get("description")),
                            posted=iso(j["pubDate"]), source="himalayas"))
    return out


def wwr():
    out = []
    for cat in ["remote-management-and-finance-jobs", "remote-product-jobs", "remote-customer-support-jobs", "remote-all-other-jobs"]:
        try:
            root = ET.fromstring(get(f"https://weworkremotely.com/categories/{cat}.rss"))
        except Exception:
            continue
        for it in root.iter("item"):
            t = it.findtext("title", "")
            company, _, title = t.partition(": ")
            try:
                posted = datetime.strptime(it.findtext("pubDate", ""), "%a, %d %b %Y %H:%M:%S %z")
            except Exception:
                posted = None
            out.append(dict(title=title or t, company=company, location=it.findtext("region", "") or "Remote",
                            url=it.findtext("link", ""), desc=strip(it.findtext("description")), posted=posted, source="wwr"))
    return out



def pmap(fn, items, workers=16):
    def safe(x):
        try:
            return fn(x)
        except Exception:
            return []
    with cf.ThreadPoolExecutor(workers) as ex:
        return [j for r in ex.map(safe, items) for j in r]


def J(url):
    return json.loads(get(url))


def _rss(url, source, default_loc="Remote"):
    root = ET.fromstring(get(url))
    out = []
    for it in root.iter("item"):
        try:
            posted = datetime.strptime(it.findtext("pubDate", ""), "%a, %d %b %Y %H:%M:%S %z")
        except Exception:
            posted = None
        t = lk_text(it.findtext("title", ""))
        company, _, title = t.partition(": ") if source in ("wwr_all",) else ("", "", t)
        out.append(dict(title=title or t, company=company or lk_text(it.findtext("{http://purl.org/dc/elements/1.1/}creator", "")),
                        location=it.findtext("region", "") or it.findtext("location", "") or default_loc, url=it.findtext("link", ""),
                        desc=strip(it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded", "") or it.findtext("description", "")),
                        posted=posted, source=source))
    return out


def wwr_all():
    """We Work Remotely: the full all-jobs feed plus every category feed (the older wwr() reads only four)."""
    out = _rss("https://weworkremotely.com/remote-jobs.rss", "wwr_all")
    for cat in ["remote-sales-and-marketing-jobs", "remote-business-and-management-jobs", "remote-executive-jobs", "remote-customer-support-jobs",
                "remote-management-and-finance-jobs", "remote-product-jobs", "remote-all-other-jobs", "remote-programming-jobs"]:
        try:
            out += _rss(f"https://weworkremotely.com/categories/{cat}.rss", "wwr_all")
        except Exception:
            pass
    return out


def remotive_all():
    return _rss("https://remotive.com/remote-jobs/feed", "remotive_all", "Worldwide")


def authenticjobs():
    return _rss("https://authenticjobs.com/feed/", "authenticjobs")


def skipthedrive():
    """SkipTheDrive: category listing pages (title, company, 'N days ago'); no API/RSS exists."""
    cats = ["remote-project-manager-jobs", "remote-account-manager-jobs", "remote-customer-service-jobs", "remote-administrative-jobs",
            "remote-sales-jobs", "remote-marketing-jobs", "remote-operations-jobs", "remote-business-development-jobs", "remote-legal-jobs",
            "remote-human-resources-jobs", "remote-part-time-jobs", "remote-data-entry-jobs", "remote-virtual-assistant-jobs"]

    def one(cat):
        out = []
        for pg in (1, 2, 3):
            try:
                h = get(f"https://www.skipthedrive.com/job-category/{cat}/" + (f"page/{pg}/" if pg > 1 else "")).decode("utf-8", "ignore")
            except Exception:
                break
            blocks = re.findall(r'<h2 class="post-title entry-title"><a href="([^"]+)">([^<]+)</a></h2>(.*?)<hr/>', h, re.S)
            if not blocks:
                break
            for url, title, rest in blocks:
                co = re.search(r"custom_fields_company_name_display_search_results'>(?:&nbsp;)?([^<]*)", rest)
                ago = re.search(r"&nbsp;(\d+\+?)\s*(hour|day|week|month)", rest)
                posted = None
                if ago:
                    n = int(ago.group(1).rstrip("+")); u = ago.group(2)
                    posted = datetime.now(timezone.utc) - timedelta(days={"hour": 0, "day": n, "week": 7 * n, "month": 30 * n}[u] if u != "hour" else 0)
                out.append(dict(title=lk_text(title), company=(co.group(1).strip() if co else ""), location="Remote", url=url,
                                desc=strip(re.sub(r"<[^>]+>", " ", rest)), posted=posted, source="skipthedrive"))
        return out
    return pmap(one, cats, workers=6)


def greenhouse():
    def one(slug):
        out = []
        for j in J(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")["jobs"]:
            out.append(dict(title=j["title"], company=j.get("company_name") or slug, location=(j.get("location") or {}).get("name", ""),
                            url=j["absolute_url"], desc="", posted=iso(j.get("first_published") or j.get("updated_at")), source="greenhouse",
                            detail=f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{j['id']}"))
        return out
    return pmap(one, CFG["companies"]["greenhouse"])


def lever():
    def one(slug):
        return [dict(title=j["text"], company=slug, location=(j.get("categories") or {}).get("location", "") + " " + (j.get("workplaceType") or ""),
                     url=j["hostedUrl"], desc=j.get("descriptionPlain", ""), posted=iso(j.get("createdAt")), source="lever")
                for j in J(f"https://api.lever.co/v0/postings/{slug}?mode=json")]
    return pmap(one, CFG["companies"]["lever"])


def ashby():
    def one(slug):
        out = []
        for j in J(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")["jobs"]:
            loc = j.get("location", "") + (" remote" if j.get("isRemote") else "")
            out.append(dict(title=j["title"], company=slug, location=loc, url=j["jobUrl"], desc=j.get("descriptionPlain", ""),
                            posted=iso(j.get("publishedAt")), source="ashby"))
        return out
    return pmap(one, CFG["companies"]["ashby"])


def smartrecruiters():
    def one(slug):
        out = []
        for j in J(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")["content"]:
            l = j.get("location", {})
            loc = ", ".join(x for x in [l.get("city"), l.get("country")] if x) + (" remote" if l.get("remote") else "")
            out.append(dict(title=j["name"], company=j["company"]["name"], location=loc, url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                            desc="", posted=iso(j.get("releasedDate")), source="smartrecruiters", detail=j["ref"]))
        return out
    return pmap(one, CFG["companies"]["smartrecruiters"])


def recruitee():
    def one(slug):
        return [dict(title=j["title"], company=j.get("company_name", slug), location=j.get("location", "") + (" remote" if j.get("remote") else ""),
                     url=j["careers_url"], desc=strip(j.get("description")), posted=iso(j.get("published_at") or j.get("created_at")), source="recruitee")
                for j in J(f"https://{slug}.recruitee.com/api/offers/")["offers"]]
    return pmap(one, CFG["companies"]["recruitee"])


def workable():
    """jobs.workable.com aggregates every company on Workable; search by title x location."""
    def one(q_loc):
        q, loc = q_loc
        out = []
        for j in J(f"https://jobs.workable.com/api/v1/jobs?query={urllib.request.quote(q)}&location={urllib.request.quote(loc)}&limit=20")["jobs"]:
            l = j.get("location") or {}
            locs = ", ".join(x for x in [l.get("city"), l.get("countryName")] if x)
            out.append(dict(title=j["title"], company=(j.get("company") or {}).get("title", ""), location=f"{locs} {j.get('workplace', '')}".strip(),
                            url=j["url"], desc=strip(j.get("description", "") + " " + j.get("requirementsSection", "")), posted=iso(j.get("created")), source="workable"))
        return out
    return pmap(one, [(q, l) for q in CFG["workable_queries"] for l in CFG["workable_locations"]], workers=8)


def remocate():
    """remocate.app (relocation / visa-friendly jobs): crawl every category and country listing (paginated), keep slugs that look like
    manager/ops/delivery roles, then read each page's JobPosting JSON-LD (title, company, location, date, description)."""
    base = "https://www.remocate.app"
    home = get(base + "/").decode("utf-8", "ignore")
    sections = sorted(set(re.findall(r'href="(/(?:job-categories|countries)/[a-z0-9-]+)"', home)))

    def crawl(sec):
        got = set()
        for pg in range(1, 9):
            try:
                page = get(base + sec + (f"?page={pg}" if pg > 1 else "")).decode("utf-8", "ignore")
            except Exception:
                break
            new = set(re.findall(r'href="(/jobs/[^"#?]+)"', page)) - got
            if not new:
                break
            got |= new
        return got
    slugs = set()
    with cf.ThreadPoolExecutor(6) as ex:
        for g in ex.map(lambda x: crawl(x), sections):
            slugs |= g
    want = re.compile(r"manager|operations|project|program|delivery|coordinator|chief|implementation|partnership|onboarding|consultant|lead|specialist|head|analyst|success|business", re.I)
    drop = re.compile("|".join(re.escape(w.replace(" ", "-")) for w in CFG["title_exclude"]), re.I)
    slugs = sorted(x for x in slugs if want.search(x) and not drop.search(x))[:450]
    print(f"  remocate: {len(sections)} listing sections, {len(slugs)} role pages to read")

    def one(path):
        page = get(base + path).decode("utf-8", "ignore")
        d = None
        for blk in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
            x = json.loads(blk)
            for y in (x if isinstance(x, list) else [x]):
                if y.get("@type") == "JobPosting":
                    d = y
        if not d:
            return []
        loc = d.get("jobLocation") or {}
        loc = loc[0] if isinstance(loc, list) and loc else loc
        addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
        place = ", ".join(str(addr.get(k, "")) for k in ("addressLocality", "addressCountry") if addr.get(k))
        if d.get("jobLocationType") == "TELECOMMUTE":
            place += " remote"
        return [dict(title=d["title"], company=(d.get("hiringOrganization") or {}).get("name", ""), location=place, url=base + path,
                     desc=strip(d.get("description", "")), posted=iso(d.get("datePosted")), source="remocate")]
    return pmap(one, slugs, workers=8)


def workingnomads():
    return [dict(title=j["title"], company=j["company_name"], location=j.get("location", ""), url=j["url"], desc=strip(j.get("description")),
                 posted=iso(j["pub_date"]), source="workingnomads") for j in J("https://www.workingnomads.com/api/exposed_jobs/")]


def jobspresso():
    """Jobspresso RSS (curated remote jobs). Location comes after the company in dc:creator."""
    root = ET.fromstring(get("https://jobspresso.co/feed/?post_type=job_listing"))
    ns = {"dc": "http://purl.org/dc/elements/1.1/", "c": "http://purl.org/rss/1.0/modules/content/"}
    out = []
    for it in root.iter("item"):
        creator = it.findtext("dc:creator", "", ns)
        parts = [lk_text(p) for p in re.split(r"<br\s*/?>", creator)]
        company = parts[0] if parts else ""
        loc = re.sub(r"^[^\w]*", "", parts[1]) if len(parts) > 1 else ""
        try:
            posted = datetime.strptime(it.findtext("pubDate"), "%a, %d %b %Y %H:%M:%S %z")
        except Exception:
            posted = None
        out.append(dict(title=lk_text(it.findtext("title", "")), company=company, location=loc or "Remote", url=it.findtext("link", ""),
                        desc=strip(it.findtext("c:encoded", "", ns) or it.findtext("description", "")), posted=posted, source="jobspresso"))
    return out


def lk_text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def posting_text(url):
    """Full posting text from a direct link: Greenhouse / Ashby / Lever / Workable / SmartRecruiters / Workday / Oracle HCM APIs, else the page text. '' if unavailable."""
    try:
        u = urllib.parse.urlparse(url)
        parts = [p for p in u.path.split("/") if p]
        host = u.netloc.lower()
        if "greenhouse.io" in host and "jobs" in parts:
            board, jid_ = parts[0], parts[parts.index("jobs") + 1]
            d = json.loads(get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid_}"))
            return strip(d.get("content", ""))
        if "ashbyhq.com" in host and len(parts) >= 2:
            d = json.loads(get(f"https://api.ashbyhq.com/posting-api/job-board/{parts[0]}"))
            for j in d.get("jobs", []):
                if parts[1] in j.get("jobUrl", "") or parts[1] == j.get("id"):
                    return j.get("descriptionPlain") or strip(j.get("descriptionHtml", ""))
            return ""
        if "lever.co" in host and len(parts) >= 2:
            d = json.loads(get(f"https://api.lever.co/v0/postings/{parts[0]}/{parts[1]}"))
            return d.get("descriptionPlain", "") + " " + " ".join(l.get("text", "") + " " + strip(l.get("content", "")) for l in d.get("lists", []))
        if host.endswith("workable.com") and "j" in parts:
            acct = parts[0] if parts[0] != "j" else None
            sc = parts[parts.index("j") + 1]
            if acct:
                d = json.loads(get(f"https://apply.workable.com/api/v2/accounts/{acct}/jobs/{sc}"))
                return strip(" ".join(str(d.get(k, "")) for k in ("description", "requirements", "benefits")))
        if "smartrecruiters.com" in host and len(parts) >= 2:
            d = json.loads(get(f"https://api.smartrecruiters.com/v1/companies/{parts[0]}/postings/{parts[1].split('-')[0]}"))
            sec = d.get("jobAd", {}).get("sections", {})
            return strip(" ".join(v.get("text", "") for v in sec.values()))
        if "myworkdayjobs.com" in host:
            m = re.match(r"(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/(job/.+)", u.path.lstrip("/"))
            if m:
                tenant = host.split(".")[0]
                d = json.loads(get(f"https://{host}/wday/cxs/{tenant}/{m.group(1)}/{m.group(2)}"))
                return strip(d.get("jobPostingInfo", {}).get("jobDescription", ""))
        if "/hcmUI/CandidateExperience/" in u.path:  # Oracle HCM careers sites (e.g. Dell)
            jid_ = re.search(r"/job/(\d+)", u.path).group(1)
            d = json.loads(get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;Id=%22{jid_}%22,siteNumber=CX_1"))["items"][0]
            return lk_text(" ".join(str(d.get(k) or "") for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")))
        return lk_text(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", get(url).decode("utf-8", "ignore"), flags=re.S))[:7000]
    except Exception:
        return ""


AGGREGATORS = re.compile(r"linkedin\.|indeed\.|glassdoor\.|jooble|jobrapido|talent\.com|adzuna|ziprecruiter|infojobs|monster\.|simplyhired|learn4good|jobleads|careerjet|whatjobs|jobtome|bebee|trabajos\.|tecnoempleo|jobsora|joblift", re.I)


def full_posting(j, cache):
    """The original full posting for a listing whose text came back short/cut off (LinkedIn trims long ones).
    1) company ATS match by name+title, 2) web search for the exact title on the company's own site. -> (text, url) or ('', '')"""
    want = set(re.findall(r"[a-z0-9]{4,}", (j["title"] + " " + j["company"]).lower()))

    def good(t, url):
        words = set(re.findall(r"[a-z0-9]{4,}", t[:3000].lower()))
        return len(t) > max(1500, len(j["desc"]) + 400) and len(want & words) >= max(2, len(want) // 2)
    try:
        r = ats.find_direct(j["company"], j["title"], j.get("location", ""), cache)
        if r.get("url"):
            t = posting_text(r["url"])
            if good(t, r["url"]):
                return t, r["url"]
    except Exception:
        pass
    try:
        q = urllib.parse.quote(f'"{j["title"]}" {j["company"]} careers')
        h = get("https://lite.duckduckgo.com/lite/?q=" + q).decode("utf-8", "ignore")
        urls = [urllib.parse.unquote(m) for m in re.findall(r'uddg=([^&"]+)', h)]
        tried = 0
        for url in urls:
            if AGGREGATORS.search(url) or tried >= 6:
                continue
            tried += 1
            t = posting_text(url)
            if good(t, url):
                return t, url
    except Exception:
        pass
    return "", ""


def generalist():
    """generalist.world/jobs: curated board for generalists (founders associate, chief of staff, strategy & ops...). Direct apply links."""
    page = get("https://generalist.world/jobs/").decode("utf-8", "ignore")
    cards = []
    for m in re.finditer(r'<a class="gw-job-card"[^>]*href="(/jobs/[^"]+)">(.*?)</a>', page, re.S):
        body = m.group(2)
        f = lambda cls: lk_text((re.search(rf'class="{cls}">(.*?)</', body, re.S) or [None, ""])[1])
        company, title = f("gw-job-company"), f("gw-job-title")
        if not title or company.lower() == "generalist world":
            continue
        loc = lk_text((re.search(r'gw-location">(.*?)</', body, re.S) or [None, ""])[1])
        sal = lk_text((re.search(r'gw-salary">(.*?)</', body, re.S) or [None, ""])[1])
        tags = [lk_text(t) for t in re.findall(r'gw-job-meta-tag">(.*?)</span>', body, re.S)]
        cards.append(dict(path=m.group(1), company=company, title=title, location=loc, salary=sal, summary=f("gw-job-description"), tags=tags))

    def one(c):
        try:
            h = get("https://generalist.world" + c["path"]).decode("utf-8", "ignore")
        except Exception:
            return None
        ap = re.search(r'href="([^"]+)"[^>]*>\s*Apply Now', h)
        url = html.unescape(ap.group(1)) if ap else "https://generalist.world" + c["path"]
        full = posting_text(url) if ap else ""
        desc = " ".join(x for x in [c["summary"], ", ".join(c["tags"]), c["salary"], full] if x)
        return dict(title=c["title"], company=c["company"], location=c["location"] or "", url=url, desc=desc, posted=None, source="generalist")

    with cf.ThreadPoolExecutor(8) as ex:
        return [j for j in ex.map(one, cards) if j]




def linkedin():
    """LinkedIn public (guest) job-search pages: no login, no account involved. Paced politely; stops on any non-200."""
    out, secs = [], (lookback_days() if os.environ.get("LI_ONEOFF") else li_window_days()) * 86400
    oneoff = os.environ.get("LI_ONEOFF")  # name of a config list: run all of it once, leave the rotation cursor alone
    allq = CFG[oneoff] if oneoff else CFG["linkedin_searches"]
    if not allq:
        return out
    per = len(allq) if oneoff else CFG.get("linkedin_per_run", 45)
    cur = paths.OUT / "li_cursor.txt"
    start_i = int(cur.read_text()) if cur.exists() and cur.read_text().strip().isdigit() else 0
    batch = [allq[(start_i + k) % len(allq)] for k in range(min(per, len(allq)))]
    cur.parent.mkdir(parents=True, exist_ok=True)
    if not oneoff:
        cur.write_text(str((start_i + per) % len(allq)))
    print(f"  linkedin: running queries {start_i}-{start_i + len(batch)} of {len(allq)} this run")
    for s in batch:
        for start in (0, 10):
            q = urllib.parse.urlencode({"keywords": s["keywords"], "location": s["location"], "f_TPR": f"r{secs}", "sortBy": "DD", "start": start, **({"f_WT": "2"} if s.get("remote") else {}), **({"f_E": "1,2"} if s.get("entry") else {})})
            page = None
            for wait in (0, 20, 45):  # back off on 429, then give up for this run
                time.sleep(wait)
                try:
                    page = get("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?" + q).decode("utf-8", "ignore")
                    break
                except Exception as e:
                    err = e
            if page is None:
                print(f"  linkedin stopped at '{s['keywords']}' ({err})")
                return out
            chunks = page.split('data-entity-urn="urn:li:jobPosting:')[1:]
            for c in chunks:
                m = lambda p: lk_text((re.search(p, c, re.S) or [None, ""])[1])
                jid_ = re.match(r"\d+", c).group(0)
                t = re.search(r'<time[^>]*datetime="([^"]+)"', c)
                out.append(dict(title=m(r"base-search-card__title[^>]*>(.*?)</h3>"), company=m(r"base-search-card__subtitle[^>]*>(.*?)</h4>"),
                                location=m(r"job-search-card__location[^>]*>(.*?)</span>"), url=f"https://www.linkedin.com/jobs/view/{jid_}/",
                                desc="", posted=iso(t.group(1)) if t else None, source="linkedin", detail=f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid_}", open_loc=bool(s.get("open_location"))))
            time.sleep(1.5)
            if len(chunks) < 10:
                break
    return out


def workday():
    """Large employers on Workday: POST the public CXS search endpoint, then read each job's detail page."""
    def one(c):
        out, seen = [], set()
        for q in CFG["workday_queries"]:
            body = json.dumps({"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": (q + " " + (CFG.get("home_city") or "")).strip()}).encode()
            req = urllib.request.Request(f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}/jobs", body, {"Content-Type": "application/json", **UA})
            for j in json.loads(urllib.request.urlopen(req, timeout=30).read()).get("jobPostings", []):
                path = j.get("externalPath", "")
                if path in seen:
                    continue
                seen.add(path)
                days = re.search(r"(\d+)\+? Days? Ago", j.get("postedOn", ""))
                age = 0 if "Today" in j.get("postedOn", "") else (int(days.group(1)) if days else (1 if "Yesterday" in j.get("postedOn", "") else 30))
                loc = j.get("locationsText", "")
                if not any(w in loc.lower() for w in CFG["workday_location_words"]):
                    continue
                out.append(dict(title=j["title"], company=c["name"], location=loc, url=f"https://{c['host']}/{c['site']}{path}", desc="",
                                posted=datetime.now(timezone.utc) - timedelta(days=age), source="workday",
                                detail=f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}{path}"))
        return out
    return pmap(one, CFG["workday"], workers=6)


def strip_keep_lines(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(s or "")))
    return re.sub(r"[ \t]+", " ", s).strip()


def amazon():
    """Amazon.jobs public JSON (direct company application page). Locations from config 'amazon_locations' (+ 'amazon_country' code, e.g. "ESP"); off when unset."""
    out, seen = [], set()
    for loc in CFG.get("amazon_locations", []):
        for off in (0, 100, 200):
            try:
                d = json.loads(get("https://www.amazon.jobs/en/search.json?" + urllib.parse.urlencode({"base_query": "", "loc_query": loc, "country": CFG.get("amazon_country", ""), "result_limit": 100, "offset": off, "sort": "recent"})))
            except Exception:
                break
            for j in d.get("jobs", []):
                if j["id_icims"] in seen:
                    continue
                seen.add(j["id_icims"])
                clean = lambda x: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or "")))
                desc = clean(j.get("description")) + " Basic qualifications: " + clean(j.get("basic_qualifications")) + " Preferred qualifications: " + clean(j.get("preferred_qualifications"))
                try:
                    posted = datetime.strptime(re.sub(r"\s+", " ", j["posted_date"]).strip(), "%B %d, %Y").replace(tzinfo=timezone.utc)
                except Exception:
                    posted = None
                out.append(dict(title=j["title"], company="Amazon", location=j.get("normalized_location") or j.get("location", ""), url="https://www.amazon.jobs" + j["job_path"], desc=desc, posted=posted, source="amazon"))
            if len(d.get("jobs", [])) < 100:
                break
    return out

US_REMOTE_MARK = " [US-REMOTE-CONTRACTOR-OK: fully remote big-tech role; open to contractors abroad] " if CFG.get("contractor_ok") else ""


_home = [x.lower() for x in [CFG.get("home_country"), CFG.get("home_city")] + list(CFG.get("home_aliases") or []) + list(CFG.get("region_words") or []) if x]
_us = r"us|usa|u\.s\.|united states|" if CFG.get("contractor_ok") else ""
OPEN_REMOTE_LOC = re.compile(r"\b(" + _us + "".join(re.escape(x) + "|" for x in _home) + r"global|worldwide|anywhere)\b|^remote\s*\(?\s*(remote)?\s*\)?$", re.I)


def google():
    """Google careers (public results page embeds the job JSON). Fully remote roles only (page says 'Remote location'); US-based remote is kept only when config 'contractor_ok' is true."""
    out, seen = [], set()
    def one(q):
        res = []
        for page in (1, 2, 3):
            try:
                h = get("https://www.google.com/about/careers/applications/jobs/results/?" + urllib.parse.urlencode({"q": q, "has_remote": "true", "page": page}))
                h = h if isinstance(h, str) else h.decode("utf-8", "ignore")
                m = re.search(r"key: 'ds:1', hash: '2', data:(.*?), sideChannel", h, re.S)
                rows = json.loads(m.group(1))[0]
            except Exception:
                break
            for e in rows:
                try:
                    if "Remote location" not in ((e[18] or [None, ""])[1] or ""):
                        continue
                    locs = ", ".join(x[0] for x in (e[9] or []))
                    if not OPEN_REMOTE_LOC.search(locs):
                        continue  # remote only inside another country (e.g. Maldives, Brazil): not open to the candidate
                    clean = lambda x: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or "")))
                    desc = clean((e[3] or [None, ""])[1]) + " Minimum qualifications: " + clean((e[4] or [None, ""])[1]) + " Preferred qualifications: " + clean((e[19] or [None, ""])[1]) + US_REMOTE_MARK
                    posted = datetime.fromtimestamp(e[12][0], timezone.utc) if e[12] else None
                    res.append(dict(title=e[1], company=e[7] or "Google", location="Remote (" + locs + ")", url=f"https://www.google.com/about/careers/applications/jobs/results/{e[0]}", desc=desc, posted=posted, source="google"))
                except Exception:
                    continue
            if len(rows) < 20:
                break
        return res
    for r in pmap(one, CFG.get("google_queries", ["operations", "program manager", "business operations", "analyst", "strategy", "procurement", "product manager", "policy"]), workers=4):
        if r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
    return out


def microsoft():
    """Microsoft careers (public PCSX API). Keeps only roles flagged workLocationOption=remote; US-based remote is kept only when config 'contractor_ok' is true."""
    def one(q):
        res = []
        for start in (0, 10, 20, 30, 40, 50, 60, 70, 80, 90):
            try:
                d = J("https://apply.careers.microsoft.com/api/pcsx/search?" + urllib.parse.urlencode({"domain": "microsoft.com", "query": q, "start": start, "filter_workplace": "remote"}))["data"]
            except Exception:
                break
            pos = d.get("positions", [])
            for p in pos:
                if p.get("workLocationOption") != "remote":
                    continue
                try:
                    det = J(f"https://apply.careers.microsoft.com/api/pcsx/position_details?position_id={p['id']}&domain=microsoft.com")["data"]
                    desc = strip(det.get("jobDescription", "")) + US_REMOTE_MARK
                except Exception:
                    desc = US_REMOTE_MARK
                res.append(dict(title=p["name"], company="Microsoft", location="Remote (" + ", ".join(p.get("locations", [])[:3]) + ")", url="https://apply.careers.microsoft.com/careers/job/" + str(p["id"]), desc=desc, posted=datetime.fromtimestamp(p["postedTs"], timezone.utc) if p.get("postedTs") else None, source="microsoft"))
            if len(pos) < 10:
                break
        return res
    out, seen = [], set()
    for r in pmap(one, CFG.get("google_queries", ["operations", "program manager", "analyst", "strategy", "procurement", "product manager"]), workers=4):
        if r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
    return out


def amazon_virtual():
    """Amazon virtual-location (remote) roles anywhere; kept as US-remote-contractor candidates."""
    out, seen = [], set()
    for off in (0, 100, 200, 300):
        try:
            d = json.loads(get("https://www.amazon.jobs/en/search.json?" + urllib.parse.urlencode({"base_query": "", "loc_query": "Virtual Location", "result_limit": 100, "offset": off, "sort": "recent"})))
        except Exception:
            break
        for j in d.get("jobs", []):
            if j["id_icims"] in seen or "virtual" not in (j.get("location", "") + j.get("normalized_location", "")).lower() or not OPEN_REMOTE_LOC.search(j.get("normalized_location", "") + " " + j.get("location", "")):
                continue
            seen.add(j["id_icims"])
            clean = lambda x: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or "")))
            desc = clean(j.get("description")) + " Basic qualifications: " + clean(j.get("basic_qualifications")) + " Preferred qualifications: " + clean(j.get("preferred_qualifications")) + US_REMOTE_MARK
            try:
                posted = datetime.strptime(re.sub(r"\s+", " ", j["posted_date"]).strip(), "%B %d, %Y").replace(tzinfo=timezone.utc)
            except Exception:
                posted = None
            out.append(dict(title=j["title"], company="Amazon", location="Remote (" + (j.get("normalized_location") or j.get("location", "")) + ")", url="https://www.amazon.jobs" + j["job_path"], desc=desc, posted=posted, source="amazon"))
        if len(d.get("jobs", [])) < 100:
            break
    return out


def bigco_workday():
    """Fortune-500 / S&P employers on Workday: remote-flagged postings only. US-based remote is kept only when config 'contractor_ok' is true."""
    def one(c):
        out, seen = [], set()
        for q in CFG["bigco_queries"]:
            for off in (0, 20):
                body = json.dumps({"appliedFacets": {}, "limit": 20, "offset": off, "searchText": q + " remote"}).encode()
                req = urllib.request.Request(f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}/jobs", body, {"Content-Type": "application/json", **UA})
                try:
                    posts = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("jobPostings", [])
                except Exception:
                    break
                for j in posts:
                    path = j.get("externalPath", "")
                    loc = j.get("locationsText", "")
                    if path in seen or not re.search(r"remote|virtual|work from home|home[- ]based", loc + " " + j["title"], re.I) or not OPEN_REMOTE_LOC.search(loc):
                        continue
                    seen.add(path)
                    days = re.search(r"(\d+)\+? Days? Ago", j.get("postedOn", ""))
                    age = 0 if "Today" in j.get("postedOn", "") else (int(days.group(1)) if days else (1 if "Yesterday" in j.get("postedOn", "") else 30))
                    out.append(dict(title=j["title"], company=c["name"], location="Remote (" + loc + ")", url=f"https://{c['host']}/{c['site']}{path}", desc="",
                                    posted=datetime.now(timezone.utc) - timedelta(days=age), source="workday", us_remote=True,
                                    detail=f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}{path}"))
                if len(posts) < 20:
                    break
        return out
    return pmap(one, CFG["bigco_workday"], workers=8)


def comeet():
    """Comeet career sites (public token): Artlist."""
    def one(c):
        out = []
        for j in J(f"https://www.comeet.co/careers-api/2.0/company/{c['uid']}/positions?token={c['token']}&details=true"):
            loc = j.get("location") or {}
            desc = " ".join(strip(d.get("value", "")) for d in (j.get("details") or []))
            out.append(dict(title=j["name"], company=c["name"], location=", ".join(x for x in [loc.get("city"), loc.get("country")] if x) + (" remote" if j.get("workplace_type") == "Remote" else ""),
                            url=j.get("url_active_page") or j["url_comeet_hosted_page"], desc=desc,
                            posted=iso(j.get("time_updated") or j.get("time_created")), source="comeet"))
        return out
    return pmap(one, CFG.get("comeet", []))


def ultipro():
    """UKG/UltiPro job boards (public JSON search): Arclin."""
    def one(c):
        out = []
        body = json.dumps({"opportunitySearch": {"Top": 100, "Skip": 0, "QueryString": "", "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}], "Filters": []}}).encode()
        req = urllib.request.Request(c["base"] + "/JobBoardView/LoadSearchResults", body, {"Content-Type": "application/json", **UA})
        for o in json.loads(urllib.request.urlopen(req, timeout=30).read()).get("opportunities", []):
            l = (o.get("Locations") or [{}])[0].get("Address") or {}
            loc = ", ".join(x for x in [l.get("City"), (l.get("State") or {}).get("Name"), (l.get("Country") or {}).get("Name")] if x)
            out.append(dict(title=o["Title"], company=c["name"], location=(loc + (" remote" if o.get("FullTime") is None and False else "")) or "United States",
                            url=f"{c['base']}/OpportunityDetail?opportunityId={o['Id']}", desc="", posted=iso(o.get("PostedDate")), source="ultipro"))
        return out
    return pmap(one, CFG.get("ultipro", []))


def eures():
    """EU public job mobility portal (EURES): free JSON API, last 3 days. Needs config 'eures_country_code' (e.g. "es"); off when unset."""
    out = []
    cc = (CFG.get("eures_country_code") or "").lower()
    if not cc:
        return out
    qs = CFG.get("eures_queries") or [k for k in list(CFG.get("title_keywords") or {})[:8]]
    for q in qs:
        for page in (1, 2):
            body = {"resultsPerPage": 50, "page": page, "sortSearch": "MOST_RECENT", "keywords": [{"keyword": q, "specificSearchCode": "EVERYWHERE"}],
                    "publicationPeriod": "LAST_THREE_DAYS", "occupationUris": [], "skillUris": [], "requiredExperienceCodes": [], "positionScheduleCodes": [],
                    "sectorCodes": [], "educationAndQualificationLevelCodes": [], "positionOfferingCodes": [], "locationCodes": [cc],
                    "euresFlagCodes": [], "otherBenefitsCodes": [], "requiredLanguages": [], "minNumberPost": None, "sessionId": "jobhunt"}
            req = urllib.request.Request("https://europa.eu/eures/api/jv-searchengine/public/jv-search/search", json.dumps(body).encode(),
                                         {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
            try:
                d = json.loads(urllib.request.urlopen(req, timeout=30).read())
            except Exception:
                break
            for j in d.get("jvs", []):
                if cc.upper() not in (j.get("locationMap") or {}):
                    continue
                emp = j.get("employer") or {}
                out.append(dict(title=j["title"], company=emp.get("name") or "", location=CFG.get("home_country") or cc.upper(), source="eures",
                                url=f"https://europa.eu/eures/portal/jv-se/jv-details/{j['id']}?lang=en",
                                desc=strip(j.get("description")), posted=iso(j.get("creationDate"))))
    return out


SOURCES = [remotive, remoteok, arbeitnow, jobicy, himalayas, wwr, workingnomads, linkedin, workable, remocate, greenhouse, lever, ashby, smartrecruiters, recruitee, workday, amazon, amazon_virtual, google, microsoft, bigco_workday, comeet, ultipro, jobspresso, generalist, eures, wwr_all, remotive_all, skipthedrive]


# ---- filtering / scoring
AVOID_CITIES = [c.lower() for c in CFG.get("avoid_cities", [])]  # cities in the home country the candidate will not work in (config "avoid_cities")
HOME = (CFG.get("home_city") or "").lower()


def location_ok(j):
    loc = (j["location"] or "").lower()
    if j.get("open_loc"):
        return True  # remote-filtered worldwide/Asia/US search: the full-posting fit check decides whether it is open from the candidate's location
    if CFG.get("allow_remote_worldwide") and re.search(r"\b(remote|anywhere|worldwide|distributed|work from home)\b", loc) and not (any(c in loc for c in AVOID_CITIES) and "remote" not in loc):
        return True  # fully remote: the full-posting fit check decides whether it is open from the candidate's location
    if AVOID_CITIES and any(c in loc for c in AVOID_CITIES) and not (HOME and HOME in loc) and "remote" not in loc and "remoto" not in loc:
        return False  # in an avoided city and not remote
    if not loc.strip():
        return True
    if any(c in loc for c in CFG["onsite_ok_cities"]):
        return True
    has_ok = any(re.search(rf"\b{re.escape(k)}\b", loc) for k in CFG["location_ok"])
    has_bad = any(re.search(rf"\b{re.escape(k)}", loc) for k in CFG["location_bad"])
    if has_bad and not re.search(r"worldwide|anywhere|global" + "".join("|" + re.escape(x) for x in _home), loc):
        return False
    # on-site abroad (e.g. "Stuttgart") is not ok unless it explicitly says remote
    if "(remote)" not in loc and not has_ok:
        return False
    return True


_ES = re.compile(r"\b(de|la|el|los|las|para|con|que|una|por|del|nuestro|equipo|experiencia|trabajo|buscamos|gestión|proyectos|persona|requisitos|ofrecemos|funciones)\b", re.I)
_EN = re.compile(r"\b(the|and|with|for|you|our|will|team|experience|work|about|role|your|are|have)\b", re.I)


def is_spanish_text(text):
    """True when the text is written mainly in Spanish (only counted when Spanish is not in config languages_spoken)."""
    if "spanish" in {x.lower() for x in CFG.get("languages_spoken", ["English"])}:
        return False
    w = text[:1500]
    es, en = len(_ES.findall(w)), len(_EN.findall(w))
    return es >= 8 and es > en * 1.2


def _other_language_required(text):
    """True when the posting asks for a language that is not in config languages_spoken (English is always assumed)."""
    fit.sync()
    langs = [x for x in fit.LANG_UNIVERSE if x not in fit.SPOKEN]
    if not langs:
        return False
    lv = "|".join(langs)
    return bool(re.search(rf"\b({lv})[ -]?(speaker|speaking|native|fluent|required)|(native|fluent|fluency)[^.]{{0,20}}\b({lv})\b", text, re.I))


def score(j):
    t = j["title"].lower()
    if any(x in t for x in CFG["title_exclude"]):
        return 0
    s = max([v for k, v in CFG["title_keywords"].items() if k in t] or [0])
    if s == 0:
        return 0
    d = j["desc"].lower()
    s += min(40, sum(v for k, v in CFG["description_keywords"].items() if k in d))
    if any(c in (j["location"] or "").lower() for c in CFG["onsite_ok_cities"]):
        s += 10
    if re.search(r"\b(senior|lead|head|principal)\b", t):
        s += 5
    if _other_language_required(t + " " + d):
        s -= 25  # a language the candidate does not speak is required
    if fit.skills_first(j["desc"]):
        s += 15  # skills-first process: tests and interviews decide, not CV filters
    if any(e in (j["company"] or "").lower() for e in CFG.get("deprioritize_employers", [])):
        s -= 30  # employers to deprioritise (config 'deprioritize_employers')
    if any(w in t for w in CFG.get("domain_penalty_words", [])):
        s -= 35  # technical/industry domains outside the candidate's background
    s -= max([v for k, v in CFG.get("soft_penalty_words", {}).items() if k in t] or [0])  # adjacent fields: possible but weaker fit
    if CFG.get("spanish_required_regex") and re.search(CFG["spanish_required_regex"], d, re.I):
        s -= CFG.get("spanish_required_penalty", 45)  # Spanish stated as required (config spanish_required_regex)
    if is_spanish_text(j["title"] + " " + d) or (not d and is_spanish_text(j["title"] + " " + j["title"])):
        s -= CFG.get("spanish_text_penalty", 60)  # posting written in Spanish -> not an English-speaking environment
    return min(100, s)


def jid(j):
    return hashlib.sha1(f"{j['company'].lower()}|{j['title'].lower()}".encode()).hexdigest()[:8]


def ensure_tracker():
    """Create DATA/tracker.csv with the header on first use."""
    if not TRACKER.exists():
        TRACKER.parent.mkdir(parents=True, exist_ok=True)
        with TRACKER.open("w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writeheader()


class tracker_lock:
    """Cross-process lock for every read-modify-write of tracker.csv (dashboard, scans, validator, daily run)."""
    def __enter__(self):
        import fcntl
        paths.OUT.mkdir(parents=True, exist_ok=True)
        self.f = open(paths.OUT / "tracker.lock", "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        ensure_tracker()
        return self

    def __exit__(self, *a):
        import fcntl
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def load_tracker():
    ensure_tracker()
    if not TRACKER.exists():
        return {}
    with TRACKER.open(newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def lookback_days():
    """Age window for the active search lane.

    The fast lane is deliberately strict: it never admits a posting older than two
    days.  A separate weekly background lane handles older/aggregated listings.
    This prevents a missed fast run from quietly filling the apply queue with stale
    jobs.
    """
    if os.environ.get("LOOKBACK_DAYS"):
        return int(os.environ["LOOKBACK_DAYS"])
    mode = os.environ.get("SEARCH_MODE", "fast").lower()
    cap = CFG.get(f"{mode}_max_age_days", CFG["max_age_days"])
    fresh = CFG.get("fresh_days", 2) if mode == "fast" else cap
    f = paths.OUT / f"last_{mode}_run.txt"
    try:
        gap = (datetime.now(timezone.utc) - datetime.fromisoformat(f.read_text().strip())).total_seconds() / 86400
        return min(cap, max(fresh, int(gap) + 2))
    except Exception:
        return cap


def li_window_days():
    """LinkedIn queries rotate (each one comes up every cycle), so its window must cover a whole cycle or postings fall between visits.
    cycle = queries / per-run / 2 runs a day; plus one day of margin."""
    n, per = len(CFG["linkedin_searches"]), CFG.get("linkedin_per_run", 45)
    return max(lookback_days(), -(-n // per) // 2 + 1 + (1 if (-(-n // per)) % 2 else 0))


def lk_page(url):
    """Fetch one LinkedIn public job page politely, backing off on 429."""
    for wait in (0.8, 8, 25):
        time.sleep(wait)
        try:
            return get(url).decode("utf-8", "ignore")
        except Exception:
            continue
    return ""


def lk_parse(page):
    m = re.search(r"show-more-less-html__markup[^>]*>(.*?)</div>", page, re.S)
    desc = lk_text(re.sub(r"<(br|/p|/li)[^>]*>", " ", m.group(1))) if m else ""
    closed = bool(re.search(r"no longer accepting applications|closed-job|job is no longer available", page, re.I))
    return desc, ats.linkedin_mode(page) if page else "", closed


def resolve_apply(rows, cache):
    """Skip LinkedIn: find each job's direct apply page (company ATS). Others already link to the employer/ATS."""
    def one(r):
        if r.get("apply_url"):
            return
        if r["source"] != "linkedin":
            r["apply_url"], r["apply_via"] = r["url"], f"{r['source']} posting"
            return
        res = ats.find_direct(r["company"], r["title"], r["location"], cache)
        r["apply_url"], r["apply_via"] = res["url"], res["via"]
        if not res["url"]:
            f = paths.OUT / "jobs" / f"{r['id']}.json"
            mode = json.loads(f.read_text()).get("mode", "") if f.exists() else ""
            r["apply_via"] = "unresolved - LinkedIn Easy Apply only (find the company page)" if mode == "easy-apply" else "unresolved - LinkedIn external apply (find the company page)"
    with cf.ThreadPoolExecutor(6) as ex:
        list(ex.map(one, rows))
    n = sum(1 for r in rows if r["apply_url"])
    print(f"  direct apply page found for {n}/{len(rows)} jobs")


def main():
    seen = load_tracker()
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days())
    amazon_cutoff = datetime.now(timezone.utc) - timedelta(days=CFG.get("amazon_max_age_days", 30))  # big-company postings stay open for weeks
    pool, errs = [], []
    mode = os.environ.get("SEARCH_MODE", "fast").lower()
    if mode not in ("fast", "background"):
        raise ValueError("SEARCH_MODE must be 'fast' or 'background'")
    only = {x for x in os.environ.get("ONLY_SOURCES", "").split(",") if x}
    lane_sources = set(CFG.get(f"{mode}_sources", []))
    if not only and not lane_sources:
        lane_sources = {src.__name__ for src in SOURCES}
    print(f"  search lane: {mode} (postings <= {lookback_days()} days; {len(lane_sources)} sources)")
    for src in SOURCES:
        if (only and src.__name__ not in only) or (not only and src.__name__ not in lane_sources):
            continue
        try:
            got = src()
            print(f"  {src.__name__:10} {len(got)} fetched")
            pool += got
        except Exception as e:
            errs.append(f"{src.__name__}: {e}")
            print(f"  {src.__name__:10} FAILED {e}")
    skip_co = [x.lower() for x in CFG.get("skip_companies", [])]  # employers you decided to avoid
    pool = [j for j in pool if not any(x in (j["company"] or "").lower() for x in skip_co)]
    def in_age_window(j):
        # A fresh daily queue must be provably fresh.  Undated listings belong in
        # the weekly review lane, never in an "apply within two days" workflow.
        if j["source"] == "linkedin" and mode == "fast":   # rotating queries: a posting is only seen when its query comes up
            return bool(j["posted"]) and j["posted"] >= datetime.now(timezone.utc) - timedelta(days=li_window_days())
        if mode == "fast":
            return bool(j["posted"]) and j["posted"] >= cutoff
        limit = amazon_cutoff if j["source"] == "amazon" else cutoff
        return not j["posted"] or j["posted"] >= limit

    cand = [j for j in pool if in_age_window(j) and j["url"] and location_ok(j) and (score({**j, "desc": ""}) > 0 or (fit.skills_first(j["desc"]) and re.search(CFG.get("skills_first_title_family", "operations|procure|product|commercial|chief of staff|project|program|partnership|customer|business|strategy|account|vendor|supply"), j["title"], re.I)))]
    print(f"  {len(pool)} fetched -> {len(cand)} candidates after title/location/age filter")

    def fill(j):
        if j.get("detail") and not j["desc"] and j["source"] != "linkedin":
            try:
                d = J(j["detail"])
                j["desc"] = strip(d.get("content") or (d.get("jobPostingInfo") or {}).get("jobDescription") or json.dumps(d.get("jobAd", d))[:6000]) + (US_REMOTE_MARK if j.get("us_remote") else "")
            except Exception:
                pass
        return j
    with cf.ThreadPoolExecutor(6) as ex:
        cand = list(ex.map(fill, cand))
    # LinkedIn detail pages: one at a time with retries (parallel requests get rate-limited and return empty pages)
    for j in cand:
        if j["source"] == "linkedin" and not j["desc"]:
            page = lk_page(j["detail"])
            j["desc"], j["mode"], j["closed"] = lk_parse(page)
    drops = {}
    new, cache = [], ats.load_cache()
    for j in cand:
        i = jid(j)
        if i in seen or any(n["id"] == i for n in new):
            continue
        if j.get("closed"):
            drops["closed"] = drops.get("closed", 0) + 1
            continue
        a = fit.assess(j["title"], j["desc"], location=j.get("location", ""))
        if a["verdict"] in ("too-little-evidence", "no-description") and not a["blockers"] and score({**j, "desc": ""}) > 0:
            txt, durl = full_posting(j, cache)  # open the original posting and read the real requirements
            if txt:
                j["desc"], j["_direct"] = txt, durl
                a = fit.assess(j["title"], j["desc"], location=j.get("location", ""))
                drops["full posting fetched"] = drops.get("full posting fetched", 0) + 1
            elif sum(1 for n in new if "MANUAL CHECK" in n["notes"]) < 10 and score(j) >= CFG["min_score"]:
                a = {"verdict": "ok", "fit": "", "blockers": [], "missing": [], "warnings": [], "met": []}
                j["_note"] = "⚠ MANUAL CHECK: the listing text is incomplete and the original posting could not be opened automatically. Open the original posting and read the requirements before drafting"
        if a["verdict"] != "ok":
            k = a["verdict"] + (": " + a["blockers"][0].split(":")[0].split("(")[0].strip() if a["blockers"] else "")
            drops[k] = drops.get(k, 0) + 1
            continue
        sc = score(j)
        if sc < CFG["min_score"]:
            drops["low title/keyword score"] = drops.get("low title/keyword score", 0) + 1
            continue
        pay, pay_basis = salary.salary_for(j["title"], j["company"], j["location"], j["desc"])
        note = j.get("_note") or fit.note(a)
        cmp = salary.compare(pay)  # pay only re-ranks: at/above your expectation moves up, below is kept and flagged for your review
        if cmp in ("above", "meets"):
            sc = min(100, sc + (10 if cmp == "above" else 5))
        elif cmp == "below":
            sc = max(1, sc - 10)
            note = f"💶 Pay ({'estimated' if pay_basis.startswith('estimate') else 'stated'}) is below your expectation: kept for your review | " + note
        row = dict(id=i, found=datetime.now().strftime("%Y-%m-%d"), score=sc, status="new", title=j["title"],
                   company=j["company"], location=j["location"], source=j["source"],
                   posted=j["posted"].strftime("%Y-%m-%d") if j["posted"] else "", url=j["url"], notes=note, applied_on="", followup_on="", fit=a["fit"], apply_url=j["url"] if j["source"] == "amazon" else j.get("_direct", ""), apply_via={"amazon": "Amazon careers page (direct)"}.get(j["source"], "company posting (original read)" if j.get("_direct") else ""), salary=pay, salary_basis=pay_basis)
        new.append(row)
        (paths.OUT / "jobs").mkdir(parents=True, exist_ok=True)
        (paths.OUT / "jobs" / f"{i}.json").write_text(json.dumps({**row, "desc": j["desc"][:6000], "mode": j.get("mode", "")}, ensure_ascii=False))
    print(f"  {len(new)} kept after full-posting fit check (>= {CFG.get('min_fit', 65)}% and no blockers); dropped: {drops}")
    resolve_apply(new, cache)
    ats.save_cache(cache)
    new.sort(key=lambda r: -int(r["score"]))
    with tracker_lock():  # re-read: you may have changed statuses in the dashboard while this run was working
        with TRACKER.open(newline="") as f:
            disk = list(csv.DictReader(f))
        have = {r["id"] for r in disk if r.get("id")}
        allrows = [r for r in disk if r.get("id")] + [r for r in new if r["id"] not in have]
        tmp = TRACKER.with_suffix(".tmp")
        with tmp.open("w", newline="") as f:
            w = csv.DictWriter(f, FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(allrows)
        tmp.replace(TRACKER)
    lines = [f"# Job digest {datetime.now():%Y-%m-%d}", f"{len(new)} new matches ({len(allrows)} tracked in total)", ""]
    for r in new:
        lines.append(f"- **{r['score']}** [{r['title']}]({r['url']}) — {r['company']} · {r['location']} · {r['posted']} · `{r['id']}`")
    if errs:
        lines += ["", "Source errors: " + "; ".join(errs)]
    (paths.OUT / "digest.md").write_text("\n".join(lines))
    print("\n".join(lines[:30]))


def main_locked():
    """Run main() unless another search is already running (scheduled runs must never overlap)."""
    import os
    lock = paths.OUT / "search.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            os.kill(int(lock.read_text()), 0)
            print("another search is still running; skipping this run")
            return False
        except (ProcessLookupError, ValueError):
            pass
    lock.write_text(str(os.getpid()))
    try:
        main()
        if not os.environ.get("ONLY_SOURCES"):
            now = datetime.now(timezone.utc).isoformat()
            mode = os.environ.get("SEARCH_MODE", "fast").lower()
            (paths.OUT / f"last_{mode}_run.txt").write_text(now)
            # Kept for existing dashboard/installations that read this file.
            if mode == "fast":
                (paths.OUT / "last_run.txt").write_text(now)
    finally:
        lock.unlink(missing_ok=True)
    return True


if __name__ == "__main__":
    main_locked()
