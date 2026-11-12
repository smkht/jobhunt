#!/usr/bin/env python3
"""Requirement-level fit check. Reads the WHOLE posting (title + description), finds which requirements the candidate meets, which they lack,
and which are hard blockers (domain/degree/coding/certification/language they do not have). No network, no AI: transparent and repeatable.

  fit % = met / (met + 2 x missing), capped by the share of the posting's own REQUIRED items the background evidences, and by documented years per domain
  (EXP_YEARS; fill via config 'experience_years'; unknown domains are not checked); preferred / nice-to-have items are ignored
  A job passes when fit >= config.min_fit, there are no hard blockers, and enough requirements were detected to judge.

What counts as "has" / "blocked" is derived from DATA/profile.json: a capability label is active when the profile text evidences it, and a blocker
category is switched off when the profile evidences that domain. Languages are driven by config 'languages_spoken'. See sync().
"""
import json, re
from pathlib import Path
import paths

ROOT = paths.ROOT
CFG = paths.load_config()

# capability catalogue: label -> regex. Active only for labels the candidate's profile evidences (see sync()).
HAS = {
    "project/programme management": r"project (management|manager|delivery|lifecycle|life cycle|plan)|program(me)? (management|manager|delivery)|program(me)? or project|managing projects|manage projects|program requirements|\bpmo\b|delivery (management|manager|lead)",
    "stakeholder management": r"stakeholder",
    "vendor/supplier management": r"vendor|supplier|procurement|third[- ]part(y|ies)",
    "risk management": r"\brisks?\b|risk (management|mitigation)|mitigat",
    "governance & reporting": r"governance|steering|status report|reporting to|sponsor|escalat",
    "documentation": r"documentation|document control|procedures|\bsops?\b",
    "budget/cost control": r"budget|cost (control|reduction|management|saving)|financial (tracking|management)|forecast",
    "cross-functional coordination": r"cross[- ]functional|multi[- ]?disciplinary|interdisciplinary|matrix|across (teams|functions|departments)",
    "process improvement": r"process (improvement|optimi[sz]ation|analys[ie]s|design)|continuous improvement|efficien",
    "operations": r"\boperations?\b|operational|day[- ]to[- ]day",
    "communication": r"communicat|presentation|written and verbal",
    "analytical/problem solving": r"analytic|problem[- ]solving|using data|data and metrics|data analysis",
    "team leadership": r"lead(ing)? (a )?teams?|team leadership|people management|manage (a )?team|coordinate teams",
    "negotiation": r"negotiat",
    "client management": r"client (management|relationship|facing|communication)|customer (management|relationship)|account management",
    "Excel/Office/reporting": r"\bexcel\b|microsoft office|\boffice 365\b|dashboard|data visuali[sz]ation|\bkpis?\b",
    "e-commerce/digital programmes": r"e-?commerce|marketplace|digital transformation|platform (launch|implementation)",
    "legal/compliance/contracts": r"legal|compliance|contract|regulat",
    "consulting/client delivery": r"consult(ing|ancy)|professional services|engagement",
    "logistics/supply chain": r"logistic|supply chain|inventory|fulfil?lment|warehouse|carrier",
    "events/conferences": r"conference|events?\b|speaker",
    "business development/partnerships": r"business development|partnerships?|new business|outbound|outreach|proposals?|pitch(ing|es)?|lead generation|account management",
    "strategy/strategic planning": r"strateg(y|ic)|go[- ]to[- ]market|market expansion|roadmap|business (case|plan)",
    "startup/founder experience": r"start-?ups?|founder|entrepreneur|zero[- ]to[- ]one|0 to 1|from scratch|scale-?ups?|build(ing)? (a )?business",
    "commercial/revenue ownership": r"\brevenue\b|\bp&l\b|commercial|pricing|profitab|\bmargins?\b",
    "CRM/work-management tools": r"\bcrm\b|salesforce|clickup|asana|hubspot|work management|project management (tool|software|platform)",
    "English": r"english",
    "planning/scheduling": r"planning|schedul|timelines?|milestone|roadmap",
}

