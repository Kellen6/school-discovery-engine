
import streamlit as st
import pandas as pd
import requests, re, time, json, hashlib, io
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin, quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import phonenumbers
from phonenumbers import NumberParseException

st.set_page_config(page_title="Prospect Discovery Engine", layout="wide")

USER_AGENT = "ProspectDiscoveryEngine/24.0 (+https://streamlit.app)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

SECTOR_PROFILES = {
    "Schools": {
        "queries": ["school", "private school", "international school", "college", "academy"],
        "keep_keywords": ["school", "college", "academy", "primary", "high", "secondary", "pre-primary", "waldorf", "montessori", "campus"],
        "reject_keywords": ["driving school", "testing yard", "licence", "license", "traffic department", "parking", "residence", "student residence"],
        "page_paths": ["", "contact", "contact-us", "admissions", "staff", "about", "about-us"],
    },
    "Universities / Colleges": {
        "queries": ["university", "college", "campus", "higher education"],
        "keep_keywords": ["university", "college", "campus", "faculty", "school of"],
        "reject_keywords": ["residence", "parking", "shop"],
        "page_paths": ["", "contact", "contact-us", "admissions", "about", "departments"],
    },
    "General Organizations": {
        "queries": ["organization", "company", "office"],
        "keep_keywords": [],
        "reject_keywords": [],
        "page_paths": ["", "contact", "contact-us", "about", "about-us", "team"],
    },
}

def init_state():
    for k, v in {
        "prospects": None,
        "candidate_key": None,
        "enriched_key": None,
        "diagnostics": {},
        "debug_log": [],
        "last_inputs": None,
    }.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

def log(msg):
    st.session_state.debug_log.append(str(msg))

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
    return s[:maxlen] or "prospects"

def normalize_url(url):
    url = safe_str(url)
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

def get_domain(url):
    try:
        return urlparse(normalize_url(url)).netloc.lower().replace("www.", "")
    except Exception:
        return ""

def text_tokens(s):
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", safe_str(s).lower()).split() if len(t) > 2]

def candidate_key(inputs):
    return hashlib.md5(json.dumps(inputs, sort_keys=True).encode()).hexdigest()

def enrichment_key(cand_key, options):
    return hashlib.md5(json.dumps({"candidate_key": cand_key, **options}, sort_keys=True).encode()).hexdigest()

def get_country_code(country):
    c = safe_str(country).lower()
    mapping = {
        "south africa": "ZA", "nigeria": "NG", "kenya": "KE", "ghana": "GH", "united kingdom": "GB",
        "uk": "GB", "united states": "US", "usa": "US", "canada": "CA", "rwanda": "RW", "uganda": "UG",
        "tanzania": "TZ", "senegal": "SN"
    }
    return mapping.get(c, None)

def extract_emails(text):
    if not text: return []
    # handle obfuscated
    text = re.sub(r"\s*\[at\]\s*|\s+\(at\)\s+|\s+at\s+", "@", text, flags=re.I)
    text = re.sub(r"\s*\[dot\]\s*|\s+\(dot\)\s+|\s+dot\s+", ".", text, flags=re.I)
    emails = re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)
    bad = {"example.com", "email.com", "domain.com"}
    out = []
    for e in emails:
        e = e.strip(".,;:()[]<>").lower()
        if get_domain("https://" + e.split("@")[-1]) not in bad and e not in out:
            out.append(e)
    return out

def extract_phones(text, country):
    cc = get_country_code(country)
    found = []
    if text:
        for match in phonenumbers.PhoneNumberMatcher(text, cc or "US"):
            try:
                num = phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
                if phonenumbers.is_valid_number(match.number) and num not in found:
                    found.append(num)
            except Exception:
                pass
    return found

def fetch(url, timeout=10):
    url = safe_str(url)
    if not url:
        return None, ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400 or "text/html" not in r.headers.get("content-type", "text/html"):
            return r.status_code, ""
        return r.status_code, r.text[:750000]
    except Exception as e:
        return type(e).__name__, ""

def page_text_and_links(url, html):
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = soup.get_text(" ", strip=True)
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        label = a.get_text(" ", strip=True)
        links.append((href, label))
    return title, text, links

