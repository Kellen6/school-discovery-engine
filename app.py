import csv
import hashlib
import io
import re
import time
from datetime import datetime
from urllib.parse import quote_plus, urljoin, urlparse

import pandas as pd
import phonenumbers
import requests
import streamlit as st
from bs4 import BeautifulSoup

APP_VERSION = "v19"
UA = "SchoolDiscoveryEngine/19 contact: personal-research"
TIMEOUT = 12

SECTOR_PROFILES = {
    "Schools": {
        "osm_terms": ["school", "college", "university", "academy"],
        "contact_paths": ["contact", "contact-us", "admissions", "enrolment", "enrollment", "staff", "leadership", "about", "office", "reception", "campus"],
        "keywords": ["school", "college", "academy", "university", "admissions", "principal", "learning support"],
    },
    "Universities / Higher Ed": {
        "osm_terms": ["university", "college", "campus"],
        "contact_paths": ["contact", "admissions", "faculty", "staff", "about", "campus"],
        "keywords": ["university", "college", "campus", "admissions", "faculty"],
    },
    "Clinics / Health": {
        "osm_terms": ["clinic", "hospital", "health centre", "medical centre"],
        "contact_paths": ["contact", "appointments", "services", "about", "locations"],
        "keywords": ["clinic", "hospital", "health", "medical", "appointments"],
    },
    "NGOs / Nonprofits": {
        "osm_terms": ["ngo", "nonprofit", "foundation", "charity"],
        "contact_paths": ["contact", "about", "team", "staff", "programmes", "programs"],
        "keywords": ["ngo", "nonprofit", "foundation", "charity", "programmes"],
    },
}


def init_state():
    defaults = {
        "debug_log": [],
        "map_cache_key": None,
        "map_candidates": None,
        "raw_df": None,
        "enriched_df": None,
        "last_mode": None,
        "last_query_label": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def log(msg):
    try:
        st.session_state.debug_log.append(str(msg))
    except Exception:
        pass


def safe_get(url, timeout=TIMEOUT):
    try:
        return requests.get(url, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=True)
    except Exception as e:
        log(f"GET error {url}: {type(e).__name__}: {e}")
        return None


def slug(s, max_len=45):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(s).lower()).strip("_")
    return s[:max_len] or "query"


def cache_key(*parts):
    raw = "||".join(map(str, parts))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def country_code_from_location(location):
    l = (location or "").lower()
    mapping = {
        "south africa": "ZA", "nigeria": "NG", "kenya": "KE", "uganda": "UG", "rwanda": "RW",
        "united kingdom": "GB", "uk": "GB", "canada": "CA", "united states": "US", "usa": "US",
        "ghana": "GH", "tanzania": "TZ", "zambia": "ZM", "mozambique": "MZ", "france": "FR",
    }
    for k, v in mapping.items():
        if k in l:
            return v
    return None


def extract_emails(text):
    if not text:
        return []
    text = text.replace(" [at] ", "@").replace("(at)", "@").replace(" at ", "@").replace(" [dot] ", ".").replace(" dot ", ".")
    emails = set(re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text))
    bad = ("example.com", "domain.com", "email.com", "yourname")
    return sorted(e for e in emails if not any(b in e.lower() for b in bad))


