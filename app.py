import streamlit as st
import pandas as pd
import requests, re, time, json, hashlib, io, urllib.parse
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin, quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher
import phonenumbers

st.set_page_config(page_title="Prospect Discovery Engine", layout="wide")

APP_VERSION = "v30"
USER_AGENT = "ProspectDiscoveryEngine/30 (+https://streamlit.app; official-site-validation-directory-separation)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}

SECTOR_PROFILES = {
    "Schools": {
        "queries": ["school", "private school", "international school", "college", "academy"],
        "keep_keywords": ["school", "college", "academy", "primary", "high", "secondary", "pre-primary", "waldorf", "montessori", "campus"],
        "reject_keywords": ["driving school", "testing yard", "licence", "license", "traffic department", "parking", "residence", "student residence", "accommodation"],
        "page_paths": ["", "contact", "contact-us", "contacts", "admissions", "admissions/contact-us", "enrolment", "enroll", "school-office", "administration", "staff", "team", "leadership", "about", "about-us", "parents", "downloads", "newsletter"],
        "official_terms": ["school", "college", "academy", "primary", "secondary", "high", "pre-primary", "preparatory"],
    },
    "Universities / Colleges": {
        "queries": ["university", "college", "campus", "higher education"],
        "keep_keywords": ["university", "college", "campus", "faculty", "school of"],
        "reject_keywords": ["residence", "parking", "shop"],
        "page_paths": ["", "contact", "contact-us", "admissions", "about", "departments"],
        "official_terms": ["university", "college", "campus", "faculty"],
    },
    "General Organizations": {
        "queries": ["organization", "company", "office"],
        "keep_keywords": [],
        "reject_keywords": [],
        "page_paths": ["", "contact", "contact-us", "about", "about-us", "team"],
        "official_terms": ["contact", "about", "organization"],
    },
}



# ---------------- Dynamic prospect profiles ----------------

META_PROFILES = {
    "healthcare": {
        "triggers": ["clinic", "doctor", "medical", "health", "therapy", "therapist", "physio", "physiotherapy", "dentist", "psychologist", "counsellor", "rehab"],
        "page_paths": ["", "contact", "contact-us", "appointments", "bookings", "booking", "services", "team", "about", "locations"],
        "roles": ["owner", "practice manager", "reception", "clinic manager", "director", "provider", "therapist", "doctor"],
        "org_terms": ["clinic", "practice", "centre", "center", "services"],
        "osm_amenities": ["clinic", "doctors", "hospital", "dentist", "physiotherapist"],
        "reject_keywords": ["job", "jobs", "vacancy", "course", "courses", "training", "university course", "directory", "near me"]
    },
    "education": {
        "triggers": ["school", "college", "university", "academy", "education", "tutor", "training"],
        "page_paths": ["", "contact", "contact-us", "admissions", "enrolment", "staff", "team", "about", "programmes"],
        "roles": ["principal", "director", "admissions", "registrar", "head", "counselor"],
        "org_terms": ["school", "college", "academy", "institute", "centre"],
        "osm_amenities": ["school", "college", "university"],
        "reject_keywords": ["driving school", "testing yard", "licence", "parking", "residence", "student residence"]
    },
    "nonprofit": {
        "triggers": ["ngo", "nonprofit", "non-profit", "charity", "foundation", "association", "community organization"],
        "page_paths": ["", "contact", "contact-us", "about", "team", "programmes", "programs", "partners", "leadership"],
        "roles": ["executive director", "director", "program manager", "partnerships", "operations", "contact"],
        "org_terms": ["ngo", "nonprofit", "foundation", "association", "organisation", "organization"],
        "osm_amenities": ["community_centre", "social_facility"],
        "reject_keywords": ["job", "jobs", "vacancy", "directory", "wikipedia"]
    },
    "professional_service": {
        "triggers": ["consultant", "lawyer", "attorney", "accountant", "agency", "firm", "advisor", "architect", "engineer"],
        "page_paths": ["", "contact", "contact-us", "about", "team", "services", "people", "leadership"],
        "roles": ["owner", "founder", "partner", "director", "manager", "reception"],
        "org_terms": ["firm", "practice", "agency", "consultancy", "services", "company"],
        "osm_amenities": ["office"],
        "reject_keywords": ["job", "jobs", "course", "training", "directory"]
    },
    "food": {
        "triggers": ["pizza", "pizzeria", "restaurant", "takeaway", "takeout", "burger", "sushi", "bakery", "cafe", "coffee", "catering", "food"],
        "page_paths": ["", "contact", "contact-us", "locations", "location", "menu", "about", "order", "bookings", "reservations"],
        "roles": ["owner", "manager", "restaurant manager", "branch manager", "reception", "orders"],
        "org_terms": ["restaurant", "takeaway", "takeout", "shop", "kitchen", "cafe"],
        "osm_amenities": ["restaurant", "fast_food", "cafe"],
        "reject_keywords": ["recipe", "recipes", "job", "jobs", "delivery app", "ubereats", "mr d", "directory", "top 10", "best of"]
    },
    "general": {
        "triggers": [],
        "page_paths": ["", "contact", "contact-us", "about", "about-us", "team", "services", "locations"],
        "roles": ["owner", "manager", "director", "reception", "contact"],
        "org_terms": ["company", "service", "provider", "office"],
        "osm_amenities": [],
        "reject_keywords": ["job", "jobs", "vacancy", "course", "courses", "training", "directory", "wikipedia"]
    }
}

ALIASES = {
    "pizza": ["pizzeria", "pizza restaurant", "pizza takeaway", "pizza shop", "italian restaurant"],
    "pizzas": ["pizzeria", "pizza restaurant", "pizza takeaway", "pizza shop", "italian restaurant"],
    "pizzeria": ["pizza restaurant", "pizza takeaway", "pizza shop", "italian restaurant"],
    "physical therapist": ["physiotherapist", "physio", "physical therapy", "physiotherapy", "rehabilitation", "sports physio"],
    "physical therapists": ["physiotherapist", "physio", "physical therapy", "physiotherapy", "rehabilitation", "sports physio"],
    "physiotherapist": ["physical therapist", "physio", "physiotherapy", "physical therapy", "rehabilitation"],
    "physiotherapists": ["physical therapists", "physio", "physiotherapy clinic", "rehabilitation clinic"],
    "doctor": ["medical practice", "clinic", "general practitioner", "gp"],
    "doctors": ["medical practices", "clinics", "general practitioners", "gp"],
    "dentist": ["dental practice", "dental clinic"],
    "dentists": ["dental practices", "dental clinics"],
    "psychologist": ["psychology practice", "therapy practice", "counsellor", "counselor"],
    "lawyer": ["attorney", "law firm", "legal practice"],
    "lawyers": ["attorneys", "law firms", "legal practices"],
    "ngo": ["nonprofit", "non-profit", "charity", "foundation", "community organisation", "community organization"],
}

def singularize_phrase(q):
    q = clean_name(q).lower()
    words = q.split()
    if not words:
        return q
    last = words[-1]
    if last.endswith("ies") and len(last) > 4:
        words[-1] = last[:-3] + "y"
    elif last.endswith("ses") and len(last) > 4:
        words[-1] = last[:-2]
    elif last.endswith("s") and len(last) > 3 and not last.endswith("ss"):
        words[-1] = last[:-1]
    return " ".join(words)