def score_website_candidate(url, prospect_name, location_hint="", title="", snippet=""):
    domain = get_domain(url)
    if not domain: return 0
    name_tokens = text_tokens(prospect_name)
    hay = " ".join([domain, safe_str(title).lower(), safe_str(snippet).lower()])
    score = 0
    for t in name_tokens:
        if t in hay:
            score += 12
    if any(k in hay for k in ["school", "college", "academy", "primary", "secondary", "high"]):
        score += 12
    if any(k in domain for k in ["facebook", "instagram", "linkedin", "wikipedia", "yell", "schoolguide", "saschools", "mapcarta", "snupit"]):
        score -= 25
    if any(k in domain for k in ["gov.za", "westerncape.gov", "education.gov"]):
        score -= 10
    if domain.endswith((".co.za", ".org.za", ".ac.za", ".edu")):
        score += 5
    # suspicious generic domains
    if any(k in domain for k in ["google.", "bing.", "duckduckgo.", "tripadvisor", "booking.com"]):
        score -= 50
    return score

def domain_guesses(name, country):
    toks = [t for t in text_tokens(name) if t not in {"school","primary","high","college","academy","the","and","cape","town"}]
    if not toks:
        return []
    bases = ["".join(toks), "-".join(toks)]
    suffixes = [".co.za", ".org.za", ".ac.za"] if safe_str(country).lower()=="south africa" else [".org", ".com"]
    return ["https://www." + b + s for b in bases[:2] for s in suffixes] + ["https://" + b + s for b in bases[:2] for s in suffixes]

def ddg_search(query, max_results=5, timeout=12):
    url = "https://duckduckgo.com/html/?q=" + quote_plus(query)
    status, html = fetch(url, timeout=timeout)
    results = []
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a.result__a")[:max_results]:
        href = a.get("href","")
        title = a.get_text(" ", strip=True)
        if "uddg=" in href:
            try:
                import urllib.parse as up
                href = up.parse_qs(up.urlparse(href).query).get("uddg", [href])[0]
            except Exception:
                pass
        results.append({"url": href, "title": title, "snippet": ""})
    return results

def resolve_website_for_row(row, location_hint, search_level):
    current = normalize_url(row.get("website",""))
    if current:
        return current, row.get("website_source","map/open data") or "map/open data", ""
    name = safe_str(row.get("prospect_name"))
    country = safe_str(row.get("country"))
    candidates = []
    # Cheap domain guesses first
    for gu in domain_guesses(name, country)[:4]:
        status, html = fetch(gu, timeout=5)
        if html:
            title, text, links = page_text_and_links(gu, html)
            score = score_website_candidate(gu, name, location_hint, title, text[:500])
            candidates.append((gu, score, "domain_guess"))
    # Search resolution. Normal is capped; extra thorough uses more queries/results.
    queries = [f'"{name}" official website {location_hint}', f'"{name}" {location_hint} school']
    if search_level == "Extra thorough":
        queries += [f'"{name}" contact', f'"{name}" admissions', f'{name} school website']
    for q in queries[:2 if search_level=="Normal" else 5]:
        for res in ddg_search(q, max_results=4 if search_level=="Normal" else 8, timeout=10):
            u = normalize_url(res["url"])
            if not u: continue
            score = score_website_candidate(u, name, location_hint, res.get("title",""), res.get("snippet",""))
            candidates.append((u, score, "resolved_search"))
    # dedupe by domain
    best_by_domain = {}
    for u, s, m in candidates:
        d = get_domain(u)
        if d and (d not in best_by_domain or s > best_by_domain[d][1]):
            best_by_domain[d] = (u, s, m)
    ranked = sorted(best_by_domain.values(), key=lambda x:x[1], reverse=True)
    cand_str = "; ".join([f"{u} ({s})" for u,s,m in ranked[:5]])
    if ranked and ranked[0][1] >= (35 if search_level=="Normal" else 28):
        return ranked[0][0], ranked[0][2], cand_str
    return "", "not_found", cand_str

def geocode(location):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": location, "format": "jsonv2", "limit": 1, "addressdetails": 1}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=20)
        log(f"Geocode HTTP {r.status_code}: {r.url}")
        js = r.json()
        if not js: return None
        item = js[0]
        return {"lat": float(item["lat"]), "lon": float(item["lon"]), "display_name": item.get("display_name",""), "country": item.get("address",{}).get("country","")}
    except Exception as e:
        log(f"Geocode error: {type(e).__name__}: {e}")
        return None

