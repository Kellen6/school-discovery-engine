
import streamlit as st
import pandas as pd
import requests
import re
import time
import math
import urllib.parse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

APP_VERSION = "v14.1"

DEBUG_LOG_BUFFER = []

st.set_page_config(page_title="School Discovery Engine", layout="wide")

USER_AGENT = "SchoolDiscoveryEngine/14.0 (educational prospecting; contact: user)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
TIMEOUT = 15

DIRECTORY_DOMAINS = [
    "schoolguide", "schools4sa", "saschools", "privateschool", "educationsa",
    "world-schools", "internationalschools", "international-schools-database",
    "whichschooladvisor", "schoolparrot", "hellopeter", "facebook.com",
    "linkedin.com", "instagram.com", "twitter.com", "x.com", "wikipedia.org",
    "wikidata.org", "tripadvisor", "maps.google", "google.com", "bing.com",
    "duckduckgo.com", "yellosa", "snupit", "brabys", "cybo", "africanadvice"
]

CONTACT_PATH_HINTS = [
    "contact", "contacts", "contact-us", "contact_us", "admissions", "admission",
    "enrol", "enroll", "staff", "team", "leadership", "management", "about",
    "learning-support", "support", "counselling", "counseling", "academics"
]

ROLE_KEYWORDS = {
    "principal": ["principal", "head of school", "headmaster", "headmistress", "executive head"],
    "admissions": ["admissions", "enrolment", "enrollment", "registrar"],
    "counselor": ["counsellor", "counselor", "college counsellor", "university counsellor", "guidance"],
    "learning_support": ["learning support", "inclusive education", "sen", "special needs", "support services"],
    "innovation_ai": ["innovation", "digital learning", "technology", "artificial intelligence", "ai policy"],
    "university_guidance": ["university guidance", "college counseling", "tertiary", "career guidance"]
}

EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
OBFUSCATED_EMAIL_PATTERNS = [
    (re.compile(r"([A-Z0-9._%+\-]+)\s*\[at\]\s*([A-Z0-9.\-]+)\s*\[dot\]\s*([A-Z]{2,})", re.I), r"\1@\2.\3"),
    (re.compile(r"([A-Z0-9._%+\-]+)\s*\(at\)\s*([A-Z0-9.\-]+)\s*\(dot\)\s*([A-Z]{2,})", re.I), r"\1@\2.\3"),
    (re.compile(r"([A-Z0-9._%+\-]+)\s+at\s+([A-Z0-9.\-]+)\s+dot\s+([A-Z]{2,})", re.I), r"\1@\2.\3"),
]

def init_state():
    defaults = {
        "raw_candidates": pd.DataFrame(),
        "enriched_results": pd.DataFrame(),
        "debug_log": [],
        "last_mode": None
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def log(msg):
    # Thread-safe logging: background workers must not touch st.session_state.
    try:
        DEBUG_LOG_BUFFER.append(str(msg))
    except Exception:
        pass

def safe_get(url, timeout=TIMEOUT, allow_redirects=True):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=allow_redirects)
        return r
    except Exception as e:
        return e

def normalize_url(url):
    if not url or pd.isna(url):
        return ""
    url = str(url).strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url

def domain_from_url(url):
    try:
        netloc = urllib.parse.urlparse(normalize_url(url)).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""

def is_directory_url(url):
    d = domain_from_url(url)
    return any(x in d for x in DIRECTORY_DOMAINS)

def clean_name(name):
    if not name:
        return ""
    s = re.sub(r"\s+", " ", str(name)).strip()
    return s

def tokenise_name(name):
    stop = set("the a an of and school primary high college academy international private public christian catholic st saint campus cape town south africa".split())
    toks = re.findall(r"[a-z0-9]+", (name or "").lower())
    return [t for t in toks if t not in stop and len(t) > 2]

def candidate_score_for_url(name, url, html_text=""):
    if not url or is_directory_url(url):
        return -10
    score = 0
    d = domain_from_url(url)
    toks = tokenise_name(name)
    if toks:
        matches = sum(1 for t in toks if t in d)
        score += matches * 3
    if any(x in d for x in ["school", "college", "academy", "university", "edu"]):
        score += 3
    if d.endswith((".ac.za", ".edu", ".edu.za", ".school.za", ".org.za", ".co.za", ".com")):
        score += 1
    text = (html_text or "").lower()
    if text:
        text_matches = sum(1 for t in toks[:5] if t in text)
        score += min(text_matches, 5)
        if any(w in text for w in ["admissions", "principal", "learners", "curriculum", "contact"]):
            score += 2
    return score