def pluralize_phrase(q):
    q = clean_name(q).lower()
    words = q.split()
    if not words:
        return q
    last = words[-1]
    if last.endswith("y"):
        words[-1] = last[:-1] + "ies"
    elif not last.endswith("s"):
        words[-1] = last + "s"
    return " ".join(words)

def detect_meta_category(query):
    q = safe_str(query).lower()
    best = ("general", 0)
    for meta, cfg in META_PROFILES.items():
        score = sum(1 for t in cfg.get("triggers", []) if t in q)
        if score > best[1]:
            best = (meta, score)
    return best[0]

def build_dynamic_profile(query):
    base = clean_name(query).lower()
    meta = detect_meta_category(base)
    cfg = META_PROFILES[meta]
    terms = []
    def add(x):
        x = clean_name(x).lower()
        if x and x not in terms:
            terms.append(x)
    add(base)
    add(singularize_phrase(base))
    add(pluralize_phrase(base))
    for a in ALIASES.get(base, []):
        add(a)
    for a in ALIASES.get(singularize_phrase(base), []):
        add(a)
    # Generic organization-type expansions. These are not sector-specific profiles;
    # they turn a service/person phrase into likely searchable organizations.
    singular = singularize_phrase(base)
    for org in cfg.get("org_terms", [])[:5]:
        add(f"{singular} {org}")
    # Healthcare-specific morphology: "therapy" -> "therapist", "physio" stays useful.
    if "therapy" in base:
        add(base.replace("therapy", "therapist"))
        add(base.replace("therapy", "clinic"))
    if "therapist" in base:
        add(base.replace("therapist", "therapy"))
        add(base.replace("therapist", "clinic"))
    core_tokens = [t for t in text_tokens(base) if t not in {"near", "me", "best", "top"}]
    keep = list(dict.fromkeys(core_tokens + [t for term in terms[:5] for t in text_tokens(term)]))
    profile = {
        "queries": terms[:10],
        "keep_keywords": [],  # do not over-filter dynamic searches; discovery query already narrows results
        "reject_keywords": list(dict.fromkeys(cfg.get("reject_keywords", []) + ["best of", "top 10", "list of", "directory-only"])),
        "page_paths": cfg.get("page_paths", META_PROFILES["general"]["page_paths"]),
        "official_terms": list(dict.fromkeys(terms[:8] + keep + cfg.get("org_terms", [])[:4])),
        "roles": cfg.get("roles", []),
        "meta_category": meta,
        "osm_amenities": cfg.get("osm_amenities", []),
        "osm_name_terms": terms[:6],
        "generated_from": query,
    }
    return profile

BAD_DOMAINS = [
    "google.", "bing.", "duckduckgo.", "yahoo.", "facebook.com", "instagram.com", "linkedin.com",
    "wikipedia.org", "mapcarta.com", "snupit.co.za", "saschools.co.za", "schoolguide.co.za",
    "schoolsdigest.co.za", "businesslist", "cybo.com", "brabys.com", "yellowpages", "yell.com",
    "tripadvisor", "booking.com", "property24", "gumtree", "indeed.com", "glassdoor",
]
DIRECTORY_HINTS = ["schoolguide", "saschools", "snupit", "brabys", "cybo", "businesslist", "directory", "yellowpages", "primaryschool", "kenyaprimaryschools", "schoolsinkenya", "educationnews", "schoolandcollegelistings", "waze", "foursquare", "yelp", "tripadvisor"]
# Country-aware domain handling
#
# v29 avoids hardcoding a few African countries only. It now uses a two-layer
# approach:
#   1) COUNTRY_SUFFIX_OVERRIDES for countries with known local education patterns
#   2) generated ccTLD patterns for any country that can be resolved to ISO-2
#
# This keeps Kenya/South Africa performance while making the resolver usable
# globally. Local suffixes are used as positive evidence; generic global domains
# (.com/.org/.net/.edu) are treated cautiously for non-US searches unless the page
# title/text strongly matches the prospect.
COUNTRY_SUFFIX_OVERRIDES = {
    "south africa": [".school.za", ".ac.za", ".edu.za", ".org.za", ".co.za", ".za"],
    "kenya": [".sc.ke", ".ac.ke", ".or.ke", ".co.ke", ".go.ke", ".ke"],
    "nigeria": [".edu.ng", ".sch.ng", ".org.ng", ".com.ng", ".ng"],
    "ghana": [".edu.gh", ".org.gh", ".com.gh", ".gh"],
    "uganda": [".ac.ug", ".co.ug", ".or.ug", ".ug"],
    "rwanda": [".ac.rw", ".co.rw", ".org.rw", ".rw"],
    "tanzania": [".ac.tz", ".co.tz", ".or.tz", ".tz"],
    "united kingdom": [".sch.uk", ".ac.uk", ".org.uk", ".co.uk", ".uk"],
    "uk": [".sch.uk", ".ac.uk", ".org.uk", ".co.uk", ".uk"],
    "australia": [".edu.au", ".org.au", ".com.au", ".au"],
    "new zealand": [".school.nz", ".ac.nz", ".org.nz", ".co.nz", ".nz"],
    "canada": [".ca"],
    "united states": [".edu", ".org", ".com", ".us"],
    "usa": [".edu", ".org", ".com", ".us"],
}

COUNTRY_ALIAS_TO_ALPHA2 = {
    "south africa": "ZA", "kenya": "KE", "nigeria": "NG", "ghana": "GH", "uganda": "UG",
    "rwanda": "RW", "tanzania": "TZ", "senegal": "SN", "zambia": "ZM", "mozambique": "MZ",
    "botswana": "BW", "namibia": "NA", "zimbabwe": "ZW", "united kingdom": "GB", "uk": "GB",
    "great britain": "GB", "england": "GB", "united states": "US", "usa": "US", "us": "US",
    "canada": "CA", "australia": "AU", "new zealand": "NZ",
}

GLOBAL_SUFFIXES = [".org", ".com", ".edu", ".net"]
DEFAULT_SUFFIXES = [".org", ".com", ".edu", ".net"]

def country_alpha2(country):
    c = safe_str(country).lower()
    if not c:
        return ""
    if c in COUNTRY_ALIAS_TO_ALPHA2:
        return COUNTRY_ALIAS_TO_ALPHA2[c]
    try:
        import pycountry
        hit = pycountry.countries.lookup(country)
        return hit.alpha_2.upper()
    except Exception:
        return ""

def generated_country_suffixes(country):
    c = safe_str(country).lower()
    override = COUNTRY_SUFFIX_OVERRIDES.get(c, [])
    alpha2 = country_alpha2(country)
    if not alpha2:
        return override[:] if override else DEFAULT_SUFFIXES[:]
    tld = "." + alpha2.lower()
    # Generic local-domain patterns that work across many countries. Some will not
    # exist everywhere, so guesses are still fetched and validated before use.
    generated = [
        f".school{tld}", f".sch{tld}", f".edu{tld}", f".ac{tld}",
        f".org{tld}", f".or{tld}", f".co{tld}", f".com{tld}", tld,
    ]
    out=[]
    for x in override + generated:
        if x not in out:
            out.append(x)
    return out