# HARD blockers if they appear as requirements (not preferred): (label, regex). A category is switched off when the profile evidences that domain (see sync()).
BLOCK = [
    ("medical/clinical/life-science domain", r"clinical|oncology|\bgcp\b|\bich[- ]gcp\b|patient|pharmaceutic|biotech|\bcro\b|medical (device|affairs)|regulatory affairs|life sciences?|healthcare professional|hospital|nursing|laborator"),
    ("engineering degree/domain", r"(bachelor|master|degree|bsc|msc)[^.]{0,40}engineer|engineering degree|\b(mechanical|electrical|civil|chemical|industrial|structural|aerospace|offshore|wind|subsea) engineer|\bepc\b|\bhvac\b|\bscada\b|construction (site|project|management)|civil works|commissioning|offshore|wind (farm|turbine|power)|refrigeration|oil (&|and) gas|power plant|substation|payload|space systems"),
    ("hands-on coding/data engineering", r"\b(java|javascript|typescript|c\+\+|golang|kotlin|scala|pyspark)\b|hands-on (coding|programming)|(write|develop|build|maintain|production)[^.]{0,25}\b(sql|python)\b[^.]{0,25}(code|queries|pipelines|scripts?)|software (development|engineer)|devops|kubernetes|terraform|\bapi (design|development)|data engineer|machine learning engineer|\bbigquery\b"),
    ("hands-on technical configuration/engineering", r"engineering mindset|staff engineers|engineering (leaders|teams?) (and|to)|configure and customi[sz]e|configuration solution designs?|hands-on[^.]{0,30}(engineering|configur\w+|coding|vault|crm)|unit testing|solution architect|platform migrations? from|shopify plus|systems? integrations? (e\.g|such as)"),
    ("immigration / global mobility specialism", r"immigration|global mobility|visa (processing|case)|relocation (services|program)"),
    ("cybersecurity specialism", r"\bsoc\b|\bsiem\b|cyber ?security|penetration test|incident response|\bciso\b|iso ?27001 (lead|auditor)"),
    ("finance/accounting qualification", r"\b(cfa|cpa|acca|cima)\b|chartered accountant|financial modell?ing|\btreasury (management|operations)|audit (experience|qualification)|\bifrs\b|tax (compliance|advis)"),
    ("required PM certification", r"(pmp|prince2|pmi[- ]?acp|csm|safe)\b[^.]{0,60}(required|mandatory|must)|(required|mandatory|must)[^.]{0,60}\b(pmp|prince2|csm)\b"),
    ("security guard licence", r"vigilante de seguridad|\\btip\\b.{0,20}(vigilante|seguridad)|licencia de (armas|seguridad)|security guard|guarda jurado|escolta|cctv operator|operador de (cra|central receptora)"),
    ("security clearance", r"security clearance|\bsc clearance|nato secret|government clearance"),
    ("licensed professional (other)", r"(registered|licensed|chartered) (nurse|pharmacist|engineer|architect|surveyor|accountant)|\bcolegiad"),
]

# things the candidate lacks that count as MISSING requirements (soft unless core). Dropped when the profile evidences them (see sync()).
LACKS = {
    "ERP/CRM platform implementation (SAP, Dynamics, Salesforce...)": r"salesforce (admin|implementation|consult)|workday (implementation|hcm)|oracle (erp|cloud|fusion)|\berp (implementation|project|rollout)|sap (implementation|consultant|s/4|hana|fico|functional)|dynamics 365 (implementation|consultant|functional)|servicenow (implementation|developer|admin)|netsuite (implementation|consultant)",
    "enterprise SaaS/software deployment": r"enterprise software|software (deployment|rollout|implementation)|saas (deployment|implementation|rollout)|go-live|technical account|\bpsa\b|\bitsm\b",
    "AI/automation adoption programmes": r"ai adoption|ai-driven|agentic|workflow automation|\brpa\b|automation (programme|program|project)",
    "IT/software delivery experience": r"software (delivery|project|implementation)|it (project|delivery|service)|saas implementation|cloud (migration|project)|\b(aws|azure|gcp) (project|migration|certified)|technical (project|programme)|infrastructure project",
    "sales/commercial quota": r"quota|carry a (number|quota)|sales target|revenue target|cold[- ]call(ing)? (at scale|daily)|closing deals? (daily|monthly)",
    "financial services domain experience": r"(banking|capital markets|financial services|payments|fintech|insurance) (experience|background|industry)|experience (in|within) (the )?(banking|financial|payments|fintech|insurance)",
    "data analytics hands-on": r"data analytics?|advanced analytics|statistical|data science|\bkpi (design|framework)\b|data governance|data quality",
    "people management of large teams": r"manage (a )?(large )?team of \d+|\d+\+? direct reports|line manage",
}
HAS_ALL, BLOCK_ALL, LACKS_ALL = dict(HAS), list(BLOCK), dict(LACKS)

GENERIC = {"communication", "analytical/problem solving", "operations", "English", "planning/scheduling", "documentation"}  # everybody has these: half weight
MISS_WEIGHT = 2.0  # a gap in a stated requirement costs twice what a generic match earns

# "N years ... <industry/specialism>" where the industry is one the candidate has NOT worked in -> hard blocker (unless marked preferred)
FOREIGN = (r"travel industry|tourism|entertainment|amazon fba|game industry|gaming|consulting|business development|account management|partnerships?|customer success|fintech|fin-tech|payments?|banking|financial services|financial sector|financial industry|insurance|saas|software|cloud|cyber ?security|healthcare|health care|pharma\w*|clinical|medical|"
           r"energy|utilities|telecom\w*|product management|product owner|marketing|sales|accounting|finance|engineering|lending|credit|card (products?|schemes?|issuing)|"
           r"it services|it delivery|technology industry|data science|machine learning|ux|recruit\w*|real estate|aviation|automotive|manufacturing|broadcast\w*|media industry|game ?dev\w*|gaming industry|video games?|luxury|fashion industry|advertising|publishing|hospitality industry|telecoms?")
INDUSTRY_YEARS = re.compile(r"(?P<n>\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)[^.;\n]{0,90}?\b(?P<dom>" + FOREIGN + r")\b", re.I)
INDUSTRY_YEARS_REV = re.compile(r"\b(?P<dom>" + FOREIGN + r")\b[^.;\n]{0,50}?(?P<n>\d{1,2})\s*\+?\s*(?:years|yrs)", re.I)