def extract_phones(text, region=None):
    if not text:
        return []
    found = set()
    for match in phonenumbers.PhoneNumberMatcher(text, region or None):
        num = match.number
        if phonenumbers.is_valid_number(num) or phonenumbers.is_possible_number(num):
            found.add(phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
    return sorted(found)


def guess_homepage_from_name(name, location_hint=""):
    # conservative domain guess only; real resolution uses search pages where available
    cleaned = re.sub(r"\b(school|college|academy|university|primary|high|the)\b", "", name.lower())
    tokens = re.findall(r"[a-z0-9]+", cleaned)
    if not tokens:
        return ""
    country = country_code_from_location(location_hint)
    tld = ".co.za" if country == "ZA" else ".org"
    return "https://" + "".join(tokens[:3]) + tld


def ddg_html_search(query, max_results=5):
    url = "https://duckduckgo.com/html/?q=" + quote_plus(query)
    r = safe_get(url, timeout=10)
    if not r or r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    results = []
    for a in soup.select("a.result__a")[:max_results]:
        href = a.get("href") or ""
        title = a.get_text(" ", strip=True)
        results.append({"title": title, "url": href})
    return results


def resolve_website_for_name(name, location_hint=""):
    if not name:
        return "", "none"
    queries = [f'"{name}" "{location_hint}" official website', f'"{name}" contact']
    bad_domains = ["facebook.com", "instagram.com", "linkedin.com", "wikipedia.org", "schoolguide", "directory", "best", "top"]
    for q in queries:
        for res in ddg_html_search(q, 5):
            u = res.get("url", "")
            if not u.startswith("http"):
                continue
            dom = urlparse(u).netloc.lower()
            if any(b in dom or b in u.lower() for b in bad_domains):
                continue
            return u.split("?")[0], "search_resolved"
    return "", "not_found"


def geocode(location):
    url = "https://nominatim.openstreetmap.org/search?q=" + quote_plus(location) + "&format=jsonv2&limit=1&addressdetails=1"
    r = safe_get(url, timeout=15)
    log(f"Geocode HTTP {getattr(r, 'status_code', None)}: {url}")
    if not r or r.status_code != 200:
        return None
    data = r.json()
    if not data:
        return None
    item = data[0]
    return float(item["lat"]), float(item["lon"]), item.get("display_name", location)


def overpass_candidates(lat, lon, radius_km, sector_profile, max_candidates):
    terms = SECTOR_PROFILES[sector_profile]["osm_terms"]
    radius_m = int(radius_km * 1000)
    filters = []
    if sector_profile == "Schools":
        filters = ['node["amenity"~"school|college|university|kindergarten"]', 'way["amenity"~"school|college|university|kindergarten"]', 'node["building"="school"]', 'way["building"="school"]']
    else:
        joined = "|".join(re.escape(t) for t in terms)
        filters = [f'node["name"~"{joined}",i]', f'way["name"~"{joined}",i]']
    body = "[out:json][timeout:25];(" + "".join(f'{f}(around:{radius_m},{lat},{lon});' for f in filters) + ");out center tags;"
    endpoints = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.osm.ch/api/interpreter"]
    rows = []
    for ep in endpoints:
        try:
            r = requests.post(ep, data={"data": body}, headers={"User-Agent": UA}, timeout=30)
            log(f"Overpass POST {ep}: HTTP {r.status_code}")
            if r.status_code != 200:
                continue
            elements = r.json().get("elements", [])
            log(f"Overpass {ep}: {len(elements)} elements")
            for e in elements:
                tags = e.get("tags") or {}
                name = tags.get("name") or tags.get("operator") or ""
                if not name:
                    continue
                center = e.get("center") or {}
                rows.append({
                    "school_name": name,
                    "sector": sector_profile,
                    "source": "overpass",
                    "website": tags.get("website") or tags.get("contact:website") or "",
                    "osm_phone": tags.get("phone") or tags.get("contact:phone") or "",
                    "address": ", ".join([tags.get("addr:street", ""), tags.get("addr:city", "")]).strip(", "),
                    "latitude": e.get("lat") or center.get("lat") or "",
                    "longitude": e.get("lon") or center.get("lon") or "",
                })
            break
        except Exception as e:
            log(f"Overpass {ep}: {type(e).__name__}: {e}")
    return dedupe_rows(rows)[:max_candidates]


def nominatim_candidates(location, sector_profile, max_candidates):
    rows = []
    terms = SECTOR_PROFILES[sector_profile]["osm_terms"]
    for term in terms:
        url = "https://nominatim.openstreetmap.org/search?q=" + quote_plus(f"{term} in {location}") + "&format=jsonv2&limit=50&addressdetails=1&extratags=1"
        r = safe_get(url, timeout=15)
        log(f"Nominatim '{term} in {location}': HTTP {getattr(r,'status_code',None)}")
        if not r or r.status_code != 200:
            continue
        for item in r.json() or []:
            extratags = item.get("extratags") or {}
            name = item.get("name") or item.get("display_name", "").split(",")[0]
            if not name:
                continue
            rows.append({
                "school_name": name,
                "sector": sector_profile,
                "source": "nominatim",
                "website": extratags.get("website", ""),
                "osm_phone": extratags.get("phone", ""),
                "address": item.get("display_name", ""),
                "latitude": item.get("lat", ""),
                "longitude": item.get("lon", ""),
            })
    return dedupe_rows(rows)[:max_candidates]


def dedupe_rows(rows):
    seen = set(); out = []
    for r in rows:
        key = re.sub(r"\W+", "", (r.get("school_name") or "").lower())
        if not key or key in seen:
            continue
        seen.add(key); out.append(r)
    return out


def discover_map(location, radius_km, max_candidates, sector_profile):
    geo = geocode(location)
    rows = []
    if geo:
        lat, lon, display = geo
        log(f"Geocoded to: {display} ({lat},{lon})")
        rows = overpass_candidates(lat, lon, radius_km, sector_profile, max_candidates)
    if len(rows) < max_candidates:
        existing = {re.sub(r"\W+", "", r.get("school_name", "").lower()) for r in rows}
        more = nominatim_candidates(location, sector_profile, max_candidates)
        for r in more:
            k = re.sub(r"\W+", "", r.get("school_name", "").lower())
            if k and k not in existing:
                rows.append(r); existing.add(k)
            if len(rows) >= max_candidates:
                break
    return rows[:max_candidates]


def candidate_from_names(names_text, sector_profile):
    names = [x.strip() for x in re.split(r"[\n;]+", names_text or "") if x.strip()]
    return [{"school_name": n, "sector": sector_profile, "source": "manual_name", "website": "", "osm_phone": "", "address": "", "latitude": "", "longitude": ""} for n in names]


def candidate_from_urls(urls_text, sector_profile):
    urls = [x.strip() for x in re.split(r"[\n,;]+", urls_text or "") if x.strip()]
    rows = []
    for u in urls:
        if not u.startswith("http"):
            u = "https://" + u
        name = urlparse(u).netloc.replace("www.", "").split(".")[0].replace("-", " ").title()
        rows.append({"school_name": name, "sector": sector_profile, "source": "manual_url", "website": u, "osm_phone": "", "address": "", "latitude": "", "longitude": ""})
    return rows


def scrape_page(url):
    r = safe_get(url, timeout=TIMEOUT)
    if not r or r.status_code >= 400 or not r.text:
        return "", []
    soup = BeautifulSoup(r.text, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        label = a.get_text(" ", strip=True).lower() + " " + href.lower()
        links.append((label, href))
    return text, links


def target_pages(homepage, sector_profile):
    if not homepage:
        return []
    pages = [homepage]
    text, links = scrape_page(homepage)
    paths = SECTOR_PROFILES[sector_profile]["contact_paths"]
    for label, href in links:
        if urlparse(href).netloc != urlparse(homepage).netloc:
            continue
        if any(p in label for p in paths):
            pages.append(href)
    # common paths fallback
    root = f"{urlparse(homepage).scheme}://{urlparse(homepage).netloc}/"
    for p in paths:
        pages.append(urljoin(root, p))
    seen = []
    for p in pages:
        if p not in seen:
            seen.append(p)
    return seen[:10]


def search_contact_fallback(name, location_hint, region):
    """Search web for contacts/phones when the official website is missing or incomplete.
    Returns emails, phones, source urls, and diagnostics.
    """
    emails, phones, urls = set(), set(), []
    diagnostics = {
        "search_fallback_executed": "yes",
        "search_queries_run": 0,
        "search_result_urls_checked": 0,
        "search_errors": [],
    }
    if not name:
        diagnostics["search_fallback_executed"] = "no_name"
        return [], [], [], diagnostics

    # Broader contact-intent queries. Keep bounded so Streamlit Cloud does not hang.
    loc = location_hint or ""
    queries = [
        f'"{name}" "phone"',
        f'"{name}" "contact"',
        f'"{name}" "admissions"',
        f'"{name}" "office"',
        f'"{name}" "reception"',
        f'"{name}" "campus"',
        f'"{name}" "principal"',
        f'"{name}" "staff"',
        f'"{name}" "{loc}" "+27"' if country_code_from_location(loc) == "ZA" else f'"{name}" "{loc}" phone',
    ]
    seen_urls = set()
    for q in queries[:7]:
        diagnostics["search_queries_run"] += 1
        try:
            results = ddg_html_search(q, max_results=4)
        except Exception as e:
            diagnostics["search_errors"].append(f"{q}: {type(e).__name__}")
            results = []
        for res in results:
            u = res.get("url", "")
            if not u.startswith("http") or u in seen_urls:
                continue
            seen_urls.add(u)
            urls.append(u)
            diagnostics["search_result_urls_checked"] += 1
            # Search snippets/titles sometimes contain email/phone, so inspect them too.
            snippet_text = " ".join([res.get("title", ""), res.get("url", "")])
            emails.update(extract_emails(snippet_text))
            phones.update(extract_phones(snippet_text, region))
            text, _ = scrape_page(u)
            if text:
                emails.update(extract_emails(text))
                phones.update(extract_phones(text, region))
            if len(phones) >= 4 and len(emails) >= 3:
                break
        if len(phones) >= 4 and len(emails) >= 3:
            break

    diagnostics["search_found_emails_count"] = len(emails)
    diagnostics["search_found_phones_count"] = len(phones)
    diagnostics["search_errors"] = "; ".join(diagnostics["search_errors"][:5])
    return sorted(emails), sorted(phones), sorted(set(urls))[:8], diagnostics

def enrich_one(row, location_hint, region, use_search_fallback):
    r = dict(row)
    name = str(r.get("school_name", "") or "")
    original_website = str(r.get("website", "") or "").strip()
    r["original_website"] = original_website

    # Preserve discovered website. Resolution is only allowed to fill blanks, never erase a source website.
    if not original_website:
        site, method = resolve_website_for_name(name, location_hint)
        r["website"] = site or ""
        r["website_resolution_method"] = method
    else:
        r["website"] = original_website
        r["website_resolution_method"] = "source_preserved"

    emails, website_phones, pages = set(), set(), []
    role_hits = set()
    scrape_pages_attempted = 0
    scrape_pages_successful = 0
    scrape_failure_reason = ""

    if r.get("website"):
        candidate_pages = target_pages(r["website"], r.get("sector", "Schools"))
        for p in candidate_pages:
            scrape_pages_attempted += 1
            text, _ = scrape_page(p)
            if not text:
                scrape_failure_reason = "empty_or_blocked_pages"
                continue
            scrape_pages_successful += 1
            pages.append(p)
            emails.update(extract_emails(text))
            website_phones.update(extract_phones(text, region))
            low = text.lower()
            for role in ["principal", "head of school", "admissions", "counsellor", "counselor", "learning support", "sen", "reception", "office", "campus", "staff", "leadership"]:
                if role in low:
                    role_hits.add(role)
        if scrape_pages_attempted and not scrape_pages_successful:
            scrape_failure_reason = scrape_failure_reason or "all_pages_failed"

    # Search fallback should run when the site/contact scrape is incomplete, and also after scrape failure.
    search_emails, search_phones, search_urls, search_diag = [], [], [], {
        "search_fallback_executed": "no",
        "search_queries_run": 0,
        "search_result_urls_checked": 0,
        "search_found_emails_count": 0,
        "search_found_phones_count": 0,
        "search_errors": "",
    }
    if use_search_fallback and (not emails or not website_phones or not r.get("website") or (scrape_pages_attempted and not scrape_pages_successful)):
        search_emails, search_phones, search_urls, search_diag = search_contact_fallback(name, location_hint, region)
        emails.update(search_emails)

    # Explicit merge hierarchy: website > search/directory > OSM.
    osm_phones = extract_phones(r.get("osm_phone", ""), region)
    website_phone_list = sorted(website_phones)
    search_phone_list = sorted(search_phones)
    osm_phone_list = sorted(osm_phones)
    all_phones = list(dict.fromkeys(website_phone_list + search_phone_list + osm_phone_list))
    best_phone = ""
    phone_source, phone_conf = "", ""
    if website_phone_list:
        best_phone = website_phone_list[0]
        phone_source, phone_conf = "website", "high"
    elif search_phone_list:
        best_phone = search_phone_list[0]
        phone_source, phone_conf = "search", "medium"
    elif osm_phone_list:
        best_phone = osm_phone_list[0]
        phone_source, phone_conf = "osm", "medium"

    sorted_emails = sorted(emails)
    generic_emails = [e for e in sorted_emails if re.match(r"^(info|admin|office|admissions|contact|reception|hello|enquiries|enquiry)@", e.lower())]
    search_email_only = sorted(set(search_emails))

    if r.get("website") and scrape_pages_successful:
        base_status = "scraped"
    elif r.get("website") and scrape_pages_attempted and not scrape_pages_successful:
        base_status = "scrape_failed_search_attempted" if use_search_fallback else "scrape_failed"
    elif not r.get("website"):
        base_status = "no_website_search_attempted" if use_search_fallback else "no_website"
    else:
        base_status = "not_enriched"

    r.update({
        "visible_emails": "; ".join(sorted_emails),
        "generic_emails": "; ".join(generic_emails),
        "search_emails": "; ".join(search_email_only),
        "website_phone": "; ".join(website_phone_list),
        "search_phone": "; ".join(search_phone_list),
        "directory_phone": "; ".join(search_phone_list),
        "osm_phone_normalized": "; ".join(osm_phone_list),
        "best_phone": best_phone,
        "phone_source": phone_source,
        "phone_confidence": phone_conf,
        "all_phones_found": "; ".join(all_phones),
        "phone_page": pages[0] if pages else (search_urls[0] if search_urls else ""),
        "search_contact_urls": "; ".join(search_urls),
        "role_signals": "; ".join(sorted(role_hits)),
        "contact_confidence": "high" if sorted_emails and best_phone else ("medium" if sorted_emails or best_phone else "low"),
        "enrichment_status": base_status,
        "scrape_pages_attempted": scrape_pages_attempted,
        "scrape_pages_successful": scrape_pages_successful,
        "scrape_failure_reason": scrape_failure_reason,
        "search_fallback_executed": search_diag.get("search_fallback_executed", "no"),
        "search_queries_run": search_diag.get("search_queries_run", 0),
        "search_result_urls_checked": search_diag.get("search_result_urls_checked", 0),
        "search_found_emails_count": search_diag.get("search_found_emails_count", 0),
        "search_found_phones_count": search_diag.get("search_found_phones_count", 0),
        "search_errors": search_diag.get("search_errors", ""),
    })

    fit = 0
    low_text = " ".join([r.get("school_name", ""), r.get("role_signals", ""), r.get("visible_emails", "")]).lower()
    for kw, pts in [("admissions", 2), ("learning support", 3), ("sen", 2), ("principal", 1), ("counsel", 2), ("international", 2)]:
        if kw in low_text:
            fit += pts
    r["fit_score"] = fit
    return r

def enrich_rows(rows, location_hint, sector_profile, use_search_fallback, progress_label="Enriching"):
    region = country_code_from_location(location_hint)
    out = []
    total = len(rows)
    bar = st.progress(0, text=f"{progress_label}: 0/{total}")
    status = st.empty()
    for i, row in enumerate(rows, start=1):
        status.write(f"{progress_label}: {i}/{total} — {row.get('school_name','')}")
        try:
            out.append(enrich_one(row, location_hint, region, use_search_fallback))
        except Exception as e:
            failed = dict(row)
            failed["enrichment_status"] = f"failed: {type(e).__name__}"
            out.append(failed)
        bar.progress(i / max(total, 1), text=f"{progress_label}: {i}/{total}")
    status.write("Enrichment complete.")
    return out


def make_download_name(kind, mode, query_label):
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    return f"school_discovery_{slug(mode,25)}_{slug(query_label,45)}_{kind}_{ts}.csv"


def df_to_csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


init_state()
st.set_page_config(page_title="School / Sector Discovery Engine", layout="wide")
st.title(f"Discovery & Contact Enrichment Engine {APP_VERSION}")
st.caption("Free Streamlit workflow: discover candidates, enrich websites/phones/emails, export for outreach.")

with st.sidebar:
    sector_profile = st.selectbox("Sector profile", list(SECTOR_PROFILES.keys()), index=0)
    mode = st.radio("Discovery mode", ["Map / geolocation", "School name", "School URL"], index=0)
    use_search_fallback = st.checkbox("Use web search fallback for missing websites/contact details", value=True)
    if st.button("Clear results / cache"):
        for k in ["map_cache_key", "map_candidates", "raw_df", "enriched_df"]:
            st.session_state[k] = None
        st.session_state.debug_log = []
        st.rerun()

if mode == "Map / geolocation":
    location = st.text_input("Location", value="Cape Town, Western Cape, South Africa")
    radius_km = st.slider("Radius (km)", min_value=1, max_value=150, value=25)
    max_candidates = st.number_input("Max candidates", min_value=10, max_value=500, value=100, step=10)
    query_label = f"{sector_profile} {location} {radius_km}km {max_candidates}"
    current_key = cache_key(sector_profile, location.strip().lower(), radius_km, max_candidates)
    can_run = st.button("Run / enrich map search", type="primary")
    if can_run:
        st.session_state.debug_log = []
        # v18.1 fix: cached path skips discovery/geocoding visible step entirely.
        if st.session_state.map_cache_key == current_key and st.session_state.map_candidates is not None:
            rows = st.session_state.map_candidates
            st.info(f"Using cached map candidate set: {len(rows)} candidates. Skipping geocoding/discovery and rerunning enrichment only.")
        else:
            st.info("Map inputs changed. Geocoding and finding candidates...")
            rows = discover_map(location, radius_km, int(max_candidates), sector_profile)
            st.session_state.map_cache_key = current_key
            st.session_state.map_candidates = rows
            st.success(f"Discovery complete: {len(rows)} candidates retained. Starting enrichment next.")
        st.session_state.raw_df = pd.DataFrame(rows)
        enriched = enrich_rows(rows, location, sector_profile, use_search_fallback, progress_label="Enriching cached candidates" if st.session_state.map_cache_key == current_key else "Enriching candidates")
        st.session_state.enriched_df = pd.DataFrame(enriched)
        st.session_state.last_mode = "map_geolocation"
        st.session_state.last_query_label = query_label

elif mode == "School name":
    location = st.text_input("Location hint for website/phone validation", value="Cape Town, Western Cape, South Africa")
    names = st.text_area("School / organization names", height=180, placeholder="One per line")
    query_label = names.splitlines()[0] if names.strip() else "manual_names"
    if st.button("Resolve and enrich names", type="primary"):
        rows = candidate_from_names(names, sector_profile)
        st.session_state.raw_df = pd.DataFrame(rows)
        enriched = enrich_rows(rows, location, sector_profile, use_search_fallback)
        st.session_state.enriched_df = pd.DataFrame(enriched)
        st.session_state.last_mode = "school_name"
        st.session_state.last_query_label = query_label

else:
    location = st.text_input("Location hint for phone validation", value="Cape Town, Western Cape, South Africa")
    urls = st.text_area("School / organization URLs", height=180, placeholder="One per line")
    query_label = "manual_urls"
    if st.button("Scrape and enrich URLs", type="primary"):
        rows = candidate_from_urls(urls, sector_profile)
        st.session_state.raw_df = pd.DataFrame(rows)
        enriched = enrich_rows(rows, location, sector_profile, use_search_fallback)
        st.session_state.enriched_df = pd.DataFrame(enriched)
        st.session_state.last_mode = "school_url"
        st.session_state.last_query_label = query_label

if st.session_state.raw_df is not None:
    st.subheader("Raw candidates")
    st.dataframe(st.session_state.raw_df, use_container_width=True, hide_index=True)
    st.download_button(
        "Download raw candidates CSV",
        data=df_to_csv_bytes(st.session_state.raw_df),
        file_name=make_download_name("raw", st.session_state.last_mode or mode, st.session_state.last_query_label),
        mime="text/csv",
        key="download_raw",
    )

if st.session_state.enriched_df is not None:
    st.subheader("Enriched results")
    df = st.session_state.enriched_df
    metrics = st.columns(6)
    metrics[0].metric("Rows", len(df))
    metrics[1].metric("Websites", int(df.get("website", pd.Series(dtype=str)).fillna("").astype(bool).sum()) if "website" in df else 0)
    metrics[2].metric("Emails", int(df.get("visible_emails", pd.Series(dtype=str)).fillna("").astype(bool).sum()) if "visible_emails" in df else 0)
    metrics[3].metric("Phones", int(df.get("best_phone", pd.Series(dtype=str)).fillna("").astype(bool).sum()) if "best_phone" in df else 0)
    metrics[4].metric("Search phones", int(df.get("search_phone", pd.Series(dtype=str)).fillna("").astype(bool).sum()) if "search_phone" in df else 0)
    metrics[5].metric("Search fallback runs", int((df.get("search_fallback_executed", pd.Series(dtype=str)).fillna("") == "yes").sum()) if "search_fallback_executed" in df else 0)
    with st.expander("Enrichment diagnostics"):
        diag_cols = [c for c in ["school_name", "enrichment_status", "website", "original_website", "scrape_pages_attempted", "scrape_pages_successful", "search_fallback_executed", "search_queries_run", "search_result_urls_checked", "search_found_emails_count", "search_found_phones_count", "search_errors"] if c in df.columns]
        st.dataframe(df[diag_cols], use_container_width=True, hide_index=True)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button(
        "Download enriched CSV",
        data=df_to_csv_bytes(df),
        file_name=make_download_name("enriched", st.session_state.last_mode or mode, st.session_state.last_query_label),
        mime="text/csv",
        key="download_enriched",
    )

with st.expander("Debug log"):
    st.code("\n".join(st.session_state.debug_log[-200:]) or "No debug log yet.")