# ---------------- State / utils ----------------

def init_state():
    defaults = {
        "candidate_rows": None,
        "prospect_rows": None,
        "candidate_key": None,
        "enriched_key": None,
        "diagnostics": {},
        "debug_log": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

def log(msg):
    try:
        st.session_state.debug_log.append(str(msg))
    except Exception:
        pass

def safe_str(x):
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    return str(x).strip()

def slugify(s, maxlen=70):
    s = re.sub(r"[^A-Za-z0-9]+", "_", safe_str(s).lower()).strip("_")
    return (s[:maxlen] or "prospects")

def normalize_url(url):
    url = safe_str(url)
    if not url:
        return ""
    # unwrap DuckDuckGo redirect URLs
    try:
        if "uddg=" in url:
            parsed = urllib.parse.urlparse(url)
            url = urllib.parse.parse_qs(parsed.query).get("uddg", [url])[0]
    except Exception:
        pass
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        if not parsed.netloc or "." not in parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    except Exception:
        return ""

def get_domain(url):
    try:
        return urlparse(normalize_url(url)).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def bare_domain(url):
    d = get_domain(url)
    suffixes = []
    for vals in COUNTRY_SUFFIX_OVERRIDES.values():
        suffixes.extend(vals)
    suffixes += [".school.za", ".co.za", ".org.za", ".ac.za", ".edu.za", ".ac.ke", ".co.ke", ".or.ke", ".sc.ke", ".edu.ng", ".com.ng", ".org.ng", ".com", ".org", ".net", ".edu"]
    for suf in sorted(set(suffixes), key=len, reverse=True):
        if d.endswith(suf):
            return d[:-len(suf)]
    return d.split(".")[0]

def clean_name(s):
    s = safe_str(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def text_tokens(s):
    stop = {"the", "and", "for", "of", "at", "in", "on", "cape", "town", "south", "africa", "western", "kenya", "campus", "branch"}
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", safe_str(s).lower()).split() if len(t) > 1 and t not in stop]
def raw_tokens(s):
    """Tokens with minimal stopword removal, used for acronyms like CCT / HIC."""
    stop = {"the", "and", "for", "of", "at", "in", "on"}
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", safe_str(s).lower()).split() if len(t) > 1 and t not in stop]


def important_tokens(name):
    sector_words = {"school", "primary", "high", "college", "academy", "independent", "secondary", "pre", "prep", "preparatory", "campus", "learners"}
    return [t for t in text_tokens(name) if t not in sector_words]

def acronym(tokens):
    return "".join(t[0] for t in tokens if t and t[0].isalnum())

def candidate_key(inputs):
    return hashlib.md5(json.dumps(inputs, sort_keys=True).encode()).hexdigest()

def enrichment_key(cand_key, options):
    return hashlib.md5(json.dumps({"candidate_key": cand_key, **options}, sort_keys=True).encode()).hexdigest()

def fetch(url, timeout=10):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        ctype = r.headers.get("content-type", "")
        if r.status_code >= 400:
            return r.status_code, ""
        if "html" not in ctype and "text" not in ctype and not ctype:
            return r.status_code, ""
        return r.status_code, r.text[:650000]
    except Exception as e:
        return type(e).__name__, ""

def page_text_and_links(url, html):
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta_desc = ""
    md = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    if md:
        meta_desc = safe_str(md.get("content"))
    text = soup.get_text(" ", strip=True)
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        label = a.get_text(" ", strip=True)
        links.append((href, label))
    return title, meta_desc, text, links

# ---------------- Extraction ----------------

def get_country_code(country):
    return country_alpha2(country) or None

def extract_emails(text):
    if not text:
        return []
    t = re.sub(r"\s*\[at\]\s*|\s+\(at\)\s+|\s+at\s+", "@", text, flags=re.I)
    t = re.sub(r"\s*\[dot\]\s*|\s+\(dot\)\s+|\s+dot\s+", ".", t, flags=re.I)
    emails = re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", t)
    out = []
    for e in emails:
        e = e.strip(".,;:()[]<>").lower()
        if any(bad in e for bad in ["example.com", "email.com", "domain.com", "yourname"]):
            continue
        if e not in out:
            out.append(e)
    return out

def extract_phones(text, country):
    cc = get_country_code(country) or "US"
    out = []
    if not text:
        return out
    try:
        for match in phonenumbers.PhoneNumberMatcher(text, cc):
            num = match.number
            if phonenumbers.is_possible_number(num) and phonenumbers.is_valid_number(num):
                formatted = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
                # reject suspicious coordinate-like or placeholder numbers
                digits = re.sub(r"\D", "", formatted)
                if len(set(digits[-7:])) <= 2:
                    continue
                if formatted not in out:
                    out.append(formatted)
    except Exception:
        pass
    return out

# ---------------- Search + website resolution ----------------

def name_variants(name):
    name = clean_name(name)
    variants = []
    def add(x):
        x = clean_name(x)
        x = re.sub(r"\s+", " ", x).strip(" -–:,")
        if x and x.lower() not in [v.lower() for v in variants]:
            variants.append(x)
    add(name)
    add(re.sub(r"\([^)]*\)", "", name))

    # Parent/campus handling: strip campus/branch qualifiers but preserve parent institution.
    parent = re.sub(r"\s*[-–:]?\s*\b[A-Za-z ]*Campus\b.*$", "", name, flags=re.I)
    add(parent)
    parent = re.sub(r"\s*\b(Pre[- ]?Prep|Pre[- ]?Primary|Preparatory|Junior School|Primary School)\b.*$", "", name, flags=re.I)
    add(parent)

    # Common abbreviated parent form for institutions like College of Cape Town / Hidayatul Islam College.
    toks_raw = raw_tokens(name)
    if len(toks_raw) >= 2:
        add(" ".join(toks_raw[:2]))
    if len(toks_raw) >= 3:
        add(" ".join(toks_raw[:3]))

    toks_imp = important_tokens(name)
    if toks_imp:
        add(" ".join(toks_imp))
        if len(toks_imp) >= 2:
            add(" ".join(toks_imp[:2]))
    return [v for v in variants if len(v) >= 3]
def country_suffixes(country):
    return generated_country_suffixes(country)

def domain_guesses(name, country):
    """Conservative official-domain guesses.

    v28 intentionally avoids generic .com/.org guesses for country-specific searches.
    That prevents false positives such as olm.com, daystar.com, palmolive.org,
    nairobi.com, etc. Search results can still return a .com/.org domain, but
    those must pass validation instead of being accepted as guesses.
    """
    guesses = []
    suffixes = country_suffixes(country)
    for variant in name_variants(name)[:6]:
        toks_all = text_tokens(variant)
        toks_raw = raw_tokens(variant)
        toks_imp = important_tokens(variant) or toks_all or toks_raw
        base_options = []

        def add_base(x):
            x = re.sub(r"[^a-z0-9-]", "", safe_str(x).lower())
            # Avoid very short/generic domains unless they are local institutional suffixes.
            if 3 <= len(x) <= 45 and x not in base_options:
                base_options.append(x)

        for toks in [toks_imp, toks_all, toks_raw]:
            if toks:
                # Compact full names are safer than first-token guesses.
                if len(toks) >= 2:
                    add_base("".join(toks))
                    add_base("-".join(toks))
                ac = acronym(toks)
                if 3 <= len(ac) <= 8:
                    add_base(ac)

        # Local schools sometimes use first two/three tokens or acronym + sector.
        if toks_raw:
            if len(toks_raw) >= 2:
                add_base("".join(toks_raw[:2]))
                add_base("-".join(toks_raw[:2]))
            if len(toks_raw) >= 3:
                add_base("".join(toks_raw[:3]))
                add_base("-".join(toks_raw[:3]))
            if "school" in toks_raw and len(toks_raw[0]) >= 4:
                add_base(toks_raw[0] + "school")
            if "college" in toks_raw and len(toks_raw[0]) >= 4:
                add_base(toks_raw[0] + "college")

        for b in base_options:
            for suf in suffixes:
                guesses.append(f"https://www.{b}{suf}")
                guesses.append(f"https://{b}{suf}")
    out, seen = [], set()
    for g in guesses:
        d = get_domain(g)
        if d and d not in seen:
            seen.add(d); out.append(g)
    return out[:80]

def is_bad_result_url(url):
    d = get_domain(url)
    if not d: return True
    return any(b in d for b in BAD_DOMAINS)

def local_domain_score(url, country):
    d = get_domain(url)
    if not d:
        return 0
    suffixes = country_suffixes(country)
    for suf in suffixes:
        if d.endswith(suf):
            # More specific local education/institution domains get stronger signal.
            if any(x in suf for x in ["school", "sch", "edu", "ac"]):
                return 22
            return 16
    # Country-specific searches should be skeptical of global generic domains,
    # except for the US where .edu/.org/.com are normal. This is a penalty, not
    # an automatic rejection; strong page evidence can still override it.
    alpha2 = country_alpha2(country)
    if alpha2 and alpha2 not in {"US"} and any(d.endswith(x) for x in GLOBAL_SUFFIXES):
        return -18
    return 0

def identity_evidence(prospect_name, location_hint, title="", snippet="", page_text=""):
    hay = " ".join([safe_str(title), safe_str(snippet), safe_str(page_text)[:3500]]).lower()
    raw = raw_tokens(prospect_name)
    loc_toks = set(text_tokens(location_hint))
    sector_words = {"school","primary","secondary","high","college","academy","university","campus","nursery","junior","pre","prep","preparatory"}
    distinctive = [t for t in raw if t not in loc_toks and t not in sector_words and len(t) > 2]
    sector = [t for t in raw if t in sector_words]
    matched_distinctive = [t for t in distinctive if t in hay]
    matched_raw = [t for t in raw if len(t) > 2 and t in hay]
    phrase = " ".join(raw)
    phrase_hit = phrase and phrase in hay
    education_hit = any(k in hay for k in ["school", "college", "academy", "primary", "secondary", "admissions", "learners", "students", "curriculum", "principal"])
    return {
        "distinctive": distinctive,
        "matched_distinctive": matched_distinctive,
        "matched_raw": matched_raw,
        "phrase_hit": phrase_hit,
        "education_hit": education_hit,
        "sector_terms": sector,
    }

def looks_like_official_site(url, prospect_name, location_hint, country, title="", snippet="", page_text=""):
    """Strict official-site validation for v28.

    Search result snippets alone can be misleading. For high confidence we require
    evidence from title/page text or a strong local-domain match. This avoids
    accepting generic collisions like palmolive.org or olympic.edu.
    """
    if is_bad_result_url(url):
        return False
    d = get_domain(url)
    b = bare_domain(url).replace("-", "")
    ev = identity_evidence(prospect_name, location_hint, title, snippet, page_text)
    raw = raw_tokens(prospect_name)
    compact = "".join(raw)
    local_bonus = local_domain_score(url, country)

    # Strong local exact-ish domain + some page evidence.
    if local_bonus > 0 and compact and compact in b and (ev["education_hit"] or ev["phrase_hit"] or len(ev["matched_raw"]) >= 2):
        return True
    # Non-local generic domains need distinctive evidence from page text/title.
    if local_bonus < 0 and len(ev["matched_distinctive"]) < 1 and not ev["phrase_hit"]:
        return False
    # If the only match is a city/location token, reject.
    if not ev["matched_distinctive"] and not ev["phrase_hit"] and len(ev["matched_raw"]) < 2:
        return False
    return ev["education_hit"] or ev["phrase_hit"] or len(ev["matched_raw"]) >= 2

def score_website_candidate(url, prospect_name, location_hint="", title="", snippet="", page_text="", country=""):
    d = get_domain(url)
    if not d or is_bad_result_url(url):
        return -100
    bdom = bare_domain(url).replace("-", "")
    variants = name_variants(prospect_name)
    loc_tokens = set(text_tokens(location_hint))
    raw = raw_tokens(prospect_name)
    distinctive = [t for t in raw if t not in loc_tokens and t not in {"school","primary","secondary","high","college","academy","university","campus","nursery","junior","pre","prep","preparatory"}]
    name_toks = set(distinctive or [t for t in raw if t not in loc_tokens] or raw)
    hay = " ".join([d, safe_str(title).lower(), safe_str(snippet).lower(), safe_str(page_text).lower()[:3500]])
    score = 0

    score += local_domain_score(url, country)

    # Domain/name similarity. Do not reward a domain that is only the city/location.
    for v in variants:
        vtoks = raw_tokens(v)
        vtoks_nonloc = [t for t in vtoks if t not in loc_tokens]
        compact = "".join(vtoks)
        compact_nonloc = "".join(vtoks_nonloc)
        if compact and len(compact) >= 6 and compact in bdom:
            score += 28
        if compact_nonloc and len(compact_nonloc) >= 6 and compact_nonloc in bdom:
            score += 26
        if bdom and compact_nonloc and len(compact_nonloc) >= 5:
            score += int(22 * SequenceMatcher(None, compact_nonloc, bdom).ratio())
        ac = acronym(vtoks_nonloc or vtoks)
        if ac and len(ac) >= 3 and ac == bdom:
            score += 28
        elif ac and len(ac) >= 4 and ac in bdom:
            score += 14

    matched = [t for t in name_toks if len(t) > 2 and t in hay]
    score += min(36, len(matched) * 12)
    if name_toks and len(matched) >= max(1, min(2, len(name_toks))):
        score += 12
    phrase = " ".join(raw)
    if phrase and phrase in hay:
        score += 35
    if any(k in hay for k in ["school", "college", "academy", "primary", "secondary", "admissions", "learners", "students", "principal"]):
        score += 18
    if any(k in hay for k in ["official", "welcome to", "contact us", "admissions"]):
        score += 7
    if any(k in d for k in DIRECTORY_HINTS):
        score -= 30
    if any(k in d for k in ["gov.za", "education.gov"]):
        score -= 8
    # Hard penalty for obvious brand/location-only collisions.
    ev = identity_evidence(prospect_name, location_hint, title, snippet, page_text)
    if local_domain_score(url, country) < 0 and not ev["phrase_hit"] and len(ev["matched_distinctive"]) == 0:
        score -= 50
    return score

def parse_search_results(html, source, max_results=8):
    results = []
    soup = BeautifulSoup(html or "", "html.parser")
    if source == "ddg":
        nodes = soup.select("a.result__a") or soup.select("a.result-link")
        for a in nodes[:max_results]:
            href = a.get("href", "")
            title = a.get_text(" ", strip=True)
            if "uddg=" in href:
                try:
                    href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
                except Exception:
                    pass
            results.append({"url": href, "title": title, "snippet": "", "source": source})
    elif source == "bing":
        for li in soup.select("li.b_algo")[:max_results]:
            a = li.find("a", href=True)
            if not a: continue
            p = li.find("p")
            results.append({"url": a.get("href", ""), "title": a.get_text(" ", strip=True), "snippet": p.get_text(" ", strip=True) if p else "", "source": source})
    else:
        for a in soup.find_all("a", href=True)[:max_results*3]:
            href = a.get("href", "")
            title = a.get_text(" ", strip=True)
            if href.startswith("http") and title:
                results.append({"url": href, "title": title, "snippet": "", "source": source})
            if len(results) >= max_results:
                break
    return results

def web_search(query, max_results=8, timeout=10):
    out = []
    # DuckDuckGo HTML
    urls = [
        ("ddg", "https://duckduckgo.com/html/?q=" + quote_plus(query)),
        ("bing", "https://www.bing.com/search?q=" + quote_plus(query)),
    ]
    for source, url in urls:
        status, html = fetch(url, timeout=timeout)
        if html:
            out.extend(parse_search_results(html, source, max_results=max_results))
        if len(out) >= max_results:
            break
    # dedupe domains/urls
    seen, deduped = set(), []
    for r in out:
        u = normalize_url(r.get("url"))
        if not u or u in seen or is_bad_result_url(u):
            continue
        seen.add(u); r["url"] = u; deduped.append(r)
        if len(deduped) >= max_results:
            break
    return deduped

def get_optional_secret(name):
    try:
        v = st.secrets.get(name, "")
        if v:
            return str(v)
    except Exception:
        pass
    try:
        import os
        return os.environ.get(name, "") or ""
    except Exception:
        return ""

def google_places_lookup(name, location_hint):
    """Optional paid/API-backed resolver. If GOOGLE_PLACES_API_KEY is absent, does nothing."""
    key = get_optional_secret("GOOGLE_PLACES_API_KEY")
    if not key:
        return None
    query = f"{name} {location_hint}"
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": query, "key": key}, headers=HEADERS, timeout=10
        )
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        place_id = results[0].get("place_id")
        if not place_id:
            return None
        d = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={"place_id": place_id, "fields": "name,website,formatted_phone_number,international_phone_number,url", "key": key},
            headers=HEADERS, timeout=10
        ).json().get("result") or {}
        website = normalize_url(d.get("website", ""))
        if not website:
            return None
        return {
            "url": website,
            "phone": d.get("international_phone_number") or d.get("formatted_phone_number") or "",
            "method": "google_places",
            "title": d.get("name", ""),
        }
    except Exception:
        return None