# Domain -> regex. Years per domain come ONLY from config 'experience_years' (unknown domain = no years check, never a blocker).
# Order matters: the most specific domain is tested first.
EXP_YEARS = [
    ("financial services/fintech/banking/payments", r"fintech|fin-tech|banking|\bbank\b|payments?\b|financial services|financial sector|insurance|lending|credit|card (products?|schemes?|issuing)", None),
    ("pharma/healthcare/life sciences", r"pharma\w*|clinical|medical|healthcare|health care|life sciences?", None),
    ("energy/utilities/telecom/manufacturing", r"energy|utilities|telecom\w*|manufacturing|automotive|aviation|construction", None),
    ("crypto/blockchain/forex", r"crypto|blockchain|forex|web3", None),
    ("travel/tourism/media/luxury", r"travel industry|tourism|entertainment|broadcast\w*|luxury|fashion|publishing|advertising|game industry|gaming", None),
    ("product management/ownership", r"product (management|manager|owner)", None),
    ("warehouse management", r"warehous", None),
    ("recruitment/talent acquisition", r"recruit|talent acquisition|headhunt", None),
    ("category management/buying", r"category manage|buying|merchandis|trade marketing", None),
    ("SaaS/software/tech", r"saas|software|\bit\b|technolog|fintech|payments?|cloud|data|engineering|engineer", None),
    ("customer success/account management", r"customer success|account manage|client success|key account", None),
    ("sales/business development", r"\bsales\b|business development|\bbd\b|commercial", None),
    ("logistics/supply chain", r"logistic|supply chain|inventory|fulfil", None),
    ("event management", r"\bevents?\b", None),
    ("marketing", r"marketing|brand|growth", None),
    ("finance/financial analysis", r"financ|accounting|treasury|audit", None),
    ("strategy/consulting", r"strateg|consult", None),
    ("procurement/vendor management", r"procurement|purchasing|vendor|supplier|sourcing", None),
    ("contract management", r"contract", None),
    ("project/programme management", r"project|program(me)?|delivery|implementation", None),
    ("customer service", r"customer service|customer support|client service|customer experience", None),
    ("people management", r"people management|manag\w* (of )?(people|teams?)|leading teams?|team lead|leadership", None),
    ("operations", r"operations?|operational", None),
    ("legal/compliance", r"legal|\blaw\b|compliance|regulat", None),
]
EXP_BASE = list(EXP_YEARS)
EXP_YEARS = list(EXP_BASE)
YEARS_RX = re.compile(r"(?P<n>\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)\b(?P<ctx>[^.;\n]{0,110})|(?:minimum|at least|min\.?)\s*(?:of\s*)?(?P<n2>\d{1,2})\s*\+?\s*(?:years|yrs)\b(?P<ctx2>[^.;\n]{0,110})", re.I)
GENERIC_YEARS_HAS = None  # years of 'relevant experience' with no domain named: set config 'generic_years' to enable that check


def years_gaps(text):
    sync()
    """Required 'N+ years' items compared with the candidate's documented years in that domain -> (blockers, missing, warnings)."""
    blockers, missing, warns, seen = [], {}, [], set()
    for m in YEARS_RX.finditer(text):
        n = int(m.group("n") or m.group("n2") or 0)
        ctx = (m.group("ctx") or m.group("ctx2") or "")
        window = text[max(0, m.start() - 40): m.end() + 10]
        if n < 2 or n > 30 or _is_preferred(window) or re.search(r"since|founded|established|history|market|customers|employees|countries|clients|heritage|legacy|\bof experience in the (company|group)", window, re.I):
            continue
        hits = [(name, y) for name, rx, y in EXP_YEARS if re.search(rx, ctx, re.I)]
        dom, his = hits[0] if hits else (None, None)
        if len(hits) > 1 and re.search(r"\bor\b|,|/|\bo\b", ctx):  # a list of alternatives ("consulting, banking, or startups"): the best match counts
            known = [h for h in hits if h[1] is not None]
            if known:
                dom, his = max(known, key=lambda h: h[1])
        if dom is None:
            if not re.search(r"experience|background|track record|proven", window, re.I):
                continue
            dom, his = "relevant experience", GENERIC_YEARS_HAS
        if his is None:
            continue  # no documented years for this domain: do not judge
        key = (dom, n)
        if key in seen:
            continue
        seen.add(key)
        gap = n - his
        if his == 0 and n >= 3 or gap >= 5:
            blockers.append(f"{n}+ years in {dom} required, you have ~{his}: \"{(m.group(0)).strip()[:90]}\"")
        elif gap >= 2:
            missing[f"{n}+ years in {dom} (you have ~{his})"] = m.group(0).strip()[:100]
            warns.append(f"asks {n}+ years in {dom}; you have ~{his}")
    return blockers, missing, warns


REQ_HEAD = re.compile(r"(requirements?|qualifications?|what you('|’)?ll need|what you need|what you bring|what you'?ll bring|who you are|about you|you have|what we('|’)?re looking for|what we are looking for|must[- ]haves?|essential|basic qualifications|perfil|requisitos|buscamos)", re.I)
REQ_SIGNAL = re.compile(r"experience|years|degree|knowledge|proven|ability|skills|proficien|fluent|track record|background|understanding|expertise|familiar|certif|qualif", re.I)


def requirement_coverage(sents):
    sync()
    """Share of the posting's REQUIRED items that the documented background actually evidences. Returns (fit_pct or None, n_items, gaps)."""
    start = next((i for i, s in enumerate(sents) if REQ_HEAD.search(s)), None)
    if start is None:
        return None, 0, []
    tail = sents[start:start + 60]
    cut = next((i for i, s in enumerate(tail) if re.search(r"preferred qualifications|nice to have|equal opportunit|we make recruiting decisions", s, re.I)), len(tail))
    items = [s for s in tail[:cut] if 30 <= len(s) <= 260 and not s.rstrip().endswith(':') and REQ_SIGNAL.search(s) and not _is_preferred(s)]
    if len(items) < 4:
        return None, len(items), []
    got, gaps = 0.0, []
    for s in items:
        hits = [k for k, rx in HAS.items() if re.search(rx, s, re.I)]
        lacks = [k for k, rx in LACKS.items() if re.search(rx, s, re.I)]
        specific = [k for k in hits if k not in GENERIC]
        if lacks:
            gaps.append(s[:90])
        elif specific:
            got += 1.0
        elif hits:
            got += 0.5
        else:
            gaps.append(s[:90])
    return round(100 * got / len(items)), len(items), gaps


