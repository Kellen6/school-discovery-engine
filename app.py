import io
import re
import time
import hashlib
from datetime import datetime
from urllib.parse import urljoin, urlparse, quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

try:
    import phonenumbers
except Exception:
    phonenumbers = None

APP_VERSION = "21.1"
USER_AGENT = "Mozilla/5.0 (compatible; SchoolDiscoveryEngine/21.1; +https://streamlit.app)"
TIMEOUT_FAST = 8
TIMEOUT_STD = 12

st.set_page_config(page_title="Prospect Discovery Engine", layout="wide")

# ----------------------------- state -----------------------------
def init_state():
    defaults = {
        "debug_log": [],
        "prospects": None,
        "last_map_key": None,
        "last_candidates": None,
        "timing": {},
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

# ----------------------------- helpers -----------------------------
def now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M")

def slugify(s, max_len=64):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(s).lower()).strip("_")
    return s[:max_len] or "prospects"

def make_filename(mode, query, kind, ext):
    return f"prospect_discovery_{slugify(mode)}_{slugify(query, 48)}_{kind}_{now_stamp()}.{ext}"

def clean_text(x):
    return re.sub(r"\s+", " ", str(x or "")).strip()

def normalize_url(url):
    if not url:
        return ""
    url = str(url).strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url

def domain(url):
    try:
        d = urlparse(normalize_url(url)).netloc.lower()
        return d.replace("www.", "")
    except Exception:
        return ""

def http_get(url, timeout=TIMEOUT_FAST):
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, allow_redirects=True)
        return r
    except Exception as e:
        return e

EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+\s*(?:@|\[at\]|\(at\)| at )\s*[A-Z0-9.\-]+\s*(?:\.|\[dot\]|\(dot\)| dot )\s*[A-Z]{2,}", re.I)

def normalize_email(e):
    e = e.strip().lower()
    e = re.sub(r"\s*(\[at\]|\(at\)| at )\s*", "@", e)
    e = re.sub(r"\s*(\[dot\]|\(dot\)| dot )\s*", ".", e)
    e = re.sub(r"\s+", "", e)
    e = e.strip(".,;:()[]{}<>")
    return e

def extract_emails(text):
    if not text:
        return []
    emails = []
    for m in EMAIL_RE.findall(text):
        e = normalize_email(m)
        if "@" in e and "." in e.split("@")[-1] and len(e) < 90:
            if not any(b in e for b in ["example.com", "domain.com", "email.com"]):
                emails.append(e)
    return sorted(set(emails))

def classify_generic_emails(emails):
    generic_prefixes = ("info", "office", "admin", "admissions", "enrol", "enroll", "reception", "contact", "hello", "principal", "secretary")
    return sorted({e for e in emails if e.split("@")[0].startswith(generic_prefixes)})

def country_code_from_country(country):
    mapping = {
        "south africa": "ZA", "nigeria": "NG", "kenya": "KE", "ghana": "GH", "tanzania": "TZ",
        "uganda": "UG", "rwanda": "RW", "united kingdom": "GB", "uk": "GB", "united states": "US",
        "canada": "CA", "zambia": "ZM", "zimbabwe": "ZW", "botswana": "BW", "namibia": "NA",
    }
    return mapping.get(str(country or "").lower().strip(), "ZA")