def resolve_website_for_row(row, location_hint, search_level):
    current = normalize_url(row.get("website", ""))
    if current:
        return current, "map/open data", "", "high"

    name = safe_str(row.get("prospect_name"))
    country = safe_str(row.get("country")) or ("South Africa" if "South Africa" in location_hint else "")
    city = safe_str(row.get("city"))
    loc = ", ".join([x for x in [city, location_hint] if x])
    candidates = []  # dicts: url, score, method, title

    # Optional high-reliability source. Configure GOOGLE_PLACES_API_KEY in Streamlit secrets.
    gp = google_places_lookup(name, location_hint)
    if gp and gp.get("url"):
        return gp["url"], "google_places", gp["url"] + " [100, google_places]", "high"

    # Domain guesses: broader than prior versions, because many schools use predictable domains.
    guess_limit = 28 if search_level == "Normal" else 70
    for gu in domain_guesses(name, country)[:guess_limit]:
        status, html = fetch(gu, timeout=5 if search_level == "Normal" else 7)
        if not html:
            continue
        title, meta, text, _ = page_text_and_links(gu, html)
        score = score_website_candidate(gu, name, loc, title, meta, text[:1500], country) + 8
        candidates.append({"url": normalize_url(gu), "score": score, "method": "domain_guess_verified", "title": title})

    # Search queries. Normal still searches hard enough to find obvious sites.
    variants = name_variants(name)[:3]
    queries = []
    for v in variants:
        queries += [
            f'"{v}" official website',
            f'"{v}" website',
            f'"{v}" "{city or location_hint}" school',
            f'{v} school {location_hint}',
            f'"{v}" contact',
        ]
    if search_level == "Extra thorough":
        for v in variants:
            queries += [f'"{v}" admissions', f'{v} school website {location_hint}', f'"{v}" phone', f'"{v}" email']
    # dedupe queries
    qseen, qlist = set(), []
    for q in queries:
        if q.lower() not in qseen:
            qseen.add(q.lower()); qlist.append(q)
    qlimit = 7 if search_level == "Normal" else 14
    for q in qlist[:qlimit]:
        for res in web_search(q, max_results=8 if search_level == "Normal" else 12, timeout=8 if search_level == "Normal" else 11):
            u = normalize_url(res.get("url"))
            if not u:
                continue
            score = score_website_candidate(u, name, loc, res.get("title", ""), res.get("snippet", ""), "", country)
            candidates.append({"url": u, "score": score, "method": f"search_{res.get('source','web')}", "title": res.get("title", "")})

    # Dedupe by domain, keep top score.
    best_by_domain = {}
    for c in candidates:
        d = get_domain(c["url"])
        if not d or is_bad_result_url(c["url"]):
            continue
        if d not in best_by_domain or c["score"] > best_by_domain[d]["score"]:
            best_by_domain[d] = c
    ranked = sorted(best_by_domain.values(), key=lambda c: c["score"], reverse=True)

    # Validate top candidates by opening pages. This is essential outside South Africa,
    # where naive domain guesses can collide with unrelated brands/global domains.
    validated = []
    for c in ranked[:4 if search_level == "Normal" else 7]:
        status, html = fetch(c["url"], timeout=6 if search_level == "Normal" else 9)
        if html:
            title, meta, text, _ = page_text_and_links(c["url"], html)
            c2 = dict(c)
            c2["score"] = max(c["score"], score_website_candidate(c["url"], name, loc, title, meta, text[:3500], country) + 5)
            c2["official_ok"] = looks_like_official_site(c["url"], name, loc, country, title, meta, text[:4000])
            c2["title"] = title or c.get("title", "")
            c2["method"] = c.get("method", "search") + "_validated"
            validated.append(c2)
        else:
            c2 = dict(c); c2["official_ok"] = False; validated.append(c2)
    if validated:
        # Merge validated scores back
        for vc in validated:
            d = get_domain(vc["url"])
            if d in best_by_domain and vc["score"] > best_by_domain[d]["score"]:
                best_by_domain[d] = vc
        ranked = sorted(best_by_domain.values(), key=lambda c: c["score"], reverse=True)

    candidates_str = "; ".join([f"{c['url']} [{c['score']}, {c['method']}]" for c in ranked[:7]])
    if not ranked:
        return "", "not_found", "", "none"

    top = ranked[0]
    # v28: accept only validated official sites as the primary website.
    # Plausible but unvalidated sites are preserved in website_candidates instead of
    # being exported as if they were official.
    # Accept only sites that validate as official. Directory pages stay in candidates.
    if top.get("official_ok") and top["score"] >= 55 and not any(h in get_domain(top["url"]) for h in DIRECTORY_HINTS):
        return top["url"], top["method"], candidates_str, "high" if top["score"] >= 78 else "medium"
    if local_domain_score(top["url"], country) > 0 and top["score"] >= 70 and not any(h in get_domain(top["url"]) for h in DIRECTORY_HINTS):
        return top["url"], top["method"], candidates_str, "medium"
    return "", "directory_or_unverified_candidates", candidates_str, "none"