# skills-first hiring: assessments / work samples decide, not CV filters. For these postings years and degree gaps are downgraded (hard blockers stay: language, location, domain).
SKILLS_FIRST = re.compile(r"skills[- ]based (hiring|recruit\w*|assessment)|skills[- ]first|hire (for|on) (skills|potential)|regardless of (your )?(degree|background|education|experience)|no degree (is )?(required|needed)|degree (is )?not (required|needed)|work[- ]sample|practical (test|task|assignment|exercise|challenge)|take[- ]home (task|challenge|assignment|test|exercise)?|paid trial|trial (project|day|week)|testgorilla|adaface|vervoe|canditech|criteria corp|\bccat\b|cognitive (ability|aptitude)? ?test|skills? (assessment|test|evaluation)|online (assessment|test)|(timed|automated) (test|assessment)|assessment (platform|phase|stage|round|link)|live case|case (study|interview|exercise)|(you will|you.ll|candidates will) (complete|take|do|receive) an? (short )?(assessment|test|task)", re.I)


def skills_first(text):
    m = SKILLS_FIRST.search(text or "")
    return m.group(0).strip() if m else ""


TOOL_NAMES = ["Salesforce", "HubSpot", "Zendesk", "Gorgias", "Gladly", "Intercom", "Freshdesk", "Jira", "Confluence", "Asana", "ClickUp", "Monday.com", "Notion", "Trello", "Power BI", "Tableau", "Looker", "Qlik", "SAP", "Oracle", "NetSuite", "Dynamics 365", "Workday", "ServiceNow", "Workable", "Greenhouse", "Lever", "Shopify", "Klaviyo", "Magento", "WMS", "ERP", "CRM", "ATS", "HRIS", "Excel", "Google Sheets", "SQL", "Python", "Airtable", "Slack", "Figma", "Canva", "MS Project", "Smartsheet", "Coupa", "Ariba", "Docusign", "Attio", "Pipedrive", "Zoho", "Linear", "Gainsight"]
_TOOLS_RX = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(t) for t in TOOL_NAMES) + r")(?![A-Za-z])", re.I)


TOOL_HINT = {}  # optional per-tool comment shown next to a tool the posting names: config "tool_hints" {"Tableau": "no experience"}


def tools_mentioned(text):
    """Software the posting names. Never a blocker: shown as a note so you can decide whether you are comfortable with the level."""
    seen, out = set(), []
    for m in _TOOLS_RX.finditer(text or ""):
        k = m.group(1).lower()
        if k not in seen:
            seen.add(k)
            out.append(next(t for t in TOOL_NAMES if t.lower() == k))
    hints = {**TOOL_HINT, **(CFG.get("tool_hints") or {})}
    return [t + (" (" + hints[t] + ")" if hints.get(t) else "") for t in out[:10]]


PLACES = (r"west coast|east coast|us west|us east|romania|poland|portugal|germany|france|italy|netherlands|belgium|ireland|uk|united kingdom|england|scotland|greece|bulgaria|hungary|czech\w*|slovakia|croatia|serbia|ukraine|turkey|"
          r"sweden|norway|denmark|finland|estonia|latvia|lithuania|austria|switzerland|cyprus|malta|usa|u\.s\.a?\.?|united states|us|canada|mexico|brazil|argentina|colombia|chile|peru|latam|latin america|"
          r"india|philippines|pakistan|bangladesh|vietnam|indonesia|malaysia|singapore|thailand|japan|china|korea|australia|new zealand|apac|asia|africa|south africa|nigeria|kenya|egypt|morocco|"
          r"israel|uae|dubai|saudi arabia|middle east|north america|emea only|gcc|"
          r"london|paris|berlin|munich|hamburg|amsterdam|dublin|lisbon|porto|milan|rome|zurich|geneva|vienna|warsaw|krakow|prague|budapest|bucharest|stockholm|copenhagen|oslo|helsinki|"
          r"new york|san francisco|boston|austin|chicago|seattle|los angeles|washington|toronto|vancouver|sydney|melbourne|singapore|tel aviv|mumbai|bangalore|bengaluru|delhi|tokyo|hong kong")