def nominatim_search(query, limit=50):
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
        name = (x.get("namedetails") or {}).get("name") or x.get("name") or safe_str(x.get("display_name","")).split(",")[0]
        addr = x.get("address") or {}
        extra = x.get("extratags") or {}
        rows.append({
            "prospect_name": name,
            "sector": "Schools",
            "source": "nominatim",
            "address": x.get("display_name",""),
            "city": addr.get("city") or addr.get("town") or addr.get("municipality") or "",
            "country": addr.get("country") or "",
            "latitude": x.get("lat",""),
            "longitude": x.get("lon",""),
            "website": normalize_url(extra.get("website") or extra.get("url") or ""),
            "osm_phone": extra.get("phone") or extra.get("contact:phone") or "",
            "osm_email": extra.get("email") or extra.get("contact:email") or "",
            "website_source": "map/open data" if (extra.get("website") or extra.get("url")) else "",
        })
    return rows

def overpass_search(lat, lon, radius_m, sector, limit):
    profile = SECTOR_PROFILES[sector]
    keys = ['node["amenity"~"school|college|university"]', 'way["amenity"~"school|college|university"]', 'relation["amenity"~"school|college|university"]']
    q = f"""[out:json][timeout:25];({''.join([k + f'(around:{radius_m},{lat},{lon});' for k in keys])});out center tags {limit};"""
    endpoints = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.osm.ch/api/interpreter"]
    for ep in endpoints:
        try:
            r = requests.post(ep, data={"data": q}, headers=HEADERS, timeout=30)
            log(f"Overpass POST {ep}: HTTP {r.status_code}")
            if r.status_code != 200: continue
            data = r.json()
            elems = data.get("elements", [])
            rows = []
            for e in elems:
                tags = e.get("tags") or {}
                name = tags.get("name") or tags.get("official_name") or ""
                if not name: continue
                lat2 = e.get("lat") or (e.get("center") or {}).get("lat")
                lon2 = e.get("lon") or (e.get("center") or {}).get("lon")
                rows.append({
                    "prospect_name": name, "sector": sector, "source": "overpass",
                    "address": ", ".join([safe_str(tags.get(k)) for k in ["addr:housenumber","addr:street","addr:city"] if safe_str(tags.get(k))]),
                    "city": tags.get("addr:city",""), "country": tags.get("addr:country",""),
                    "latitude": lat2, "longitude": lon2,
                    "website": normalize_url(tags.get("website") or tags.get("contact:website") or ""),
                    "osm_phone": tags.get("phone") or tags.get("contact:phone") or "",
                    "osm_email": tags.get("email") or tags.get("contact:email") or "",
                    "website_source": "map/open data" if (tags.get("website") or tags.get("contact:website")) else "",
                })
            log(f"Overpass {ep}: {len(rows)} candidates")
            if rows: return rows[:limit]
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
        if not name: continue
        key = re.sub(r"[^a-z0-9]+","", name.lower())[:40] + "|" + safe_str(r.get("latitude"))[:7] + "|" + safe_str(r.get("longitude"))[:7]
        if key in seen: continue
        seen.add(key)
        out.append(r)
        if len(out) >= max_candidates: break
    return out

def discover_map(location, radius_km, max_candidates, sector):
    geo = geocode(location)
    rows = []
    if geo:
        rows += overpass_search(geo["lat"], geo["lon"], int(radius_km*1000), sector, max_candidates)
    profile = SECTOR_PROFILES[sector]
    # Always supplement with Nominatim because Overpass is inconsistent on Streamlit.
    for qterm in profile["queries"]:
        if len(rows) >= max_candidates: break
        rows += nominatim_search(f"{qterm} in {location}", limit=max_candidates)
    rows = dedupe_rows(rows, sector, max_candidates)
    return rows