def extract_phones(text, country="South Africa"):
    if not text:
        return []
    cc = country_code_from_country(country)
    found = set()
    if phonenumbers:
        for match in phonenumbers.PhoneNumberMatcher(text, cc):
            try:
                num = match.number
                if phonenumbers.is_possible_number(num) and phonenumbers.is_valid_number(num):
                    found.add(phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
            except Exception:
                pass
    # fallback patterns, filtered to avoid coordinates and dummy numbers
    patterns = [r"\+\d{1,3}[\s\-\(\)]?\d[\d\s\-\(\)]{6,}\d", r"\b0\d{1,3}[\s\-]?\d{3}[\s\-]?\d{3,4}\b"]
    for pat in patterns:
        for raw in re.findall(pat, text):
            digits = re.sub(r"\D", "", raw)
            if len(digits) < 9 or len(digits) > 15:
                continue
            if len(set(digits)) <= 2:
                continue
            if raw.count(".") >= 1:
                continue
            if phonenumbers:
                try:
                    num = phonenumbers.parse(raw, cc)
                    if phonenumbers.is_valid_number(num):
                        found.add(phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
                except Exception:
                    pass
            else:
                found.add(clean_text(raw))
    return sorted(found)

BAD_ENTITY_TERMS = ["testing yard", "driver", "driving", "licence", "license", "traffic department", "parking", "sports field"]
GOOD_ENTITY_TERMS = ["school", "college", "academy", "primary", "high", "pre-primary", "pre primary", "campus", "university", "institute"]

def is_likely_prospect(name, sector="Schools"):
    n = str(name or "").lower()
    if not n or len(n) < 3:
        return False
    if any(t in n for t in BAD_ENTITY_TERMS):
        return False
    # keep schools and colleges; allow names without terms because OSM may be clean
    return True

# ----------------------------- discovery -----------------------------
def geocode(location):
    url = f"https://nominatim.openstreetmap.org/search?q={quote_plus(location)}&format=jsonv2&limit=1&addressdetails=1"
    r = http_get(url, timeout=12)
    if isinstance(r, Exception):
        log(f"Geocode error: {type(r).__name__}: {r}")
        return None
    log(f"Geocode HTTP {r.status_code}: {url}")
    if r.status_code != 200:
        return None
    data = r.json()
    if not data:
        return None
    hit = data[0]
    return {
        "lat": float(hit["lat"]),
        "lon": float(hit["lon"]),
        "display_name": hit.get("display_name", location),
        "country": (hit.get("address") or {}).get("country", ""),
    }

def overpass_query(lat, lon, radius_m, max_results, sector="Schools"):
    # Broader education tags
    q = f"""
    [out:json][timeout:25];
    (
      node(around:{int(radius_m)},{lat},{lon})[amenity~"school|college|university|kindergarten"];
      way(around:{int(radius_m)},{lat},{lon})[amenity~"school|college|university|kindergarten"];
      relation(around:{int(radius_m)},{lat},{lon})[amenity~"school|college|university|kindergarten"];
      node(around:{int(radius_m)},{lat},{lon})[building~"school|college|university"];
      way(around:{int(radius_m)},{lat},{lon})[building~"school|college|university"];
    );
    out center tags {int(max_results)};
    """
    endpoints = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.osm.ch/api/interpreter"]
    all_rows = []
    for ep in endpoints:
        try:
            r = requests.post(ep, data={"data": q}, headers={"User-Agent": USER_AGENT}, timeout=30)
            log(f"Overpass POST {ep}: HTTP {r.status_code}")
            if r.status_code != 200:
                continue
            data = r.json()
            elems = data.get("elements", [])
            log(f"Overpass {ep}: {len(elems)} elements")
            for e in elems:
                tags = e.get("tags") or {}
                name = tags.get("name") or tags.get("official_name") or ""
                if not is_likely_prospect(name, sector):
                    continue
                row = {
                    "prospect_name": name,
                    "sector": sector,
                    "source": "overpass",
                    "website": tags.get("website") or tags.get("contact:website") or "",
                    "osm_phone": tags.get("phone") or tags.get("contact:phone") or "",
                    "address": ", ".join([tags.get(k, "") for k in ["addr:housenumber", "addr:street", "addr:city"] if tags.get(k)]),
                    "lat": e.get("lat") or (e.get("center") or {}).get("lat"),
                    "lon": e.get("lon") or (e.get("center") or {}).get("lon"),
                    "country": "",
                }
                all_rows.append(row)
            if all_rows:
                break
        except Exception as e:
            log(f"Overpass error {ep}: {type(e).__name__}: {e}")
    return all_rows

def nominatim_search_terms(sector):
    if sector.lower().startswith("school"):
        return ["school", "private school", "international school", "college", "university", "academy"]
    return [sector.lower(), f"{sector.lower()} near"]

def nominatim_candidates(location, max_results, sector="Schools"):
    rows = []
    for term in nominatim_search_terms(sector):
        url = f"https://nominatim.openstreetmap.org/search?q={quote_plus(term + ' in ' + location)}&format=jsonv2&limit={min(50,max_results)}&addressdetails=1&extratags=1"
        r = http_get(url, timeout=12)
        if isinstance(r, Exception):
            log(f"Nominatim error {term}: {type(r).__name__}: {r}")
            continue
        log(f"Nominatim '{term} in {location}': HTTP {r.status_code}")
        if r.status_code != 200:
            continue
        try:
            data = r.json() or []
        except Exception:
            continue
        for hit in data:
            name = hit.get("name") or hit.get("display_name", "").split(",")[0]
            if not is_likely_prospect(name, sector):
                continue
            extra = hit.get("extratags") or {}
            addr = hit.get("address") or {}
            rows.append({
                "prospect_name": clean_text(name),
                "sector": sector,
                "source": "nominatim",
                "website": extra.get("website") or extra.get("contact:website") or "",
                "osm_phone": extra.get("phone") or extra.get("contact:phone") or "",
                "address": hit.get("display_name", ""),
                "lat": hit.get("lat"),
                "lon": hit.get("lon"),
                "country": addr.get("country", ""),
            })
    return rows

def dedupe_rows(rows, max_results=None):
    out = []
    seen = set()
    for r in rows:
        name = clean_text(r.get("prospect_name"))
        if not name:
            continue
        key = (re.sub(r"\W+", "", name.lower()), domain(r.get("website")) or clean_text(r.get("address"))[:60].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if max_results and len(out) >= max_results:
            break
    return out

def discover_map(location, radius_km, max_results, sector):
    t0 = time.time()
    geo = geocode(location)
    rows = []
    if geo:
        log(f"Geocoded to: {geo['display_name']} ({geo['lat']:.5f},{geo['lon']:.5f})")
        rows.extend(overpass_query(geo["lat"], geo["lon"], radius_km*1000, max_results, sector))
    if len(rows) < max_results:
        rows.extend(nominatim_candidates(location, max_results, sector))
    rows = dedupe_rows(rows, max_results=max_results)
    country = geo.get("country", "") if geo else ""
    for r in rows:
        if not r.get("country"):
            r["country"] = country
    st.session_state.timing["discovery_seconds"] = round(time.time() - t0, 1)
    return rows

# ----------------------------- website resolution -----------------------------
def ddg_search(query, max_results=5, timeout=10):
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    r = http_get(url, timeout=timeout)
    if isinstance(r, Exception) or r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for a in soup.select("a.result__a")[:max_results]:
        href = a.get("href") or ""
        title = clean_text(a.get_text(" "))
        results.append({"title": title, "url": href})
    # fallback generic links
    if not results:
        for a in soup.find_all("a", href=True)[:max_results*3]:
            href = a.get("href")
            title = clean_text(a.get_text(" "))
            if href and href.startswith("http") and "duckduckgo" not in href:
                results.append({"title": title, "url": href})
                if len(results) >= max_results:
                    break
    return results

def score_official_site(name, url, title="", location_hint=""):
    u = normalize_url(url)
    d = domain(u)
    if not d:
        return -10
    bad_domains = ["facebook.com", "instagram.com", "linkedin.com", "wikipedia.org", "youtube.com", "google.com", "yelp", "tripadvisor", "saschools", "schools4sa", "schoolguide"]
    score = 0
    if any(b in d for b in bad_domains):
        score -= 4
    name_tokens = [t for t in re.findall(r"[a-z0-9]+", name.lower()) if len(t) > 2 and t not in ["the", "school", "college", "academy", "primary", "high"]]
    blob = (d + " " + title.lower() + " " + u.lower())
    score += sum(2 for t in name_tokens if t in blob)
    if any(x in d for x in ["school", "college", "academy", "edu"]):
        score += 2
    if ".ac." in d or ".edu" in d or d.endswith(".school"):
        score += 2
    if "official" in title.lower():
        score += 1
    return score

def resolve_website_for_name(name, location_hint="", mode="Fast"):
    if not name:
        return "", ""
    queries = [f'"{name}" official website', f'"{name}" {location_hint} school']
    if mode == "Deep":
        queries += [f'"{name}" contact', f'"{name}" admissions']
    best = ("", "", -999)
    for q in queries[:2 if mode == "Fast" else 4]:
        try:
            for res in ddg_search(q, max_results=5, timeout=8 if mode=="Fast" else 12):
                s = score_official_site(name, res["url"], res.get("title",""), location_hint)
                if s > best[2]:
                    best = (normalize_url(res["url"]), "web_search", s)
        except Exception:
            continue
    if best[2] >= 2:
        return best[0], best[1]
    # domain guesses for common SA school domains
    base = re.sub(r"\b(the|school|college|academy|primary|high|pre|campus)\b", "", name.lower())
    base = re.sub(r"[^a-z0-9]+", "", base)
    guesses = [f"https://www.{base}.co.za", f"https://{base}.co.za", f"https://www.{base}.org.za", f"https://{base}.org.za"]
    for g in guesses[:2 if mode=="Fast" else 4]:
        r = http_get(g, timeout=5)
        if not isinstance(r, Exception) and r.status_code < 400:
            return g, "domain_guess"
    return "", ""

# ----------------------------- scraping -----------------------------
def candidate_contact_paths(mode="Fast"):
    paths = ["", "/contact", "/contact-us"]
    if mode in ["Standard", "Deep"]:
        paths += ["/admissions", "/enrolments", "/enrollment", "/about", "/staff", "/leadership"]
    if mode == "Deep":
        paths += ["/office", "/reception", "/campus", "/contact-details", "/our-school"]
    return paths

def find_internal_contact_links(base_url, html, mode="Fast"):
    links = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        terms = ["contact", "admission", "enrol", "staff", "leadership", "office", "reception"]
        for a in soup.find_all("a", href=True):
            txt = (a.get_text(" ") + " " + a.get("href", "")).lower()
            if any(t in txt for t in terms):
                links.append(urljoin(base_url, a.get("href")))
    except Exception:
        pass
    max_links = 2 if mode == "Fast" else 5 if mode == "Standard" else 8
    return list(dict.fromkeys(links))[:max_links]

def scrape_site(row, mode="Fast", use_search_fallback=False, location_hint=""):
    name = row.get("prospect_name", "")
    country = row.get("country") or "South Africa"
    website = normalize_url(row.get("website", ""))
    website_method = row.get("website_source", "map") if website else ""
    if not website:
        website, website_method = resolve_website_for_name(name, location_hint, mode=mode)

    result = dict(row)
    result.update({
        "website": website or row.get("website", ""),
        "website_source": website_method,
        "visible_emails": "",
        "generic_emails": "",
        "search_emails": "",
        "website_phone": "",
        "search_phone": "",
        "best_email": "",
        "best_phone": row.get("osm_phone", ""),
        "phone_source": "osm" if row.get("osm_phone") else "",
        "phone_confidence": "medium" if row.get("osm_phone") else "",
        "source_pages": "",
        "enrichment_status": "no_website" if not website else "pending",
    })

    texts = []
    pages = []
    if website:
        urls = [urljoin(website, p) for p in candidate_contact_paths(mode)]
        fetched_home = None
        for idx, u in enumerate(list(dict.fromkeys(urls))):
            r = http_get(u, timeout=TIMEOUT_FAST if mode=="Fast" else TIMEOUT_STD)
            if isinstance(r, Exception) or getattr(r, "status_code", 999) >= 400 or "text/html" not in r.headers.get("content-type", "text/html"):
                continue
            html = r.text or ""
            if idx == 0:
                fetched_home = html
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(" ")
            texts.append(text)
            pages.append(u)
            # Fast keeps small; Standard/Deep can expand from actual nav links
            if idx == 0 and mode != "Fast":
                for lnk in find_internal_contact_links(u, html, mode):
                    if lnk not in urls:
                        urls.append(lnk)
            if mode == "Fast" and len(pages) >= 2:
                break
            if mode == "Standard" and len(pages) >= 5:
                break
            if mode == "Deep" and len(pages) >= 9:
                break
    combined = "\n".join(texts)
    emails = extract_emails(combined)
    phones = extract_phones(combined, country)
    result["visible_emails"] = "; ".join(emails)
    result["generic_emails"] = "; ".join(classify_generic_emails(emails))
    result["website_phone"] = "; ".join(phones)
    result["source_pages"] = "; ".join(pages[:8])

    search_emails, search_phones = [], []
    if use_search_fallback and mode in ["Standard", "Deep"]:
        queries = [f'"{name}" phone', f'"{name}" contact', f'"{name}" admissions']
        if mode == "Deep":
            queries += [f'"{name}" reception', f'"{name}" office', f'"{name}" +27']
        snippets = []
        for q in queries[:3 if mode=="Standard" else 6]:
            for res in ddg_search(q, max_results=4, timeout=10):
                snippets.append(res.get("title", "") + " " + res.get("url", ""))
                # open only likely non-social pages in Deep
                if mode == "Deep" and not any(b in domain(res.get("url","")) for b in ["facebook.com", "instagram.com", "linkedin.com"]):
                    rr = http_get(res.get("url"), timeout=8)
                    if not isinstance(rr, Exception) and rr.status_code < 400:
                        snippets.append(BeautifulSoup(rr.text, "html.parser").get_text(" ")[:5000])
        blob = "\n".join(snippets)
        search_emails = extract_emails(blob)
        search_phones = extract_phones(blob, country)
        result["search_emails"] = "; ".join(search_emails)
        result["search_phone"] = "; ".join(search_phones)

    # Merge hierarchy: website > search > OSM
    best_email = ""
    for pool in [classify_generic_emails(emails), emails, search_emails]:
        if pool:
            best_email = pool[0]
            break
    result["best_email"] = best_email

    if phones:
        result["best_phone"] = phones[0]
        result["phone_source"] = "website"
        result["phone_confidence"] = "high"
    elif search_phones:
        result["best_phone"] = search_phones[0]
        result["phone_source"] = "search"
        result["phone_confidence"] = "medium"
    elif row.get("osm_phone"):
        osm_p = extract_phones(row.get("osm_phone"), country)
        result["best_phone"] = osm_p[0] if osm_p else row.get("osm_phone")
        result["phone_source"] = "map"
        result["phone_confidence"] = "medium"

    if website and pages:
        result["enrichment_status"] = "scraped"
    elif website and not pages:
        result["enrichment_status"] = "scrape_failed"
    elif not website:
        result["enrichment_status"] = "no_website"

    # simple fit/contact score
    contact_score = 0
    if result.get("website"): contact_score += 2
    if result.get("best_email"): contact_score += 3
    if result.get("best_phone"): contact_score += 2
    if result.get("generic_emails"): contact_score += 1
    result["contact_score"] = contact_score
    return result

def enrich_rows(rows, mode="Fast", use_search_fallback=False, location_hint="", max_workers=6):
    t0 = time.time()
    out = []
    progress = st.progress(0)
    status = st.empty()
    total = len(rows)
    workers = min(max_workers, 4 if mode == "Deep" else 8)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(scrape_site, r, mode, use_search_fallback, location_hint): i for i, r in enumerate(rows)}
        done = 0
        for fut in as_completed(futures):
            done += 1
            try:
                out.append(fut.result())
            except Exception as e:
                r = dict(rows[futures[fut]])
                r["enrichment_status"] = f"error: {type(e).__name__}"
                out.append(r)
            progress.progress(done / max(total,1))
            status.write(f"Enriching prospects: {done}/{total}")
    progress.empty(); status.empty()
    # preserve input order
    name_order = {clean_text(r.get("prospect_name")): i for i, r in enumerate(rows)}
    out.sort(key=lambda r: name_order.get(clean_text(r.get("prospect_name")), 999999))
    st.session_state.timing["enrichment_seconds"] = round(time.time() - t0, 1)
    return out

# ----------------------------- exports -----------------------------
def to_csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")

def to_excel_bytes(df):
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Prospects")
    return bio.getvalue()

def display_columns(df):
    cols = [
        "prospect_name", "website", "best_email", "best_phone", "phone_source", "contact_score",
        "visible_emails", "generic_emails", "website_phone", "search_phone", "address", "enrichment_status"
    ]
    return [c for c in cols if c in df.columns]

# ----------------------------- UI -----------------------------
st.title("Prospect Discovery Engine")
st.caption(f"v{APP_VERSION} · Find prospects, enrich contact details, and export outreach-ready lists")

with st.sidebar:
    st.header("Search setup")
    sector = st.selectbox("Sector profile", ["Schools", "Universities", "Clinics", "NGOs", "Companies"], index=0)
    mode_choice = st.radio("Discovery method", ["Map / geolocation", "Prospect name", "Prospect URL", "Source/list page"], index=0)
    enrichment_mode = st.selectbox("Enrichment depth", ["Fast", "Standard", "Deep"], index=0, help="Fast is closest to v18 speed. Standard/Deep try harder but run slower.")
    use_search = st.checkbox("Use web search fallback for missing contacts", value=False, help="Slower. Best used with Standard or Deep.")
    max_workers = st.slider("Parallel workers", 2, 10, 6)
    st.divider()
    if st.button("Clear results/cache"):
        st.session_state.prospects = None
        st.session_state.last_candidates = None
        st.session_state.last_map_key = None
        st.session_state.debug_log = []
        st.rerun()

query_label = ""
rows = None

if mode_choice == "Map / geolocation":
    c1, c2, c3 = st.columns([3,1,1])
    with c1:
        location = st.text_input("Location", value="Cape Town, Western Cape, South Africa")
    with c2:
        radius = st.number_input("Radius (km)", min_value=1, max_value=250, value=10)
    with c3:
        max_results = st.number_input("Max prospects", min_value=10, max_value=500, value=100, step=10)
    query_label = f"{sector} {location}"
    map_key = hashlib.sha256(f"{sector}|{location}|{radius}|{max_results}".encode()).hexdigest()
    if st.button("Find prospects", type="primary"):
        st.session_state.debug_log = []
        st.session_state.timing = {}
        if st.session_state.last_map_key == map_key and st.session_state.last_candidates is not None:
            st.info(f"Using saved prospect set: {len(st.session_state.last_candidates)} prospects. Re-running contact enrichment only.")
            candidates = st.session_state.last_candidates
        else:
            if st.session_state.last_map_key is None:
                st.info("Starting new prospect search…")
            else:
                st.info("Search area or filters changed. Finding a new prospect set…")
            with st.spinner("Finding prospects from map/location data…"):
                candidates = discover_map(location, int(radius), int(max_results), sector)
            st.session_state.last_map_key = map_key
            st.session_state.last_candidates = candidates
            st.success(f"Found {len(candidates)} prospects. Starting contact enrichment…")
        prospects = enrich_rows(candidates, enrichment_mode, use_search, location, max_workers=max_workers)
        st.session_state.prospects = prospects

elif mode_choice == "Prospect name":
    location = st.text_input("Location hint", value="Cape Town, Western Cape, South Africa")
    names = st.text_area("Enter one prospect/school name per line", height=180)
    query_label = names.splitlines()[0] if names.strip() else "name_search"
    if st.button("Find prospects", type="primary"):
        candidates = [{"prospect_name": clean_text(n), "sector": sector, "source": "manual_name", "website": "", "osm_phone": "", "address": "", "country": "South Africa"} for n in names.splitlines() if clean_text(n)]
        st.session_state.prospects = enrich_rows(candidates, enrichment_mode, True if use_search else False, location, max_workers=max_workers)

elif mode_choice == "Prospect URL":
    urls = st.text_area("Enter one website URL per line", height=180)
    query_label = "url_list"
    if st.button("Find prospects", type="primary"):
        candidates = []
        for u in urls.splitlines():
            u = normalize_url(u)
            if not u: continue
            name = domain(u).split(".")[0].replace("-", " ").title()
            candidates.append({"prospect_name": name, "sector": sector, "source": "manual_url", "website": u, "osm_phone": "", "address": "", "country": "South Africa"})
        st.session_state.prospects = enrich_rows(candidates, enrichment_mode, use_search, "", max_workers=max_workers)

else:  # source/list page
    source_urls = st.text_area("Enter source/list page URLs", height=160, help="Example: best schools pages, directories, association lists")
    query_label = "source_pages"
    if st.button("Find prospects", type="primary"):
        candidates = []
        for su in source_urls.splitlines():
            su = normalize_url(su)
            r = http_get(su, timeout=15)
            if isinstance(r, Exception) or r.status_code >= 400:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                txt = clean_text(a.get_text(" "))
                href = urljoin(su, a.get("href"))
                if not href.startswith("http") or not txt or len(txt) < 3:
                    continue
                if any(b in domain(href) for b in ["facebook.com", "instagram.com", "linkedin.com", "google.com"]):
                    continue
                if is_likely_prospect(txt, sector):
                    candidates.append({"prospect_name": txt, "sector": sector, "source": "source_page", "website": href, "osm_phone": "", "address": "", "country": "South Africa"})
        candidates = dedupe_rows(candidates, max_results=300)
        st.session_state.prospects = enrich_rows(candidates, enrichment_mode, use_search, "", max_workers=max_workers)

# Results
prospects = st.session_state.prospects
if prospects:
    df = pd.DataFrame(prospects)
    st.subheader("Prospects")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Prospects", len(df))
    m2.metric("Websites", int(df.get("website", pd.Series(dtype=str)).astype(str).str.len().gt(0).sum()) if "website" in df else 0)
    m3.metric("Emails", int(df.get("best_email", pd.Series(dtype=str)).astype(str).str.len().gt(0).sum()) if "best_email" in df else 0)
    m4.metric("Phones", int(df.get("best_phone", pd.Series(dtype=str)).astype(str).str.len().gt(0).sum()) if "best_phone" in df else 0)

    st.dataframe(df[display_columns(df)], use_container_width=True, height=420)

    st.subheader("Export")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("Download CSV", data=to_csv_bytes(df), file_name=make_filename(mode_choice, query_label, "prospects", "csv"), mime="text/csv")
    with c2:
        st.download_button("Download Excel", data=to_excel_bytes(df), file_name=make_filename(mode_choice, query_label, "prospects", "xlsx"), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with st.expander("Diagnostics / full fields"):
        st.write("Timing", st.session_state.timing)
        st.dataframe(df, use_container_width=True, height=300)
        if st.session_state.debug_log:
            st.text("\n".join(st.session_state.debug_log[-80:]))
else:
    st.info("Choose a discovery method and click **Find prospects**.")
    with st.expander("Diagnostics"):
        if st.session_state.debug_log:
            st.text("\n".join(st.session_state.debug_log[-80:]))
        else:
            st.write("No diagnostics yet.")