LOC_OK = re.compile(r"\b(worldwide|anywhere|global(ly)?|international)\b", re.I)  # rebuilt from the home location in sync()
LOC_STRICT = re.compile(r"\b(worldwide|anywhere)\b", re.I)
HOME_WORDS = set()
LOC_PATTERNS = [
    re.compile(r"\b(?:based|located|residing|resident|residents|living|live|reside|work(?:ing)? from|work remotely from|remote (?:in|from)|hiring in|open to candidates (?:in|from)|candidates (?:in|from|based in)|applicants (?:in|from|based in)|employees (?:in|from)|eligible to work in|right to work in|authori[sz]ed to work in)\s+(?:in |within |from |the )?(?P<p>" + PLACES + r")\b", re.I),
    re.compile(r"\b(?P<p>" + PLACES + r")[- ](?:based|only|resident|residents|located)\b", re.I),
    re.compile(r"\b(?:must|need to|required to|should)\s+(?:be|reside|live|work|have)[^.;]{0,40}?\b(?P<p>" + PLACES + r")\b", re.I),
    re.compile(r"\bremote\s*[\(\-–:]\s*(?P<p>" + PLACES + r")\b", re.I),
    re.compile(r"\b(?:based|located|living|residing)\s+in\s+[^.;]{0,30}?(?P<p>usa|u\.s\.a?\.?|united states|canada|india|philippines|brazil|mexico)\b", re.I),
]
TZ = re.compile(r"\b(?:est|edt|pst|pdt|cst|mst|et|pt)\b(?:\s*(?:time ?zone|hours|overlap))|(?:overlap|work)[^.;]{0,40}\b(?:est|pst|cst|us) (?:hours|time)|\bus (?:time ?zones?|hours)\b|\b(?:west|east) coast (?:hours|time)|\b(?:pacific|eastern|central) (?:time|standard time)\b|\bu\.?s\.? (?:west|east) coast hours", re.I)


def location_blockers(text):
    """Remote/hybrid roles that are only open to people living/working somewhere other than the candidate's home region. Returns list of reasons."""
    out = []
    if "US-REMOTE-CONTRACTOR-OK" in text:
        return out
    for s in re.split(r"(?<=[.!?])\s+|\n", re.sub(r"[ \t]+", " ", text)):
        if _is_preferred(s):
            continue
        for rx in LOC_PATTERNS:
            m = rx.search(s)
            if m:
                if m.group("p").lower() in HOME_WORDS:
                    continue
                if LOC_OK.search(s) and LOC_STRICT.search(s):
                    continue
                out.append(f"location restriction: {m.group('p')} (\"{s.strip()[:100]}\")")
                break
    return out[:2]


WORLDWIDE_HIRING = re.compile(r"work from anywhere|anywhere in the world|worldwide|any (country|location)|across (the )?(world|globe)|employer of record|\beor\b|international contractors?|hire globally|fully distributed|remote[- ]first company", re.I)
LOC_FIELD_BAD = re.compile(r"\b(" + PLACES + r")\b", re.I)


def location_field_blocker(location, desc):
    """Job-board location field limited to a place the candidate cannot work from (e.g. 'United States', 'India') -> reason, unless the posting hires worldwide."""
    loc = location or ""
    if not loc.strip() or LOC_OK.search(loc) or loc.startswith("Remote (") or WORLDWIDE_HIRING.search(desc or "") or "US-REMOTE-CONTRACTOR-OK" in (desc or ""):
        return None
    m = LOC_FIELD_BAD.search(loc)
    if m and m.group(1).lower() in HOME_WORDS:
        return None
    return f"location field limited to {m.group(1)}: \"{loc[:60]}\"" if m else None


DOMAIN_MUST = re.compile(r"(?:experience|background|knowledge)\s+(?:in|with|of)\s+(?:the\s+)?(?P<dom>" + FOREIGN + r")[^.;\n]{0,40}\b(?:is a must|is required|is mandatory|is essential|required|mandatory|essential|must)\b", re.I)

_ES = re.compile(r"\b(de|la|el|los|las|para|con|que|una|por|del|nuestro|equipo|experiencia|trabajo|buscamos|gesti[oó]n|proyectos|persona|requisitos|ofrecemos|funciones|responsabilidades)\b", re.I)
_EN = re.compile(r"\b(the|and|with|for|you|our|will|team|experience|work|about|role|your|are|have)\b", re.I)


_DE = re.compile(r"\b(und|der|die|das|mit|für|wir|sie|du|dein|deine|ein|eine|bei|oder|auf|zu|sind|werden|deine aufgaben|ihre|unser|unsere|bewerbung|erfahrung|kenntnisse)\b", re.I)
_FR = re.compile(r"\b(nous|vous|votre|notre|les|des|une|pour|avec|dans|poste|entreprise|équipe|expérience|missions|profil)\b", re.I)
_IT = re.compile(r"\b(per|della|nostro|nostra|con|siamo|esperienza|lavoro|ricerchiamo|candidato|requisiti|azienda)\b", re.I)


def foreign_language(text):
    """'German' / 'French' / 'Italian' / 'Spanish' if the posting is clearly written in it AND the candidate does not speak it (config languages_spoken), else ''."""
    w = text[:1500]
    en = len(_EN.findall(w))
    for name, rx, need in (("German", _DE, 10), ("French", _FR, 10), ("Italian", _IT, 9)):
        n = len(rx.findall(w))
        if n >= need and n > en * 1.1 and name.lower() not in SPOKEN:
            return name
    return "Spanish" if "spanish" not in SPOKEN and is_spanish_text(text) else ""


def is_spanish_text(text):
    w = text[:1500]
    es, en = len(_ES.findall(w)), len(_EN.findall(w))
    return es >= 8 and es > en * 1.2