def resolve_websites(rows, location_hint, search_level, workers, progress):
    rows = [dict(r) for r in rows]
    todo = [i for i,r in enumerate(rows) if not safe_str(r.get("website"))]
    total = max(1, len(todo))
    def worker(i):
        r = rows[i]
        url, method, cands = resolve_website_for_row(r, location_hint, search_level)
        return i, url, method, cands
    if not todo:
        progress.progress(1.0, text="Website resolution complete")
        return rows
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(worker, i) for i in todo]
        for fut in as_completed(futs):
            try:
                i, url, method, cands = fut.result()
                if url:
                    rows[i]["website"] = url
                rows[i]["website_source"] = method
                rows[i]["website_candidates"] = cands
            except Exception as e:
                pass
            done += 1
            progress.progress(done/total, text=f"Finding websites: {done}/{total}")
    return rows

def scrape_one(row, search_level, find_more_contacts):
    r = dict(row)
    country = r.get("country","")
    site = normalize_url(r.get("website",""))
    if not site:
        r.update({"enrichment_status":"no_website"})
        return r
    profile = SECTOR_PROFILES.get(r.get("sector","Schools"), SECTOR_PROFILES["Schools"])
    paths = profile["page_paths"][:3 if search_level=="Normal" else len(profile["page_paths"])]
    emails, phones, pages = [], [], []
    for path in paths:
        url = site if not path else urljoin(site+"/", path)
        status, html = fetch(url, timeout=8 if search_level=="Normal" else 12)
        if not html: continue
        pages.append(url)
        title, text, links = page_text_and_links(url, html)
        for e in extract_emails(text):
            if e not in emails: emails.append(e)
        for p in extract_phones(text, country):
            if p not in phones: phones.append(p)
        # In extra thorough mode, follow contact-like links on homepage
        if search_level == "Extra thorough" and path == "":
            contact_links = []
            for href,label in links:
                lab = (label + " " + href).lower()
                if any(k in lab for k in ["contact", "admission", "staff", "office", "reception"]):
                    contact_links.append(href)
            for href in contact_links[:5]:
                st2, h2 = fetch(href, timeout=10)
                if not h2: continue
                pages.append(href)
                _, txt2, _ = page_text_and_links(href, h2)
                for e in extract_emails(txt2):
                    if e not in emails: emails.append(e)
                for p in extract_phones(txt2, country):
                    if p not in phones: phones.append(p)
    r["visible_emails"] = "; ".join(emails)
    generic = [e for e in emails if re.match(r"^(info|admin|admissions|office|reception|enrol|enrolments|contact)@", e)]
    r["generic_emails"] = "; ".join(generic)
    r["best_email"] = (generic[0] if generic else (emails[0] if emails else safe_str(r.get("osm_email"))))
    r["email_source"] = "website_generic" if generic else ("website_visible" if emails else ("osm" if safe_str(r.get("osm_email")) else ""))
    r["website_phone"] = "; ".join(phones[:3])
    osm_phone = safe_str(r.get("osm_phone"))
    r["best_phone"] = phones[0] if phones else osm_phone
    r["phone_source"] = "website" if phones else ("osm" if osm_phone else "")
    r["source_pages"] = "; ".join(dict.fromkeys(pages))
    r["enrichment_status"] = "scraped" if pages else "scrape_failed"
    return r

def enrich_rows(rows, search_level, find_more_contacts, workers, progress):
    total = max(1, len(rows))
    out, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(scrape_one, r, search_level, find_more_contacts) for r in rows]
        for fut in as_completed(futs):
            try:
                out.append(fut.result())
            except Exception:
                pass
            done += 1
            progress.progress(done/total, text=f"Enriching contact details: {done}/{total}")
    # preserve original order approximately
    by_name = {safe_str(r.get("prospect_name")): r for r in out}
    ordered = [by_name.get(safe_str(r.get("prospect_name")), r) for r in rows]
    return ordered

def export_bytes(df, excel=False):
    if not excel:
        return df.to_csv(index=False).encode("utf-8")
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Prospects")
    return bio.getvalue()

# UI
st.title("Prospect Discovery Engine")
st.caption("Find prospects, resolve official websites, and enrich contact details.")