# ---------------- Discovery ----------------

def geocode(location):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": location, "format": "jsonv2", "limit": 1, "addressdetails": 1}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=20)
        log(f"Geocode HTTP {r.status_code}: {r.url}")
        js = r.json()
        if not js:
            return None
        item = js[0]
        return {"lat": float(item["lat"]), "lon": float(item["lon"]), "display_name": item.get("display_name", ""), "country": item.get("address", {}).get("country", "")}
    except Exception as e:
        log(f"Geocode error: {type(e).__name__}: {e}")
        return None

def nominatim_search(query, sector, limit=50):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 1, "extratags": 1, "namedetails": 1}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=25)
        log(f"Nominatim '{query}': HTTP {r.status_code}")
        js = r.json()
    except Exception as e:
        log(f"Nominatim error '{query}': {type(e).__name__}: {e}")
        return []
    rows = []
    for x in js if isinstance(js, list) else []:
        name = (x.get("namedetails") or {}).get("name") or x.get("name") or safe_str(x.get("display_name", "")).split(",")[0]
        addr = x.get("address") or {}
        extra = x.get("extratags") or {}
        rows.append({
            "prospect_name": name,
            "sector": sector,
            "source": "nominatim",
            "address": x.get("display_name", ""),
            "city": addr.get("city") or addr.get("town") or addr.get("municipality") or addr.get("suburb") or "",
            "country": addr.get("country") or "",
            "latitude": x.get("lat", ""),
            "longitude": x.get("lon", ""),
            "website": normalize_url(extra.get("website") or extra.get("url") or extra.get("contact:website") or ""),
            "osm_phone": extra.get("phone") or extra.get("contact:phone") or "",
            "osm_email": extra.get("email") or extra.get("contact:email") or "",
            "website_source": "map/open data" if (extra.get("website") or extra.get("url") or extra.get("contact:website")) else "",
            "website_confidence": "high" if (extra.get("website") or extra.get("url") or extra.get("contact:website")) else "",
        })
    return rows