# a Spanish-written posting is acceptable when it says English is the working language / the team is international and does not require Spanish
ENGLISH_OK = re.compile(r"working language (is )?english|english (is )?(the |our )?(working|business|corporate) language|english[- ]speaking (team|environment|company|workplace|office)|(team|environment|company|office) (is|works) (entirely |fully |mainly )?in english|trabajamos en ingl[eé]s|idioma de trabajo[^.]{0,20}ingl[eé]s|(entorno|equipo|oficina|ambiente) (de trabajo )?(totalmente |completamente )?(en|de) ingl[eé]s|entorno (laboral )?(internacional|multinacional|multicultural)|equipos? internacionales?|international (team|environment|company|context|workplace)|multinational environment|multicultural (team|environment)|apply (now )?with your cv in english|submit your application in english|cv (en|in) ingl[eé]s", re.I)  # English working environment explicit or clearly international; a bare 'English level B2/C1' does NOT count
SPANISH_REQUIRED = re.compile(r"espa[nñ]ol[^.]{0,25}(nativo|fluido|avanzado|c1|c2|imprescindible|obligatorio|excelente)|(nativo|fluido|avanzado|excelente)[^.]{0,20}espa[nñ]ol|castellano|nivel[^.]{0,15}(alto|nativo|avanzado)[^.]{0,15}(espa[nñ]ol|castellano)", re.I)

# gaps that define the job itself: if the posting asks for experience in them, it is a hard blocker, not a soft miss
CORE_LACKS = {"IT/software delivery experience", "enterprise SaaS/software deployment", "ERP/CRM platform implementation (SAP, Dynamics, Salesforce...)"}
EXPERIENCE_WORDS = re.compile(r"experience|proven|track record|background|expertise|skilled|knowledge", re.I)

DOMAIN_MUST2 = re.compile(r"\b(?P<dom>" + FOREIGN + r")(?:/\w+)?\s+(?:industry\s+)?experience[^.;]{0,15}\b(?:is|are)\s+(?:required|a must|mandatory|essential)", re.I)

PREF = re.compile(r"(preferred|nice to have|nice-to-have|bonus|a plus|is a plus|desirable|advantage|ideally|an asset|would be great|beneficial|optional|good to have)", re.I)
REQ_HINT = re.compile(r"(requirements?|qualifications?|what you('|’)?ll need|what you need|what you bring|what you'?ll bring|who you are|about you|skills|experience|must have|you have|we are looking|we'?re looking)", re.I)


def _sentences(text):
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?;:])\s+|\s[•·▪●■*-]\s|\n", text) if s.strip()]


def _is_preferred(sent):
    return bool(PREF.search(sent))


LANG_UNIVERSE = ["spanish", "castellano", "french", "german", "italian", "dutch", "portuguese", "chinese", "mandarin", "cantonese", "japanese", "korean", "arabic",
                 "polish", "turkish", "swedish", "norwegian", "danish", "finnish", "russian", "hindi", "greek", "czech", "hungarian", "romanian", "hebrew", "ukrainian",
                 "thai", "vietnamese", "indonesian"]
RELOC_CITIES = ["barcelona", "madrid", "lisbon", "berlin", "london", "paris", "amsterdam", "hamburg", "dublin", "warsaw", "milan", "valencia", "palma", "zug", "zurich",
                "munich", "prague", "vienna", "tallinn", "malaga", "seville", "bilbao", "new york", "san francisco", "singapore", "dubai"]
EVIDENCE_HINT = {"required PM certification": r"\bpmp\b|prince2|\bcsm\b|pmi[- ]?acp", "finance/accounting qualification": r"\b(cfa|cpa|acca|cima)\b|chartered accountant",
                 "security clearance": r"clearance"}
STATIC_LANG_LABELS = {"English plus another language is required", "native/fluent language other than English", "relocation to another city (you will not relocate)"}
SPOKEN = {"english"}
PROF_TEXT = ""
_SYNC_KEY = None


def _profile_text(p):
    parts = [p.get("headline_default", ""), " ".join(p.get("skills", [])), " ".join(p.get("credentials", [])), " ".join(p.get("languages", []))]
    parts += [" ".join(map(str, e)) if isinstance(e, (list, tuple)) else str(e) for e in p.get("education", [])]
    for j in p.get("jobs", []):
        parts += [j.get("title", ""), j.get("org", ""), " ".join(j.get("bullets", []))]
    return " ".join(parts).lower().strip()


def _evidenced(term):
    return bool(term) and bool(PROF_TEXT) and term.lower().strip() in PROF_TEXT