def extract_emails(text):
    if not text:
        return []
    emails = set(EMAIL_RE.findall(text))
    for pat, repl in OBFUSCATED_EMAIL_PATTERNS:
        for m in pat.finditer(text):
            emails.add(m.expand(repl))
    cleaned = []
    for e in emails:
        e = e.strip(".,;:()[]{}<>").lower()
        if not any(e.endswith(x) for x in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]):
            cleaned.append(e)
    return sorted(set(cleaned))

def soup_text(soup):
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()

def extract_links(base_url, html_text):
    links = []
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(" ", strip=True)
            abs_url = urllib.parse.urljoin(base_url, href)
            links.append((abs_url, text))
    except Exception:
        pass
    return links

def discover_from_nominatim(query, limit=50):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": limit,
        "addressdetails": 1,
        "extratags": 1,
        "namedetails": 1,
        "dedupe": 1,
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        log(f"Nominatim '{query}': HTTP {r.status_code}")
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception as e:
        log(f"Nominatim error '{query}': {type(e).__name__}: {e}")
        return []
    rows = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        extratags = item.get("extratags") or {}
        namedetails = item.get("namedetails") or {}
        name = (
            namedetails.get("name") or
            item.get("name") or
            (item.get("display_name", "").split(",")[0] if item.get("display_name") else "")
        )
        website = (
            extratags.get("website") or extratags.get("contact:website") or
            extratags.get("url") or ""
        )
        email = extratags.get("email") or extratags.get("contact:email") or ""
        phone = extratags.get("phone") or extratags.get("contact:phone") or ""
        cls = item.get("class") or ""
        typ = item.get("type") or ""
        rows.append({
            "school_name": clean_name(name),
            "website": normalize_url(website) if website else "",
            "domain": domain_from_url(website) if website else "",
            "source": "nominatim",
            "source_detail": query,
            "status": "candidate",
            "osm_email": email,
            "phone": phone,
            "lat": item.get("lat", ""),
            "lon": item.get("lon", ""),
            "place_class": cls,
            "place_type": typ,
        })
    return rows

def geocode_location(location):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": location, "format": "jsonv2", "limit": 1, "addressdetails": 1}
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    log(f"Geocode HTTP {r.status_code}: {r.url}")
    if r.status_code != 200 or not r.json():
        return None
    item = r.json()[0]
    return float(item["lat"]), float(item["lon"]), item.get("display_name", location)