def overpass_search(lat, lon, radius_m, sector, limit):
    profile = SECTOR_PROFILES.get(sector, SECTOR_PROFILES["General Organizations"])
    amenities = profile.get("osm_amenities") or (["school", "college", "university"] if sector in ["Schools", "Universities / Colleges"] else [])
    clauses = []
    if amenities:
        amenity_re = "|".join([re.escape(a) for a in amenities])
        for obj in ["node", "way", "relation"]:
            clauses.append(f'{obj}["amenity"~"{amenity_re}"](around:{radius_m},{lat},{lon});')
    # Dynamic custom searches: add name/brand/cuisine text matches so generic terms like
    # “pizza” do not return zero results just because there is no exact amenity value.
    name_terms = profile.get("osm_name_terms", []) if sector == "Custom" else []
    if name_terms:
        compact_terms = []
        for term in name_terms:
            t = safe_str(term).lower().strip()
            if len(t) >= 3 and t not in compact_terms:
                compact_terms.append(t)
        # Limit regex complexity for Overpass reliability.
        term_re = "|".join([re.escape(t) for t in compact_terms[:8]])
        if term_re:
            for obj in ["node", "way", "relation"]:
                clauses.append(f'{obj}["name"~"{term_re}",i](around:{radius_m},{lat},{lon});')
                clauses.append(f'{obj}["brand"~"{term_re}",i](around:{radius_m},{lat},{lon});')
            # Food-specific support, e.g. pizza/pizzeria searches.
            if any(t in term_re for t in ["pizza", "pizzeria", "italian"]):
                for obj in ["node", "way", "relation"]:
                    clauses.append(f'{obj}["cuisine"~"pizza|italian",i](around:{radius_m},{lat},{lon});')
    if not clauses:
        return []
    q = f"""[out:json][timeout:25];({''.join(clauses)});out center tags {max(limit * 3, 100)};"""
    endpoints = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.osm.ch/api/interpreter"]
    for ep in endpoints:
        try:
            r = requests.post(ep, data={"data": q}, headers=HEADERS, timeout=30)
            log(f"Overpass POST {ep}: HTTP {r.status_code}")
            if r.status_code != 200:
                continue
            data = r.json()
            elems = data.get("elements", [])
            rows = []
            for e in elems:
                tags = e.get("tags") or {}
                name = tags.get("name") or tags.get("official_name") or tags.get("brand") or ""
                if not name:
                    continue
                lat2 = e.get("lat") or (e.get("center") or {}).get("lat")
                lon2 = e.get("lon") or (e.get("center") or {}).get("lon")
                address = ", ".join([safe_str(tags.get(k)) for k in ["addr:housenumber", "addr:street", "addr:city"] if safe_str(tags.get(k))])
                rows.append({
                    "prospect_name": name,
                    "sector": sector,
                    "source": "overpass",
                    "address": address,
                    "city": tags.get("addr:city", ""),
                    "country": tags.get("addr:country", ""),
                    "latitude": lat2,
                    "longitude": lon2,
                    "website": normalize_url(tags.get("website") or tags.get("contact:website") or ""),
                    "osm_phone": tags.get("phone") or tags.get("contact:phone") or "",
                    "osm_email": tags.get("email") or tags.get("contact:email") or "",
                    "website_source": "map/open data" if (tags.get("website") or tags.get("contact:website")) else "",
                    "website_confidence": "high" if (tags.get("website") or tags.get("contact:website")) else "",
                })
            log(f"Overpass {ep}: {len(rows)} candidates")
            if rows:
                return rows[:limit]
        except Exception as e:
            log(f"Overpass error {ep}: {type(e).__name__}: {e}")
    return []

def is_false_positive(row, sector):
    name = safe_str(row.get("prospect_name")).lower()
    addr = safe_str(row.get("address")).lower()
    hay = name + " " + addr
    profile = SECTOR_PROFILES[sector]
    if any(bad in hay for bad in profile["reject_keywords"]):
        return True
    keep = profile["keep_keywords"]
    if keep and not any(k in hay for k in keep):
        return True
    return False

def dedupe_rows(rows, sector, max_candidates):
    seen, out = set(), []
    for r in rows:
        if is_false_positive(r, sector):
            continue
        name = safe_str(r.get("prospect_name"))
        if not name:
            continue
        # Dedupe by normalized name first, then coordinates.
        name_key = re.sub(r"[^a-z0-9]+", "", name.lower())[:60]
        coord_key = safe_str(r.get("latitude"))[:8] + "|" + safe_str(r.get("longitude"))[:8]
        key = name_key if name_key else coord_key
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= max_candidates:
            break
    return out