def sync(force=False):
    """Derive the active capability / blocker / language / location rules from DATA/profile.json and the merged config. Cheap: re-runs only when those files change."""
    global CFG, HAS, BLOCK, LACKS, EXP_YEARS, SPOKEN, PROF_TEXT, LOC_OK, LOC_STRICT, HOME_WORDS, _SYNC_KEY
    files = [paths.DATA / "profile.json", paths.DATA / "config.json", paths.ROOT / "defaults" / "config.json"]
    key = tuple(f.stat().st_mtime_ns if f.exists() else 0 for f in files)
    if key == _SYNC_KEY and not force:
        return
    _SYNC_KEY = key
    CFG = paths.load_config()
    prof = paths.load_profile()
    PROF_TEXT = _profile_text(prof)
    SPOKEN = {x.lower().split()[0] for x in (CFG.get("languages_spoken") or ["English"])} | {"english"}
    if "spanish" in SPOKEN:
        SPOKEN.add("castellano")
    # capabilities the profile evidences (no profile yet: everything counts, so nothing is judged harshly before onboarding)
    HAS = {l: rx for l, rx in HAS_ALL.items() if not PROF_TEXT or re.search(rx, PROF_TEXT, re.I) or (l == "English" and "english" in SPOKEN)}
    HAS.update(CFG.get("fit_has_extra") or {})
    for l in CFG.get("fit_has") or []:
        if l in HAS_ALL:
            HAS[l] = HAS_ALL[l]
    LACKS = {l: rx for l, rx in LACKS_ALL.items() if not (PROF_TEXT and re.search(rx, PROF_TEXT, re.I))}
    BLOCK = [(l, rx) for l, rx in BLOCK_ALL if l not in STATIC_LANG_LABELS
             and not (PROF_TEXT and re.search(EVIDENCE_HINT.get(l, rx), PROF_TEXT, re.I)) and l not in (CFG.get("fit_blockers_off") or [])]
    unspoken = [x for x in LANG_UNIVERSE if x not in SPOKEN]
    if unspoken:
        lv = "|".join(unspoken)
        BLOCK.append(("English plus another language is required",
                      rf"\benglish\s*(?:&|and|/)\s*(?:{lv})\b[^.]{{0,40}}\b(?:must|required|essential|mandatory|fluen\w*|native)|\b(?:fluen\w*|proficien\w*|native)\b[^.]{{0,25}}\benglish\s*(?:&|and|/)\s*(?:{lv})\b|\b(?:{lv})\s*(?:&|and|/)\s*english\b[^.]{{0,30}}(?:must|required|essential|mandatory|fluen\w*)"))
        BLOCK.append(("native/fluent language other than English",
                      rf"(native|fluent|fluency|proficien\w*|excellent|advanced|very good|strong|good|professional|business[- ]level|bilingual|c1|c2|mother[- ]tongue)[^.]{{0,30}}\b({lv})\b|\b({lv})\b[^.]{{0,25}}(native|fluent|fluency|c1|c2|mother[- ]tongue|required|speaker|speaking|a must|mandatory|and english)|\b(both|bilingual)\b[^.]{{0,20}}\b({lv})\b|\b({lv})\s*(&|and|/)\s*english\b"))
    home_city = (CFG.get("home_city") or "").strip().lower()
    if home_city and not CFG.get("will_relocate"):
        cs = "|".join(c for c in RELOC_CITIES if c != home_city)
        BLOCK.append(("relocation to another city (you will not relocate)",
                      rf"(relocat\w+[^.]{{0,80}}\b({cs})\b|\b({cs})\b[^.]{{0,80}}relocat\w+|not in ({cs})\?[^.]{{0,60}}relocation)"))
    # location vocabulary
    home = [x.lower() for x in [CFG.get("home_city"), CFG.get("home_country")] if x] + [x.lower() for x in (CFG.get("home_aliases") or [])]
    region = [x.lower() for x in (CFG.get("region_words") or [])]
    HOME_WORDS = set(home) | set(region)
    esc = lambda xs: "|".join(re.escape(x) for x in xs)
    LOC_OK = re.compile(r"\b(" + (esc(list(HOME_WORDS)) + "|" if HOME_WORDS else "") + r"worldwide|anywhere|global(ly)?|international)\b", re.I)
    LOC_STRICT = re.compile(r"\b(" + (esc(list(HOME_WORDS)) + "|" if HOME_WORDS else "") + r"worldwide|anywhere)\b", re.I)
    EXP_YEARS = [(n, rx, (CFG.get("experience_years") or {}).get(n, y)) for n, rx, y in EXP_BASE]
    global GENERIC_YEARS_HAS
    GENERIC_YEARS_HAS = CFG.get("generic_years")


def refresh():
    sync(force=True)