def overpass_query(lat, lon, radius_km, max_results):
    # Query multiple education-related tags. Uses [out:json] and data parameter.
    radius_m = int(radius_km * 1000)
    q = f"""
    [out:json][timeout:25];
    (
      node(around:{radius_m},{lat},{lon})["amenity"~"school|college|university|kindergarten|language_school"];
      way(around:{radius_m},{lat},{lon})["amenity"~"school|college|university|kindergarten|language_school"];
      relation(around:{radius_m},{lat},{lon})["amenity"~"school|college|university|kindergarten|language_school"];
      node(around:{radius_m},{lat},{lon})["building"="school"];
      way(around:{radius_m},{lat},{lon})["building"="school"];
      relation(around:{radius_m},{lat},{lon})["building"="school"];
      node(around:{radius_m},{lat},{lon})["office"="educational_institution"];
      way(around:{radius_m},{lat},{lon})["office"="educational_institution"];
      relation(around:{radius_m},{lat},{lon})["office"="educational_institution"];
    );
    out center tags {max_results};
    """
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.osm.ch/api/interpreter",
    ]
    rows = []
    for ep in endpoints:
        try:
            r = requests.post(ep, data={"data": q}, headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
            log(f"Overpass POST {ep}: HTTP {r.status_code}")
            if r.status_code != 200:
                log(f"Overpass body preview: {r.text[:160]}")
                continue
            data = r.json()
            elems = data.get("elements", [])
            log(f"Overpass {ep}: {len(elems)} elements")
            for el in elems[:max_results]:
                tags = el.get("tags") or {}
                name = tags.get("name") or tags.get("official_name") or ""
                if not name:
                    continue
                website = tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
                email = tags.get("email") or tags.get("contact:email") or ""
                phone = tags.get("phone") or tags.get("contact:phone") or ""
                center = el.get("center") or {}
                rows.append({
                    "school_name": clean_name(name),
                    "website": normalize_url(website) if website else "",
                    "domain": domain_from_url(website) if website else "",
                    "source": "overpass",
                    "source_detail": ep,
                    "status": "candidate",
                    "osm_email": email,
                    "phone": phone,
                    "lat": el.get("lat", center.get("lat", "")),
                    "lon": el.get("lon", center.get("lon", "")),
                    "place_class": tags.get("amenity", tags.get("building", "")),
                    "place_type": tags.get("operator:type", ""),
                })
            if rows:
                break
        except Exception as e:
            log(f"Overpass {ep}: {type(e).__name__}: {e}")
    return rows

def discover_by_location(location, radius_km, max_results):
    rows = []
    geo = geocode_location(location)
    if geo:
        lat, lon, display = geo
        log(f"Geocoded to: {display} ({lat:.5f},{lon:.5f})")
        rows.extend(overpass_query(lat, lon, radius_km, max_results))
    if not rows:
        log("Overpass returned no candidates or timed out. Trying Nominatim fallback...")
    queries = [
        f"school in {location}",
        f"private school in {location}",
        f"international school in {location}",
        f"college in {location}",
        f"university in {location}",
        f"academy in {location}",
    ]
    for q in queries:
        rows.extend(discover_from_nominatim(q, limit=max_results))
    return dedupe_candidates(rows)[:max_results]

def discover_by_school_names(names, location_hint="", max_results=20, resolve_websites=True):
    rows = []
    for name in names:
        q = f"{name} {location_hint}".strip()
        nom_rows = discover_from_nominatim(q, limit=5)
        if nom_rows:
            rows.extend(nom_rows)
        else:
            rows.append({
                "school_name": clean_name(name),
                "website": "",
                "domain": "",
                "source": "manual_name",
                "source_detail": location_hint,
                "status": "candidate",
                "osm_email": "",
                "phone": "",
                "lat": "",
                "lon": "",
                "place_class": "",
                "place_type": "",
            })
    rows = dedupe_candidates(rows)[:max_results]
    if resolve_websites:
        rows = resolve_websites_for_rows(rows)
    return rows

def discover_by_urls(urls):
    rows = []
    for url in urls:
        u = normalize_url(url)
        if not u:
            continue
        rows.append({
            "school_name": "",
            "website": u,
            "domain": domain_from_url(u),
            "source": "manual_url",
            "source_detail": u,
            "status": "candidate",
            "osm_email": "",
            "phone": "",
            "lat": "",
            "lon": "",
            "place_class": "",
            "place_type": "",
        })
    return dedupe_candidates(rows)

def discover_from_source_pages(urls, max_links=100):
    rows = []
    for url in urls:
        u = normalize_url(url)
        r = safe_get(u, timeout=20)
        if isinstance(r, Exception):
            log(f"Source page error {u}: {r}")
            continue
        log(f"Source page {u}: HTTP {r.status_code}")
        if r.status_code >= 400:
            continue
        for link, text in extract_links(u, r.text):
            if is_directory_url(link):
                continue
            d = domain_from_url(link)
            if not d:
                continue
            if any(x in d for x in ["school", "college", "academy", "university", "edu"]) or any(x in (text or "").lower() for x in ["school", "college", "academy", "university"]):
                rows.append({
                    "school_name": clean_name(text) or d,
                    "website": normalize_url(link),
                    "domain": d,
                    "source": "source_page",
                    "source_detail": u,
                    "status": "candidate",
                    "osm_email": "",
                    "phone": "",
                    "lat": "",
                    "lon": "",
                    "place_class": "",
                    "place_type": "",
                })
            if len(rows) >= max_links:
                break
    return dedupe_candidates(rows)

def dedupe_candidates(rows):
    seen = set()
    out = []
    for r in rows:
        name = clean_name(r.get("school_name", ""))
        website = normalize_url(r.get("website", ""))
        dom = domain_from_url(website)
        key = dom or re.sub(r"[^a-z0-9]+", "", name.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        r["school_name"] = name
        r["website"] = website
        r["domain"] = dom
        out.append(r)
    return out

def ddg_search_official_site(name, location_hint=""):
    # Best-effort. This may work locally and sometimes on Streamlit Cloud, but is not guaranteed.
    query = f"{name} {location_hint} official school website".strip()
    url = "https://html.duckduckgo.com/html/"
    try:
        r = requests.post(url, data={"q": query}, headers=HEADERS, timeout=12)
        log(f"DDG resolve '{name}': HTTP {r.status_code}")
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        candidates = []
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            text = a.get_text(" ", strip=True)
            # DDG redirect URLs may contain uddg.
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            if "uddg" in qs:
                href = qs["uddg"][0]
            if href and not is_directory_url(href):
                candidates.append((href, text))
        if not candidates:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("http") and not is_directory_url(href):
                    candidates.append((href, a.get_text(" ", strip=True)))
        scored = sorted(candidates, key=lambda x: candidate_score_for_url(name, x[0], x[1]), reverse=True)
        if scored and candidate_score_for_url(name, scored[0][0], scored[0][1]) >= 2:
            return normalize_url(scored[0][0])
    except Exception as e:
        log(f"DDG resolve error '{name}': {type(e).__name__}: {e}")
    return ""

def guess_domain_candidates(name):
    toks = tokenise_name(name)
    if not toks:
        return []
    compact = "".join(toks)
    dashed = "-".join(toks)
    first_two = "".join(toks[:2])
    bases = list(dict.fromkeys([compact, dashed, first_two]))
    suffixes = [".co.za", ".org.za", ".school.za", ".ac.za", ".com", ".org"]
    urls = []
    for b in bases:
        for s in suffixes:
            urls.append(f"https://www.{b}{s}")
            urls.append(f"https://{b}{s}")
    return urls[:30]

def validate_possible_site(name, url):
    r = safe_get(url, timeout=8)
    if isinstance(r, Exception) or getattr(r, "status_code", 999) >= 400:
        return 0, ""
    text = ""
    try:
        soup = BeautifulSoup(r.text[:200000], "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        text = title + " " + soup_text(soup)[:3000]
    except Exception:
        text = r.text[:3000]
    score = candidate_score_for_url(name, url, text)
    return score, url

def resolve_website_for_name(name, location_hint=""):
    # 1. Search engine fallback
    url = ddg_search_official_site(name, location_hint)
    if url:
        return url, "ddg"
    # 2. Domain guessing fallback
    best = ("", 0)
    for u in guess_domain_candidates(name):
        score, valid_url = validate_possible_site(name, u)
        if score > best[1]:
            best = (valid_url, score)
        if score >= 8:
            break
    if best[0] and best[1] >= 5:
        return best[0], "domain_guess"
    return "", ""

def resolve_websites_for_rows(rows, location_hint="", max_workers=6):
    unresolved = [i for i, r in enumerate(rows) if not r.get("website")]
    if not unresolved:
        return rows
    def worker(i):
        r = rows[i]
        url, method = resolve_website_for_name(r.get("school_name", ""), location_hint)
        return i, url, method
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(worker, i) for i in unresolved]
        for fut in as_completed(futures):
            try:
                i, url, method = fut.result()
            except Exception as e:
                log(f"Website resolver worker failed: {type(e).__name__}: {e}")
                continue
            if url:
                rows[i]["website"] = url
                rows[i]["domain"] = domain_from_url(url)
                rows[i]["website_resolution_method"] = method
                rows[i]["status"] = "website_resolved"
            else:
                rows[i]["website_resolution_method"] = "not_found"
    return rows


CONTACT_SEARCH_QUERIES = [
    "{name} contact email {location}",
    "{name} admissions email {location}",
    "{name} principal email {location}",
    "{name} staff email {location}",
    "{name} learning support email {location}",
]


def parse_ddg_results(html):
    """Return [(url, title_or_snippet)] from DuckDuckGo HTML results."""
    soup = BeautifulSoup(html or "", "html.parser")
    results = []
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        text = a.get_text(" ", strip=True)
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs:
            href = qs["uddg"][0]
        if href:
            results.append((href, text))
    # Fallback selector for DDG variants.
    if not results:
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = a.get_text(" ", strip=True)
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            if "uddg" in qs:
                href = qs["uddg"][0]
            if href.startswith("http") and "duckduckgo.com" not in domain_from_url(href):
                results.append((href, text))
    # Deduplicate by normalized URL/domain path.
    seen = set()
    out = []
    for href, text in results:
        href = normalize_url(href)
        key = href.split("#")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append((href, text))
    return out


def ddg_search_results(query, max_results=8):
    url = "https://html.duckduckgo.com/html/"
    try:
        r = requests.post(url, data={"q": query}, headers=HEADERS, timeout=14)
        log(f"DDG contact search '{query[:80]}': HTTP {r.status_code}")
        if r.status_code != 200:
            return []
        return parse_ddg_results(r.text)[:max_results]
    except Exception as e:
        log(f"DDG contact search error '{query[:80]}': {type(e).__name__}: {e}")
        return []


def search_contact_details(name, website="", location_hint="", max_pages=8):
    """Best-effort web search fallback for contacts when website scrape is incomplete.

    Returns a dict containing emails, generic_emails, source_urls, text, and role signals.
    Search-derived contacts are not verified unless found on an opened source page.
    """
    name = clean_name(name)
    domain = domain_from_url(website)
    queries = []
    for tmpl in CONTACT_SEARCH_QUERIES:
        queries.append(tmpl.format(name=name, location=location_hint or "" ).strip())
    if domain:
        queries.extend([
            f"site:{domain} contact email",
            f"site:{domain} admissions email",
            f"site:{domain} principal staff",
        ])

    result_urls = []
    for q in queries:
        for url, title in ddg_search_results(q, max_results=5):
            # Avoid obvious social networks/search engines; keep directories because they often expose contact details.
            if any(bad in domain_from_url(url) for bad in ["google.com", "bing.com", "duckduckgo.com", "linkedin.com"]):
                continue
            result_urls.append((url, title))
            if len(result_urls) >= max_pages * 2:
                break
        if len(result_urls) >= max_pages * 2:
            break

    # Prioritize official domain if known, then education/directories, then everything else.
    def source_score(item):
        url, title = item
        d = domain_from_url(url)
        score = 0
        if domain and d == domain:
            score += 50
        if not is_directory_url(url):
            score += 10
        if any(k in (url + " " + title).lower() for k in ["contact", "admission", "staff", "principal", "school"]):
            score += 10
        score += candidate_score_for_url(name, url, title)
        return score

    dedup = []
    seen = set()
    for item in sorted(result_urls, key=source_score, reverse=True):
        key = item[0].split("#")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
        if len(dedup) >= max_pages:
            break

    all_text = ""
    emails = set()
    opened_sources = []
    snippet_sources = []
    snippet_text = ""

    for url, title in dedup:
        snippet_text += "\n" + title
        emails.update(extract_emails(title))
        snippet_sources.append(url)
        r = safe_get(url, timeout=12)
        if isinstance(r, Exception) or getattr(r, "status_code", 999) >= 400:
            continue
        ctype = r.headers.get("content-type", "")
        if "pdf" in ctype.lower():
            continue
        if "text/html" not in ctype and "html" not in ctype and not r.text.strip().startswith("<"):
            continue
        try:
            soup = BeautifulSoup(r.text[:250000], "html.parser")
            text = soup_text(soup)
        except Exception:
            text = r.text[:12000]
        all_text += "\n" + text
        emails.update(extract_emails(r.text + "\n" + text))
        opened_sources.append(url)

    roles = analyze_roles(all_text + "\n" + snippet_text)
    email_list = sorted(emails)
    generic = [e for e in email_list if e.split("@")[0] in ["info", "office", "admin", "admissions", "enrolments", "enrollment", "contact", "reception", "registrar"]]
    confidence = 0
    if email_list:
        confidence += 20
    if opened_sources:
        confidence += 15
    if domain and any(domain_from_url(u) == domain for u in opened_sources):
        confidence += 25
    if generic:
        confidence += 10
    if roles.get("admissions"):
        confidence += 8
    if roles.get("principal"):
        confidence += 8
    if roles.get("learning_support"):
        confidence += 8

    return {
        "search_emails": email_list,
        "search_generic_emails": generic,
        "search_source_urls": opened_sources or snippet_sources,
        "search_text": all_text + "\n" + snippet_text,
        "search_roles": roles,
        "search_confidence": min(confidence, 85),
    }

def pages_to_scrape(home_url, max_pages=8):
    urls = [normalize_url(home_url)]
    r = safe_get(home_url, timeout=TIMEOUT)
    if isinstance(r, Exception) or getattr(r, "status_code", 999) >= 400:
        return urls
    links = extract_links(home_url, r.text)
    scored = []
    home_domain = domain_from_url(home_url)
    for link, text in links:
        if domain_from_url(link) != home_domain:
            continue
        path = urllib.parse.urlparse(link).path.lower()
        blob = f"{path} {text}".lower()
        if any(h in blob for h in CONTACT_PATH_HINTS):
            scored.append((link, sum(1 for h in CONTACT_PATH_HINTS if h in blob)))
    scored = sorted(scored, key=lambda x: x[1], reverse=True)
    for link, _ in scored:
        if link not in urls:
            urls.append(link)
        if len(urls) >= max_pages:
            break
    # Add guessed pages
    root = f"{urllib.parse.urlparse(home_url).scheme}://{urllib.parse.urlparse(home_url).netloc}"
    for p in ["contact", "contact-us", "admissions", "staff", "team", "leadership", "about"]:
        u = f"{root}/{p}"
        if u not in urls:
            urls.append(u)
        if len(urls) >= max_pages:
            break
    return urls[:max_pages]

def infer_email_pattern(emails):
    if not emails:
        return ""
    locals_ = [e.split("@")[0] for e in emails if "@" in e]
    patterns = []
    for loc in locals_:
        if "." in loc and all(part.isalpha() for part in loc.split(".")[:2]):
            patterns.append("firstname.lastname@domain")
        elif "_" in loc:
            patterns.append("firstname_lastname@domain")
        elif re.match(r"^[a-z][a-z]+$", loc):
            patterns.append("firstname@domain / generic")
        elif re.match(r"^[a-z][a-z]+[0-9]*$", loc):
            patterns.append("name@domain")
    if not patterns:
        return ""
    return max(set(patterns), key=patterns.count)

def analyze_roles(text):
    found = {}
    low = (text or "").lower()
    for role, kws in ROLE_KEYWORDS.items():
        found[role] = any(k in low for k in kws)
    return found

def fit_score(roles, text):
    score = 0
    if roles.get("learning_support"): score += 3
    if roles.get("university_guidance") or roles.get("counselor"): score += 2
    if roles.get("innovation_ai"): score += 2
    low = (text or "").lower()
    if any(k in low for k in ["international", "ib ", "cambridge", "a-level", "igcse"]): score += 2
    if any(k in low for k in ["parent", "workshop", "webinar"]): score += 1
    return score

def scrape_school(row, location_hint="", use_contact_search=True):
    website = normalize_url(row.get("website", ""))
    result = dict(row)
    result.update({
        "scrape_status": "not_attempted",
        "scraped_pages": "",
        "visible_emails": "",
        "generic_emails": "",
        "search_emails": "",
        "search_generic_emails": "",
        "search_contact_sources": "",
        "contact_source": "none",
        "email_pattern": "",
        "contact_confidence": 0,
        "fit_score": 0,
        "has_principal_signal": False,
        "has_admissions_signal": False,
        "has_counselor_signal": False,
        "has_learning_support_signal": False,
        "has_innovation_ai_signal": False,
        "has_university_guidance_signal": False,
        "source_pages_scraped": "",
        "notes": "",
    })

    all_text = ""
    all_emails = set()
    scraped = []
    roles = {}

    if website and not is_directory_url(website):
        for u in pages_to_scrape(website):
            r = safe_get(u, timeout=TIMEOUT)
            if isinstance(r, Exception):
                continue
            if r.status_code >= 400:
                continue
            ctype = r.headers.get("content-type", "")
            if "text/html" not in ctype and "html" not in ctype and not r.text.strip().startswith("<"):
                continue
            try:
                soup = BeautifulSoup(r.text, "html.parser")
                text = soup_text(soup)
            except Exception:
                text = r.text
            all_text += "\n" + text
            all_emails.update(extract_emails(r.text + "\n" + text))
            scraped.append(u)
    elif website and is_directory_url(website):
        result["scrape_status"] = "directory_or_social_skipped"
    else:
        result["scrape_status"] = "no_website"

    roles = analyze_roles(all_text)
    emails = sorted(all_emails)
    generic = [e for e in emails if e.split("@")[0] in ["info", "office", "admin", "admissions", "enrolments", "enrollment", "contact", "reception", "registrar"]]
    confidence = 0
    if emails: confidence += 30
    if generic: confidence += 20
    if roles.get("admissions"): confidence += 10
    if roles.get("principal"): confidence += 10
    if roles.get("learning_support"): confidence += 10
    if scraped: confidence += 10

    contact_source = "website" if emails else "none"
    search = {"search_emails": [], "search_generic_emails": [], "search_source_urls": [], "search_text": "", "search_roles": {}, "search_confidence": 0}

    # Search fallback: run when website scraping failed, no visible emails, or weak confidence.
    if use_contact_search and (not emails or confidence < 35):
        search = search_contact_details(
            result.get("school_name", ""),
            website=website,
            location_hint=location_hint,
            max_pages=8,
        )
        if search["search_emails"]:
            contact_source = "search_result" if not emails else "website + search_result"
        # Merge role signals and fit text from search pages/snippets.
        search_roles = search.get("search_roles", {}) or {}
        for k, v in search_roles.items():
            roles[k] = bool(roles.get(k) or v)
        all_text += "\n" + (search.get("search_text") or "")
        # Merge emails but keep separate columns for transparency.
        for e in search.get("search_emails", []):
            all_emails.add(e)
        emails = sorted(all_emails)
        generic = sorted(set(generic + (search.get("search_generic_emails") or [])))
        confidence = max(confidence, search.get("search_confidence", 0))
        if search.get("search_emails") and scraped:
            confidence = min(confidence + 8, 95)

    if not contact_source or contact_source == "none":
        if website and scraped:
            contact_source = "website_no_email"
        elif website:
            contact_source = "website_unreachable_or_no_contact"
        else:
            contact_source = "no_website_search_attempted" if use_contact_search else "no_website"

    result.update({
        "scrape_status": "scraped" if scraped else (result.get("scrape_status") if result.get("scrape_status") != "not_attempted" else "scrape_failed"),
        "scraped_pages": len(scraped),
        "visible_emails": "; ".join(emails),
        "generic_emails": "; ".join(generic),
        "search_emails": "; ".join(search.get("search_emails", [])),
        "search_generic_emails": "; ".join(search.get("search_generic_emails", [])),
        "search_contact_sources": "; ".join((search.get("search_source_urls") or [])[:10]),
        "contact_source": contact_source,
        "email_pattern": infer_email_pattern(emails),
        "contact_confidence": min(confidence, 100),
        "fit_score": fit_score(roles, all_text),
        "has_principal_signal": roles.get("principal", False),
        "has_admissions_signal": roles.get("admissions", False),
        "has_counselor_signal": roles.get("counselor", False),
        "has_learning_support_signal": roles.get("learning_support", False),
        "has_innovation_ai_signal": roles.get("innovation_ai", False),
        "has_university_guidance_signal": roles.get("university_guidance", False),
        "source_pages_scraped": "; ".join(scraped[:10]),
        "notes": "Search-derived contacts are unverified unless the source page is an official school domain. Review before sending outreach." if search.get("search_emails") else "",
    })
    return result

def enrich_rows(rows, resolve_missing=True, location_hint="", max_workers=6, use_contact_search=True):
    if resolve_missing:
        rows = resolve_websites_for_rows(rows, location_hint=location_hint, max_workers=max_workers)
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(scrape_school, r, location_hint, use_contact_search) for r in rows]
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                log(f"Scrape worker failed: {type(e).__name__}: {e}")
    # stable-ish sort by fit/contact/name
    results = sorted(results, key=lambda r: (r.get("fit_score",0), r.get("contact_confidence",0), r.get("school_name","")), reverse=True)
    return results

def to_excel_bytes(raw_df, enriched_df):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        raw_df.to_excel(writer, index=False, sheet_name="Raw Candidates")
        enriched_df.to_excel(writer, index=False, sheet_name="Enriched Results")
    return output.getvalue()

def show_results():
    raw_df = st.session_state.raw_candidates
    enriched_df = st.session_state.enriched_results
    if raw_df is not None and not raw_df.empty:
        st.subheader("Raw candidates")
        st.caption(f"{len(raw_df)} candidates retained. Rows without websites are kept for manual review or website resolution.")
        st.dataframe(raw_df, use_container_width=True, height=260)
    if enriched_df is not None and not enriched_df.empty:
        st.subheader("Enriched / scraped results")
        st.caption(f"{len(enriched_df)} rows exported. Downloading will not clear results.")
        st.dataframe(enriched_df, use_container_width=True, height=360)
        c1, c2, c3 = st.columns(3)
        with c1:
            st.download_button(
                "Download enriched CSV",
                data=enriched_df.to_csv(index=False).encode("utf-8"),
                file_name="school_discovery_enriched_results.csv",
                mime="text/csv",
                key="download_enriched_csv",
            )
        with c2:
            st.download_button(
                "Download raw candidates CSV",
                data=raw_df.to_csv(index=False).encode("utf-8"),
                file_name="candidate_schools_raw.csv",
                mime="text/csv",
                key="download_raw_csv",
            )
        with c3:
            st.download_button(
                "Download Excel workbook",
                data=to_excel_bytes(raw_df, enriched_df),
                file_name="school_discovery_results.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_xlsx",
            )

def clear_results():
    st.session_state.raw_candidates = pd.DataFrame()
    st.session_state.enriched_results = pd.DataFrame()
    st.session_state.debug_log = []
    DEBUG_LOG_BUFFER.clear()

init_state()

st.title("School Discovery Engine")
st.caption(f"Free workflow build {APP_VERSION}: discover schools, resolve websites, scrape contacts, and export for outreach.")

with st.sidebar:
    st.header("Settings")
    resolve_missing = st.checkbox("Try to resolve missing websites", value=True, help="Uses best-effort free search and domain guessing. Not guaranteed.")
    scrape_after = st.checkbox("Scrape contacts after discovery", value=True)
    use_contact_search = st.checkbox("Use web search fallback for missing contacts", value=True, help="If website scraping finds no useful emails/contacts, search the web for contact/admissions/principal details and mark them as search-derived.")
    max_workers = st.slider("Scraping speed", 1, 10, 5)
    if st.button("Clear results"):
        clear_results()
        st.rerun()

mode = st.radio(
    "Choose one discovery mode",
    ["1. Map / geolocation", "2. School name", "3. School URL", "4. Source/list page"],
    horizontal=True,
)

with st.form("discovery_form"):
    if mode.startswith("1"):
        location = st.text_input("Location", value="Cape Town, Western Cape, South Africa")
        radius = st.slider("Radius (km)", 1, 100, 10)
        max_results = st.slider("Max candidates", 10, 300, 100)
        submitted = st.form_submit_button("Find and scrape schools")
    elif mode.startswith("2"):
        location = st.text_input("Optional location hint", value="Cape Town, South Africa")
        names_text = st.text_area("School names, one per line", height=180, placeholder="Lycée Français du Cap\nCape Town High School\n...")
        max_results = st.slider("Max candidates", 10, 300, 100)
        submitted = st.form_submit_button("Resolve and scrape names")
    elif mode.startswith("3"):
        urls_text = st.text_area("School URLs, one per line", height=180, placeholder="https://example-school.org\nhttps://another-school.co.za")
        submitted = st.form_submit_button("Scrape URLs")
    else:
        source_urls_text = st.text_area("Source/list page URLs, one per line", height=180, placeholder="https://example.com/best-schools-cape-town")
        max_results = st.slider("Max extracted school links", 10, 300, 100)
        submitted = st.form_submit_button("Extract and scrape school links")

if submitted:
    st.session_state.debug_log = []
    DEBUG_LOG_BUFFER.clear()
    with st.spinner("Running discovery..."):
        if mode.startswith("1"):
            rows = discover_by_location(location, radius, max_results)
            if resolve_missing:
                rows = resolve_websites_for_rows(rows, location_hint=location, max_workers=max_workers)
        elif mode.startswith("2"):
            names = [clean_name(x) for x in names_text.splitlines() if clean_name(x)]
            rows = discover_by_school_names(names, location_hint=location, max_results=max_results, resolve_websites=resolve_missing)
        elif mode.startswith("3"):
            urls = [x.strip() for x in urls_text.splitlines() if x.strip()]
            rows = discover_by_urls(urls)
        else:
            urls = [x.strip() for x in source_urls_text.splitlines() if x.strip()]
            rows = discover_from_source_pages(urls, max_links=max_results)
            if resolve_missing:
                rows = resolve_websites_for_rows(rows, max_workers=max_workers)
        raw_df = pd.DataFrame(rows)
        st.session_state.raw_candidates = raw_df
    if not rows:
        st.warning("No candidates found. Try School name or School URL mode, or paste a source/list page.")
    elif scrape_after:
        with st.spinner("Scraping websites and contacts..."):
            enriched = enrich_rows(rows, resolve_missing=False, location_hint=(locals().get("location") or ""), max_workers=max_workers, use_contact_search=use_contact_search)
            st.session_state.enriched_results = pd.DataFrame(enriched)
    else:
        st.session_state.enriched_results = raw_df.copy()

show_results()

with st.expander("Debug log"):
    combined_debug = list(st.session_state.get("debug_log", [])) + list(DEBUG_LOG_BUFFER)
    if combined_debug:
        st.code("\n".join(combined_debug))
    else:
        st.write("No debug messages yet.")