def discover_map(location, radius_km, max_candidates, sector):
    geo = geocode(location)
    rows = []
    if geo:
        rows += overpass_search(geo["lat"], geo["lon"], int(radius_km * 1000), sector, max_candidates)
    profile = SECTOR_PROFILES[sector]
    # Supplement with Nominatim because Overpass metadata varies across endpoints.
    for qterm in profile["queries"]:
        if len(dedupe_rows(rows, sector, max_candidates)) >= max_candidates:
            break
        # Try both forms; Nominatim handles some categories better as "X in Y" and
        # others better as "X Y".
        rows += nominatim_search(f"{qterm} in {location}", sector, limit=max_candidates)
        if sector == "Custom" and len(dedupe_rows(rows, sector, max_candidates)) < max_candidates:
            rows += nominatim_search(f"{qterm} {location}", sector, limit=max_candidates)
    return dedupe_rows(rows, sector, max_candidates)

# ---------------- Pipeline ----------------

def resolve_websites(rows, location_hint, search_level, workers, progress):
    rows = [dict(r) for r in rows]
    todo = [i for i, r in enumerate(rows) if not safe_str(r.get("website"))]
    total = max(1, len(todo))
    if not todo:
        progress.progress(1.0, text="Step 2 complete: all prospects already had websites")
        return rows

    def worker(i):
        r = rows[i]
        url, method, cands, confidence = resolve_website_for_row(r, location_hint, search_level)
        return i, url, method, cands, confidence

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(worker, i) for i in todo]
        for fut in as_completed(futs):
            try:
                i, url, method, cands, confidence = fut.result()
                if url:
                    rows[i]["website"] = url
                # Always keep method/candidates/confidence, even if no selected site.
                rows[i]["website_source"] = method
                rows[i]["website_candidates"] = cands
                rows[i]["website_confidence"] = confidence
                if rows[i].get("website"):
                    rows[i]["official_website_status"] = "verified official" if confidence == "high" else "likely official"
                elif cands:
                    rows[i]["official_website_status"] = "not found - candidates retained"
                else:
                    rows[i]["official_website_status"] = "not found"
            except Exception as e:
                pass
            done += 1
            progress.progress(done / total, text=f"Step 2 of 3: Finding official websites ({done}/{total})")
    return rows

def scrape_one(row, search_level, find_more_contacts):
    r = dict(row)
    country = r.get("country", "")
    site = normalize_url(r.get("website", ""))
    if not site:
        r.update({"enrichment_status": "no_website"})
        return r
    profile = SECTOR_PROFILES.get(r.get("sector", "Schools"), SECTOR_PROFILES["Schools"])
    # v28: Normal mode still crawls enough school-relevant pages to find Kenyan/SA contacts.
    max_paths = 9 if search_level == "Normal" else len(profile["page_paths"])
    paths = profile["page_paths"][:max_paths]
    emails, phones, pages = [], [], []

    def add_contacts_from_html(url, html):
        title, meta, text, links = page_text_and_links(url, html)
        # Include hrefs because many emails/phones live in mailto:/tel: links rather than visible text.
        href_text = " ".join([h for h, _ in links])
        combined = text + " " + href_text
        for e in extract_emails(combined):
            if e not in emails:
                emails.append(e)
        for p in extract_phones(combined, country):
            if p not in phones:
                phones.append(p)
        return title, meta, text, links

    homepage_links = []
    for path in paths:
        url = site if not path else urljoin(site + "/", path)
        status, html = fetch(url, timeout=8 if search_level == "Normal" else 12)
        if not html:
            continue
        pages.append(url)
        title, meta, text, links = add_contacts_from_html(url, html)
        if path == "":
            homepage_links = links

    # Follow relevant internal contact links found on homepage in Normal and Extra thorough.
    contact_links = []
    for href, label in homepage_links:
        lab = (label + " " + href).lower()
        if get_domain(href) == get_domain(site) and any(k in lab for k in ["contact", "admission", "staff", "office", "reception", "enrol", "apply", "leadership", "team"]):
            contact_links.append(href)
    link_budget = 4 if search_level == "Normal" else 10
    for href in list(dict.fromkeys(contact_links))[:link_budget]:
        st2, h2 = fetch(href, timeout=9 if search_level == "Normal" else 12)
        if not h2:
            continue
        pages.append(href)
        add_contacts_from_html(href, h2)

    # Optional search fallback for contacts only when explicitly enabled and missing data.
    if find_more_contacts and (not emails or not phones):
        name = safe_str(r.get("prospect_name"))
        loc = safe_str(r.get("city")) or safe_str(r.get("country"))
        queries = [f'"{name}" contact', f'"{name}" phone', f'"{name}" email', f'"{name}" admissions']
        if loc:
            queries.append(f'"{name}" "{loc}" contact')
        seen_pages = set(pages)
        for q in queries[:3 if search_level == "Normal" else 6]:
            for res in web_search(q, max_results=4, timeout=7):
                u = normalize_url(res.get("url"))
                if not u or u in seen_pages or is_bad_result_url(u):
                    continue
                # Prefer official domain, but allow directories for contact fallback.
                st3, h3 = fetch(u, timeout=7)
                if not h3:
                    continue
                seen_pages.add(u); pages.append(u)
                add_contacts_from_html(u, h3)
                if emails and phones:
                    break
            if emails and phones:
                break

    generic = [e for e in emails if re.match(r"^(info|admin|admissions|admission|office|reception|enrol|enrolments|contact|secretary|registrar)@", e)]
    osm_email = safe_str(r.get("osm_email"))
    osm_phone = safe_str(r.get("osm_phone"))
    r["visible_emails"] = "; ".join(emails)
    r["generic_emails"] = "; ".join(generic)
    r["best_email"] = generic[0] if generic else (emails[0] if emails else osm_email)
    r["email_source"] = "website_generic" if generic else ("website_visible" if emails else ("osm" if osm_email else ""))
    r["website_phone"] = "; ".join(phones[:4])
    r["best_phone"] = phones[0] if phones else osm_phone
    r["phone_source"] = "website" if phones else ("osm" if osm_phone else "")
    r["source_pages"] = "; ".join(list(dict.fromkeys(pages))[:10])
    r["enrichment_status"] = "scraped" if pages else "scrape_failed"
    return r

def enrich_rows(rows, search_level, find_more_contacts, workers, progress):
    rows = [dict(r) for r in rows]
    total = max(1, len(rows))
    results = {}
    def worker(idx, row):
        return idx, scrape_one(row, search_level, find_more_contacts)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(worker, i, r) for i, r in enumerate(rows)]
        for fut in as_completed(futs):
            try:
                idx, rr = fut.result()
                results[idx] = rr
            except Exception:
                pass
            done += 1
            progress.progress(done / total, text=f"Step 3 of 3: Enriching contact details ({done}/{total})")
    return [results.get(i, rows[i]) for i in range(len(rows))]

def export_bytes(df, excel=False):
    if not excel:
        return df.to_csv(index=False).encode("utf-8")
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Prospects")
    return bio.getvalue()

# ---------------- UI ----------------