with st.sidebar:
    st.header("Search")
    sector = st.selectbox("Sector", list(SECTOR_PROFILES.keys()), index=0)
    location = st.text_input("Location", "Cape Town, Western Cape, South Africa")
    radius_km = st.slider("Search radius (km)", 1, 100, 10)
    max_candidates = st.slider("Maximum prospects", 10, 250, 50, step=10)
    st.divider()
    search_level = st.radio("Search depth", ["Normal", "Extra thorough"], index=0, help="Normal is faster. Extra thorough tries more website/contact sources.")
    find_more_contacts = st.checkbox("Find more contact details when missing", value=False)
    speed_label = st.select_slider("Processing speed", options=["Safe", "Balanced", "Fast"], value="Balanced")
    workers = {"Safe":2, "Balanced":5, "Fast":10}[speed_label]
    st.divider()
    clear = st.button("Clear results")

if clear:
    for k in ["prospects","candidate_key","enriched_key","diagnostics","debug_log","last_inputs"]:
        st.session_state[k] = [] if k=="debug_log" else None
    st.rerun()

inputs = {"sector":sector, "location":location.strip(), "radius_km":radius_km, "max_candidates":max_candidates}
ckey = candidate_key(inputs)
eopts = {"search_level":search_level, "find_more_contacts":find_more_contacts, "speed":speed_label, "workers":workers}
ekey = enrichment_key(ckey, eopts)

run = st.button("Find prospects", type="primary", use_container_width=True)

if run:
    st.session_state.debug_log = []
    t0 = time.time()
    # Stacked progress bars, always visible and independent
    p1 = st.progress(0, text="Step 1 of 3: Finding prospects")
    p2 = st.progress(0, text="Step 2 of 3: Finding official websites")
    p3 = st.progress(0, text="Step 3 of 3: Enriching contact details")
    if st.session_state.candidate_key == ckey and st.session_state.prospects is not None:
        rows = st.session_state.prospects
        st.info(f"Using cached prospect list: {len(rows)} prospects. Rechecking websites/contact details only.")
        p1.progress(1.0, text=f"Step 1 complete: using cached prospect list ({len(rows)} prospects)")
        discovery_seconds = 0.0
    else:
        st.info("Starting new prospect search…")
        ts = time.time()
        rows = discover_map(location, radius_km, max_candidates, sector)
        discovery_seconds = time.time() - ts
        p1.progress(1.0, text=f"Step 1 complete: found {len(rows)} prospects")
        st.session_state.candidate_key = ckey
    ts = time.time()
    rows = resolve_websites(rows, location, search_level, workers, p2)
    website_seconds = time.time() - ts
    ts = time.time()
    rows = enrich_rows(rows, search_level, find_more_contacts, workers, p3)
    enrichment_seconds = time.time() - ts
    st.session_state.prospects = rows
    st.session_state.enriched_key = ekey
    st.session_state.diagnostics = {
        "discovery_seconds": round(discovery_seconds,2),
        "website_resolution_seconds": round(website_seconds,2),
        "enrichment_seconds": round(enrichment_seconds,2),
        "total_seconds": round(time.time()-t0,2),
    }
    st.success(f"Done: {len(rows)} prospects ready.")

if st.session_state.prospects:
    df = pd.DataFrame(st.session_state.prospects)
    # user-facing table columns
    show_cols = [c for c in [
        "prospect_name","sector","city","country","website","best_email","best_phone",
        "enrichment_status","website_source","website_candidates","email_source","phone_source","source_pages"
    ] if c in df.columns]
    st.subheader("Prospects")
    st.dataframe(df[show_cols], use_container_width=True, hide_index=True)
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Prospects", len(df))
    c2.metric("Websites", int(df["website"].fillna("").astype(str).str.len().gt(0).sum()) if "website" in df else 0)
    c3.metric("Emails", int(df["best_email"].fillna("").astype(str).str.len().gt(0).sum()) if "best_email" in df else 0)
    c4.metric("Phones", int(df["best_phone"].fillna("").astype(str).str.len().gt(0).sum()) if "best_phone" in df else 0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    fname_base = f"prospect_discovery_{slugify(sector)}_{slugify(location)}_{stamp}"
    st.download_button("Download CSV", export_bytes(df, excel=False), file_name=f"{fname_base}.csv", mime="text/csv")
    st.download_button("Download Excel", export_bytes(df, excel=True), file_name=f"{fname_base}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with st.expander("Diagnostics"):
        st.json(st.session_state.diagnostics or {})
        st.text("\n".join(st.session_state.debug_log[-200:]))
else:
    st.info("Choose a sector and location, then click **Find prospects**.")