def assess(title, desc, min_fit=None, location=""):
    sync()
    min_fit = min_fit or CFG.get("min_fit", 65)
    import html as _h
    desc = re.sub(r"<[^>]+>", " ", _h.unescape(re.sub(r"<[^>]+>", " ", _h.unescape(desc or ""))))  # tolerate entity-encoded HTML
    text = f"{title}. {desc}"[:9000]
    sents = _sentences(text)
    blockers, met, missing, soft_seen = [], {}, {}, set()
    industry_warn = []
    low_title = title.lower()
    for label, rx in BLOCK:
        if re.search(rx, low_title, re.I) and "language" not in label and not label.startswith("relocation"):
            blockers.append(f"{label} (in title)")
            continue
        for s in sents:
            m = re.search(rx, s, re.I)
            if m and not _is_preferred(s[max(0, m.start() - 70): m.end() + 40]):
                # a language mentioned only as 'a plus' is ignored; domain words in company boilerplate need requirement context
                if label.startswith("engineering") and re.search(r"business|supply chain|economics|operations|logistics|administration|any (field|discipline)", s, re.I):
                    continue  # degree listed among several accepted fields (e.g. 'Engineering, Supply Chain, Economics or Business')
                if label.startswith("medical") and not (REQ_HINT.search(s) or re.search(r"experience|background|knowledge|degree|expertise|years", s, re.I)):
                    continue
                blockers.append(f"{label}: \"{s[:110]}\"")
                break
    blockers += location_blockers(text)
    lf = location_field_blocker(location, desc)
    if lf:
        blockers.append(lf)
    tzn = (CFG.get("timezone_note") or "").strip()
    warnings = ([f"⚠ {w}" for w in industry_warn]) + ([tzn] if tzn and TZ.search(text) else [])
    home = (CFG.get("home_country") or "").lower()
    if home not in ("united states", "usa", "us", "u.s.") and re.search(r"authori[sz]ed to work in the (u\.?s\.?|united states)|work authori[sz]ation[^.]{0,40}(u\.?s\.?|united states)|without (visa )?sponsorship", text, re.I) and re.search(r"u\.?s\.?|united states", text, re.I):
        warnings.append("US work authorisation asked: check whether you are eligible (for example as a contractor abroad) before applying")
    if not CFG.get("english_native") and re.search(r"native[- ]level[^.]{0,30}english|native[- ](english|speaker)|english[^.]{0,20}native", text, re.I):
        warnings.append("native English requested: confirm your English level is enough")
    lang = foreign_language(desc) or foreign_language(title)
    if lang:
        if (lang == "Spanish" and SPANISH_REQUIRED.search(desc)) or not ENGLISH_OK.search(desc):
            blockers.append(f"posting is written in {lang} and does not say English is the working language")
        else:
            warnings.append(f"posting is in {lang} but mentions an English / international working environment: confirm English is enough")
    for label, rx in HAS.items():
        if re.search(rx, text, re.I):
            met[label] = True
    for label, rx in LACKS.items():
        for s in sents:
            if re.search(rx, s, re.I):
                if _is_preferred(s):
                    soft_seen.add(label)
                else:
                    missing[label] = s[:110]
                    if label in CORE_LACKS and EXPERIENCE_WORDS.search(s):
                        blockers.append(f"{label} is required: \"{s[:100]}\"")
                break
    for m in DOMAIN_MUST2.finditer(text):
        ctx = text[max(0, m.start() - 40): m.end() + 40]
        if not _is_preferred(text[max(0, m.start() - 25): m.end() + 10]) and not _evidenced(m.group("dom")):
            blockers.append(f"{m.group('dom').lower()} industry experience is required: \"{ctx.strip()[:100]}\"")
            break
    for m in DOMAIN_MUST.finditer(text):
        ctx = text[max(0, m.start() - 40): m.end() + 40]
        if not _is_preferred(ctx) and not _evidenced(m.group("dom")):
            blockers.append(f"{m.group('dom').lower()} experience is a must: \"{ctx.strip()[:100]}\"")
            break
    yb, ym, yw = years_gaps(text)
    sf = skills_first(text)
    if sf and yb:  # skills decide: a years requirement is a gap, not a wall
        for b in yb:
            ym[b.split(":")[0].replace(" required", "")] = b[:100]
        yb = []
    blockers += yb
    missing.update(ym)
    industry_warn += [w[0:0] + w for w in yw]
    # years of experience
    yrs = [int(x) for x in re.findall(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)\b(?=[^.]{0,40}experience)|experience[^.]{0,40}?(\d{1,2})\s*\+?\s*(?:years|yrs)\b", text, re.I) if 0 < int(x or 0) <= 30] if False else [int(a or b) for a, b in re.findall(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?(?:years|yrs)\b(?=[^.]{0,40}experience)|experience[^.]{0,40}?(\d{1,2})\s*\+?\s*(?:years|yrs)\b", text, re.I) if 0 < int(a or b or 0) <= 30]
    cap = CFG.get("max_years_asked")
    if cap and yrs and max(yrs) > cap:
        blockers.append(f"asks for {max(yrs)}+ years")
    n_met = sum(0.5 if k in GENERIC else 1.0 for k in met)
    n_miss = len(missing) * MISS_WEIGHT
    judged = n_met + n_miss
    fit = round(100 * n_met / judged) if judged else 0
    req_fit, req_n, req_gaps = requirement_coverage(sents)
    if req_fit is not None:
        fit = min(fit, req_fit)  # topic overlap alone must not give 100%: the posting's own required items decide
        if req_gaps:
            warnings.append(f"{len(req_gaps)} of {req_n} stated requirements not evidenced in your background, e.g. \"{req_gaps[0]}\"")
    if ym:
        fit = min(fit, 70)
    verdict = "ok"
    if blockers:
        verdict = "blocked"
    elif len(desc) < 300:
        verdict = "no-description"
    elif n_met < 3.0 or (req_fit is None and len(desc) < 2500):
        verdict = "too-little-evidence"  # short text with no requirements section: probably a truncated listing (LinkedIn cuts long ones)
    elif fit < min_fit:
        verdict = "low-fit"
    if sf:
        warnings.append(f"SKILLS-FIRST hiring (\"{sf}\"): assessments/work samples decide; years and degree gaps downgraded")
    return {"fit": 0 if blockers else fit, "tools": tools_mentioned(text), "skills_first": sf, "verdict": verdict, "met": sorted(met), "missing": sorted(missing), "req_gaps": req_gaps, "preferred_only": sorted(soft_seen), "blockers": blockers, "warnings": warnings}


def note(a):
    t = a.get("tools") or []
    tail = (" | tools named: " + ", ".join(t) + " (you decide if the level suits you)") if t else ""
    return _note(a) + tail


def _note(a):
    if a["verdict"] == "ok":
        s = f"fit {a['fit']}%" + (" | ⚠ " + "; ".join(a.get("warnings", [])) if a.get("warnings") else "")
        if a["missing"]:
            s += " | missing: " + "; ".join(a["missing"])
        return s
    why = {"blocked": "blocker: " + "; ".join(b.split(":")[0] for b in a["blockers"]), "no-description": "no description fetched - read the posting",
           "too-little-evidence": "posting gives too little to judge", "low-fit": f"fit {a['fit']}% < {CFG.get('min_fit', 65)}% | missing: " + "; ".join(a["missing"])}[a["verdict"]]
    return why


if __name__ == "__main__":
    import sys
    j = json.loads((paths.OUT / "jobs" / f"{sys.argv[1]}.json").read_text())
    print(json.dumps(assess(j["title"], j["desc"]), indent=1))