st.title("Prospect Discovery Engine")
st.caption(f"{APP_VERSION} — optimized Schools mode plus dynamic Custom Search profiles; optional Google Places support")

st.markdown("## Search criteria")
st.write("Choose what you want to find and where to search. Schools use the optimized school-specific profile. Custom search uses algorithmic profile expansion from your input.")
search_mode = st.radio("Prospect type", ["Schools (optimized)", "Custom search"], horizontal=True, key="main_search_mode")
if search_mode == "Schools (optimized)":
    sector = "Schools"
    custom_query = ""
    st.caption("Using optimized school discovery: school-specific website resolution, false-positive filters, and school contact pages.")
else:
    custom_query = st.text_input("What are you looking for?", "physical therapists", key="main_custom_query")
    sector = "Custom"
    SECTOR_PROFILES["Custom"] = build_dynamic_profile(custom_query)
    with st.expander("Generated search profile", expanded=False):
        prof = SECTOR_PROFILES["Custom"]
        st.write("**Detected category:**", prof.get("meta_category", "general"))
        st.write("**Search terms:**", ", ".join(prof.get("queries", [])[:10]))
        st.write("**Priority pages:**", ", ".join(prof.get("page_paths", [])[:10]))
        st.write("**Target roles:**", ", ".join(prof.get("roles", [])[:10]))
        st.write("**Exclude terms:**", ", ".join(prof.get("reject_keywords", [])[:10]))
location = st.text_input("Location", "Cape Town, Western Cape, South Africa", key="main_location_input")
col_c, col_d = st.columns(2)
with col_c:
    radius_km = st.slider("Search radius (km)", 1, 100, 10, key="main_radius_slider")
with col_d:
    max_candidates = st.slider("Maximum prospects", 10, 250, 50, step=10, key="main_max_prospects_slider")

with st.sidebar:
    st.header("Advanced settings")
    if get_optional_secret("GOOGLE_PLACES_API_KEY"):
        st.success("Google Places website lookup enabled")
    else:
        st.caption("Free lookup only. Add GOOGLE_PLACES_API_KEY in Streamlit secrets for near-complete official website/phone coverage.")
    search_level = st.radio("Search depth", ["Normal", "Extra thorough"], index=0, help="Normal searches for official websites and basic contacts. Extra thorough tries more queries/pages and takes longer.")
    find_more_contacts = st.checkbox("Find more contact details when missing", value=False)
    speed_label = st.select_slider("Processing speed", options=["Safe", "Balanced", "Fast"], value="Balanced")
    workers = {"Safe": 2, "Balanced": 5, "Fast": 8}[speed_label]
    st.divider()
    if st.button("Clear results"):
        for k in ["candidate_rows", "prospect_rows", "candidate_key", "enriched_key", "diagnostics", "debug_log"]:
            st.session_state[k] = [] if k == "debug_log" else None
        st.rerun()

inputs = {"sector": sector, "custom_query": custom_query if sector == "Custom" else "", "profile": SECTOR_PROFILES.get(sector, {}), "location": location.strip(), "radius_km": radius_km, "max_candidates": max_candidates}
ckey = candidate_key(inputs)
eopts = {"search_level": search_level, "find_more_contacts": find_more_contacts, "speed": speed_label, "workers": workers}
ekey = enrichment_key(ckey, eopts)

run = st.button("Find prospects", type="primary", use_container_width=True)

if run:
    st.session_state.debug_log = []
    t0 = time.time()
    p1 = st.progress(0, text="Step 1 of 3: Finding prospects")
    p2 = st.progress(0, text="Step 2 of 3: Finding official websites")
    p3 = st.progress(0, text="Step 3 of 3: Enriching contact details")

    if st.session_state.candidate_key == ckey and st.session_state.candidate_rows is not None:
        candidates = [dict(r) for r in st.session_state.candidate_rows]
        st.info(f"Using cached prospect list: {len(candidates)} prospects. Rechecking websites/contact details only.")
        p1.progress(1.0, text=f"Step 1 complete: using cached prospect list ({len(candidates)} prospects)")
        discovery_seconds = 0.0
    else:
        st.info("Starting new prospect search…")
        ts = time.time()
        candidates = discover_map(location, radius_km, max_candidates, sector)
        discovery_seconds = time.time() - ts
        st.session_state.candidate_rows = [dict(r) for r in candidates]
        st.session_state.candidate_key = ckey
        p1.progress(1.0, text=f"Step 1 complete: found {len(candidates)} prospects")

    ts = time.time()
    with_websites = resolve_websites(candidates, location, search_level, workers, p2)
    website_seconds = time.time() - ts
    ts = time.time()
    prospects = enrich_rows(with_websites, search_level, find_more_contacts, workers, p3)
    enrichment_seconds = time.time() - ts

    st.session_state.prospect_rows = prospects
    st.session_state.enriched_key = ekey
    st.session_state.diagnostics = {
        "discovery_seconds": round(discovery_seconds, 2),
        "website_resolution_seconds": round(website_seconds, 2),
        "enrichment_seconds": round(enrichment_seconds, 2),
        "total_seconds": round(time.time() - t0, 2),
        "search_depth": search_level,
        "processing_speed": speed_label,
    }
    st.success(f"Done: {len(prospects)} prospects ready.")

if st.session_state.prospect_rows:
    df = pd.DataFrame(st.session_state.prospect_rows)
    show_cols = [c for c in [
        "prospect_name", "sector", "city", "country", "website", "official_website_status", "website_confidence", "website_source", "best_email", "best_phone",
        "enrichment_status", "email_source", "phone_source", "website_candidates", "source_pages"
    ] if c in df.columns]
    st.subheader("Prospects")
    st.dataframe(df[show_cols], use_container_width=True, hide_index=True)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Prospects", len(df))
    c2.metric("Websites", int(df.get("website", pd.Series(dtype=str)).fillna("").astype(str).str.len().gt(0).sum()) if "website" in df else 0)
    c3.metric("High/Med confidence sites", int(df.get("website_confidence", pd.Series(dtype=str)).fillna("").astype(str).str.contains("high|medium", case=False, regex=True).sum()) if "website_confidence" in df else 0)
    c4.metric("Emails", int(df.get("best_email", pd.Series(dtype=str)).fillna("").astype(str).str.len().gt(0).sum()) if "best_email" in df else 0)
    c5.metric("Phones", int(df.get("best_phone", pd.Series(dtype=str)).fillna("").astype(str).str.len().gt(0).sum()) if "best_phone" in df else 0)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    fname_sector = custom_query if sector == "Custom" and custom_query else sector
    fname_base = f"prospect_discovery_{slugify(fname_sector)}_{slugify(location)}_{stamp}"
    d1, d2 = st.columns(2)
    with d1:
        st.download_button("Download CSV", export_bytes(df, excel=False), file_name=f"{fname_base}.csv", mime="text/csv", use_container_width=True)
    with d2:
        st.download_button("Download Excel", export_bytes(df, excel=True), file_name=f"{fname_base}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

    with st.expander("Diagnostics"):
        st.json(st.session_state.diagnostics or {})
        st.text("\n".join(st.session_state.debug_log[-300:]))
else:
    st.info("Choose a sector and location, then click **Find prospects**.")
