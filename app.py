
import re
import time
import json
import math
import html
from urllib.parse import urlparse, urljoin, quote_plus
from collections import defaultdict, Counter

import pandas as pd
import requests
from bs4 import BeautifulSoup
import streamlit as st

APP_VERSION = "v12"
UA = "Mozilla/5.0 (compatible; SchoolDiscoveryEngine/12.0; +https://streamlit.app)"
TIMEOUT = 18

EMAIL_RE = re.compile(r"""(?i)\b[A-Z0-9._%+\-]+(?:\s?\[at\]\s?|\s?\(at\)\s?|\s+at\s+|@)[A-Z0-9.\-]+(?:\s?\[dot\]\s?|\s?\(dot\)\s?|\s+dot\s+|\.)[A-Z]{2,}\b""")
NORMAL_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b")
BAD_EXT = (".jpg",".jpeg",".png",".gif",".svg",".webp",".pdf",".doc",".docx",".xls",".xlsx",".zip",".mp4",".mp3")
GENERIC_PREFIXES = ("info","office","admin","admissions","admission","enquiries","enquiry","contact","hello","reception","principal","registrar","school","secretary")
SCHOOL_WORDS = ("school","college","academy","university","campus","primary","secondary","preparatory","prep","high school","international","lycée","lycee")
LISTICLE_WORDS = ("best schools","top schools","directory","rankings","ranking","list of schools","schoolguide","school guide","reviews","wikipedia","facebook","linkedin","instagram","x.com","twitter","youtube","news24","briefly","safaribookings")
CONTACT_PATH_HINTS = ("contact", "admission", "admissions", "staff", "team", "leadership", "about", "support", "counselling", "counseling", "learning-support", "inclusive", "inclusion", "sen", "senco", "academics", "student-support")
ROLE_KEYWORDS = {
    "role_principal_head": ["principal", "head of school", "headmaster", "headmistress", "executive head", "school director", "director of school"],
    "role_admissions": ["admissions", "admission", "registrar", "enrolment", "enrollment", "enrolments"],
    "role_counselor": ["counsellor", "counselor", "college counselor", "university guidance", "career guidance", "guidance counselor", "university counsellor"],
    "role_learning_support": ["learning support", "inclusive education", "inclusion", "sen", "senco", "special needs", "additional needs", "educational support"],
    "role_innovation_ai": ["innovation", "technology integration", "digital learning", "ai", "artificial intelligence", "edtech", "ict"],
}
FIT_KEYWORDS = {
    "international_curriculum": ["ib", "international baccalaureate", "cambridge", "a level", "a-level", "igcse", "international curriculum", "ap curriculum"],
    "college_university_guidance": ["university guidance", "college counselling", "college counseling", "career guidance", "university applications", "tertiary guidance"],
    "learning_support": ROLE_KEYWORDS["role_learning_support"],
    "ai_innovation": ROLE_KEYWORDS["role_innovation_ai"],
    "parent_education": ["parent workshop", "parent education", "parent information evening", "parent seminar", "parent talk"],
}

def safe_get(url, timeout=TIMEOUT):
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}, timeout=timeout, allow_redirects=True)
        return r
    except Exception as e:
        return e

def normalize_url(url):
    if not url:
        return ""
    url = str(url).strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    if not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")

def domain_of(url):
    try:
        return urlparse(normalize_url(url)).netloc.lower().replace("www.","")
    except Exception:
        return ""

def clean_text(s):
    if not s:
        return ""
    s = html.unescape(str(s))
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def normalize_email(raw):
    if not raw:
        return ""
    e = raw.strip().lower()
    e = re.sub(r"\s*(\[at\]|\(at\)|\sat\s)\s*", "@", e, flags=re.I)
    e = re.sub(r"\s*(\[dot\]|\(dot\)|\sdot\s)\s*", ".", e, flags=re.I)
    e = re.sub(r"[<>\(\)\[\],;:]+$", "", e)
    return e

def extract_emails(text):
    if not text:
        return []
    candidates = EMAIL_RE.findall(text)
    candidates += NORMAL_EMAIL_RE.findall(text)
    emails = []
    for c in candidates:
        e = normalize_email(c)
        if NORMAL_EMAIL_RE.fullmatch(e or ""):
            if not any(bad in e for bad in ["example.com", "sentry.io"]):
                emails.append(e)
    return sorted(set(emails))

def infer_patterns(emails):
    personal = []
    for e in emails:
        local, _, dom = e.partition("@")
        if local in GENERIC_PREFIXES:
            continue
        if "." in local and all(part.isalpha() for part in local.split(".")[:2]):
            personal.append(e)
    if not personal:
        return "", ""
    patterns = []
    for e in personal:
        local = e.split("@")[0]
        if re.match(r"^[a-z]+\.[a-z]+$", local):
            patterns.append("firstname.lastname")
        elif re.match(r"^[a-z][a-z]+$", local):
            patterns.append("firstname/lastname")
        elif re.match(r"^[a-z][a-z]+[0-9]*$", local):
            patterns.append("possible name-based")
    pattern = Counter(patterns).most_common(1)[0][0] if patterns else "name-based"
    return pattern, "; ".join(personal[:8])

def score_signals(text):
    lower = (text or "").lower()
    out = {}
    for col, kws in FIT_KEYWORDS.items():
        hits = sorted({kw for kw in kws if kw.lower() in lower})
        out[col] = "; ".join(hits) if hits else ""
    for col, kws in ROLE_KEYWORDS.items():
        hits = sorted({kw for kw in kws if kw.lower() in lower})
        out[col] = "; ".join(hits) if hits else ""
    fit = 0
    fit += 2 if out["international_curriculum"] else 0
    fit += 2 if out["college_university_guidance"] else 0
    fit += 3 if out["learning_support"] else 0
    fit += 1 if out["ai_innovation"] else 0
    fit += 1 if out["parent_education"] else 0
    return out, fit

def is_likely_school_domain(url, name=""):
    d = domain_of(url)
    if not d:
        return False
    if any(x in d for x in ["facebook.", "instagram.", "linkedin.", "youtube.", "wikipedia.", "google.", "bing.", "duckduckgo.", "mapcarta.", "tripadvisor."]):
        return False
    if any(w.replace(" ","") in d.replace("-","") for w in ["schools", "school", "college", "academy", "university", "campus", "lycee", "lycée"]):
        return True
    n = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    dclean = re.sub(r"[^a-z0-9]+", "", d.split(".")[0])
    return bool(n and len(n) > 5 and (dclean in n or n[:8] in dclean or dclean[:8] in n))

def candidate_from_osm_element(el, source):
    tags = el.get("tags", {}) or {}
    name = tags.get("name") or tags.get("official_name") or tags.get("operator") or ""
    website = tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
    email = tags.get("email") or tags.get("contact:email") or ""
    phone = tags.get("phone") or tags.get("contact:phone") or ""
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    return {
        "school_name": clean_text(name),
        "website": normalize_url(website),
        "domain": domain_of(website),
        "source": source,
        "source_detail": "osm",
        "status": "candidate",
        "osm_email": email,
        "phone": phone,
        "lat": lat,
        "lon": lon,
    }

def geocode(place, debug):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": place, "format": "json", "limit": 1, "addressdetails": 1}
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=15)
        debug.append(f"Geocode HTTP {r.status_code}: {r.url}")
        if r.ok and r.json():
            j = r.json()[0]
            return float(j["lat"]), float(j["lon"]), j.get("display_name", place)
    except Exception as e:
        debug.append(f"Geocode error: {type(e).__name__}: {e}")
    return None, None, ""

def overpass_query(lat, lon, radius_km, debug):
    # Use simple radius query instead of fragile area query.
    radius_m = int(radius_km * 1000)
    query = f"""
    [out:json][timeout:25];
    (
      node["amenity"~"school|college|university"](around:{radius_m},{lat},{lon});
      way["amenity"~"school|college|university"](around:{radius_m},{lat},{lon});
      relation["amenity"~"school|college|university"](around:{radius_m},{lat},{lon});
      node["office"="educational_institution"](around:{radius_m},{lat},{lon});
      way["office"="educational_institution"](around:{radius_m},{lat},{lon});
      relation["office"="educational_institution"](around:{radius_m},{lat},{lon});
    );
    out center tags 300;
    """
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.osm.ch/api/interpreter",
    ]
    rows = []
    for ep in endpoints:
        try:
            r = requests.post(ep, data={"data": query}, headers={"User-Agent": UA}, timeout=30)
            debug.append(f"Overpass POST {ep}: HTTP {r.status_code}")
            if r.ok:
                js = r.json()
                elems = js.get("elements", [])
                debug.append(f"Overpass {ep}: {len(elems)} elements")
                rows.extend([candidate_from_osm_element(e, "map_overpass") for e in elems])
                if rows:
                    break
            else:
                debug.append(f"Overpass body preview: {r.text[:180]}")
        except Exception as e:
            debug.append(f"Overpass {ep}: {type(e).__name__}: {e}")
    return rows

def nominatim_search(query, limit, debug):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": limit, "addressdetails": 1, "extratags": 1, "namedetails": 1}
    out = []
    try:
        r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=18)
        debug.append(f"Nominatim '{query}': HTTP {r.status_code}")
        if r.ok:
            for j in r.json():
                name = j.get("namedetails", {}).get("name") or j.get("name") or j.get("display_name","").split(",")[0]
                extr = j.get("extratags", {}) or {}
                website = extr.get("website") or extr.get("contact:website") or extr.get("url") or ""
                email = extr.get("email") or extr.get("contact:email") or ""
                typ = j.get("type","")
                klass = j.get("class","")
                out.append({
                    "school_name": clean_text(name),
                    "website": normalize_url(website),
                    "domain": domain_of(website),
                    "source": "map_nominatim",
                    "source_detail": f"{klass}/{typ}",
                    "status": "candidate",
                    "osm_email": email,
                    "phone": extr.get("phone") or extr.get("contact:phone") or "",
                    "lat": j.get("lat"),
                    "lon": j.get("lon"),
                })
    except Exception as e:
        debug.append(f"Nominatim error '{query}': {type(e).__name__}: {e}")
    return out

def discover_by_map(place, radius_km, max_results):
    debug = []
    lat, lon, display = geocode(place, debug)
    if not lat:
        return [], debug
    debug.append(f"Geocoded to: {display} ({lat:.5f},{lon:.5f})")
    candidates = overpass_query(lat, lon, radius_km, debug)
    # Nominatim fallback/supplement. Not only if Overpass fails; this fills websites.
    variants = [
        f"school in {place}",
        f"private school in {place}",
        f"international school in {place}",
        f"college in {place}",
        f"university in {place}",
        f"academy in {place}",
    ]
    for q in variants:
        candidates.extend(nominatim_search(q, max(10, max_results//3), debug))
        time.sleep(1.0)  # respect Nominatim
    return dedupe_candidates(candidates)[:max_results], debug

def discover_by_name(names, location, max_results):
    debug=[]
    rows=[]
    for name in [x.strip() for x in names.splitlines() if x.strip()]:
        q = f"{name} {location}".strip()
        rows.extend(nominatim_search(q, max_results, debug))
        # Keep explicit placeholder even when no geocoder result
        if not rows or not any(r["school_name"].lower()==name.lower() or name.lower() in r["school_name"].lower() for r in rows):
            rows.append({"school_name": name, "website": "", "domain": "", "source": "school_name_manual", "source_detail": q, "status": "needs website", "osm_email": "", "phone": "", "lat": "", "lon": ""})
        time.sleep(1.0)
    return dedupe_candidates(rows)[:max_results], debug

def discover_by_urls(urls):
    debug=[]
    rows=[]
    for u in [x.strip() for x in urls.splitlines() if x.strip()]:
        nu = normalize_url(u)
        rows.append({"school_name": "", "website": nu, "domain": domain_of(nu), "source": "school_url", "source_detail": "manual_url", "status": "candidate", "osm_email": "", "phone": "", "lat": "", "lon": ""})
    return rows, debug

def dedupe_candidates(rows):
    seen=set(); out=[]
    for r in rows:
        key = r.get("domain") or re.sub(r"[^a-z0-9]+","", (r.get("school_name") or "").lower())[:40]
        if not key or key in seen:
            continue
        seen.add(key); out.append(r)
    return out

def find_internal_links(base_url, soup):
    base_domain = domain_of(base_url)
    links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        u = normalize_url(urljoin(base_url, href))
        if not u or domain_of(u) != base_domain:
            continue
        path = (urlparse(u).path or "").lower()
        text = clean_text(a.get_text(" ")).lower()
        if any(h in path or h in text for h in CONTACT_PATH_HINTS):
            if not u.lower().endswith(BAD_EXT):
                links.append(u)
    # Prioritize contact/admissions/staff pages
    def priority(u):
        s = u.lower()
        score = 0
        for i, h in enumerate(CONTACT_PATH_HINTS):
            if h in s:
                score += (len(CONTACT_PATH_HINTS)-i)
        return -score
    return sorted(set(links), key=priority)[:10]

def extract_possible_school_urls_from_page(url):
    r = safe_get(url)
    if isinstance(r, Exception) or not getattr(r, "ok", False):
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a.get("href")
        u = normalize_url(urljoin(url, href))
        if u and is_likely_school_domain(u, clean_text(a.get_text(" "))):
            out.append(u)
    return sorted(set(out))

def scrape_school(row, max_pages=8):
    row = dict(row)
    website = normalize_url(row.get("website",""))
    all_text = ""
    emails = set()
    pages = []
    source_pages = []
    status_notes = []

    if not website:
        # Keep candidate; do not drop it.
        row.update({
            "website": "", "domain": "", "status": "no website found",
            "visible_emails": row.get("osm_email",""),
            "generic_emails": row.get("osm_email","") if (row.get("osm_email","").split("@")[0].lower() in GENERIC_PREFIXES if row.get("osm_email","") else False) else "",
            "email_pattern_inferred": "", "personal_email_examples": "",
            "pages_scraped": "", "source_pages": "",
            "fit_score": 0, "contact_confidence": 10 if row.get("osm_email") else 0,
            "notes": "Candidate retained but no website was available from map/name lookup."
        })
        signals, fit = score_signals(row.get("school_name",""))
        row.update(signals)
        return row

    row["website"] = website
    row["domain"] = domain_of(website)
    r = safe_get(website)
    if isinstance(r, Exception):
        row["status"] = "homepage error"
        row["notes"] = f"{type(r).__name__}: {r}"
        row["contact_confidence"] = 0
        row["fit_score"] = 0
        return row
    if not getattr(r, "ok", False):
        row["status"] = "homepage HTTP error"
        row["notes"] = f"HTTP {getattr(r,'status_code','')}"
        row["contact_confidence"] = 0
        row["fit_score"] = 0
        return row

    soup = BeautifulSoup(r.text, "html.parser")
    title = clean_text(soup.title.get_text(" ")) if soup.title else ""
    if not row.get("school_name"):
        row["school_name"] = title.split("|")[0].split("-")[0].strip() or row["domain"]

    homepage_text = clean_text(soup.get_text(" "))
    all_text += " " + homepage_text
    emails.update(extract_emails(r.text + " " + homepage_text))
    pages.append(website)

    links = find_internal_links(website, soup)
    for link in links[:max_pages-1]:
        rr = safe_get(link, timeout=14)
        if isinstance(rr, Exception) or not getattr(rr, "ok", False):
            continue
        try:
            ss = BeautifulSoup(rr.text, "html.parser")
            txt = clean_text(ss.get_text(" "))
            all_text += " " + txt
            emails.update(extract_emails(rr.text + " " + txt))
            pages.append(link)
        except Exception:
            pass

    # If this URL is a source/list page, extract school sites and mark accordingly instead of pretending it is one school.
    if not is_likely_school_domain(website, row.get("school_name","")) and any(w in (title+" "+homepage_text).lower() for w in LISTICLE_WORDS):
        school_urls = extract_possible_school_urls_from_page(website)
        row["status"] = "source/list page"
        row["notes"] = f"Looks like a source/list page. Extracted {len(school_urls)} possible school URLs."
        row["source_pages"] = "; ".join(school_urls[:20])
    else:
        row["status"] = "scraped"

    if row.get("osm_email"):
        emails.add(normalize_email(row.get("osm_email")))

    emails = sorted({e for e in emails if e})
    generic = sorted([e for e in emails if e.split("@")[0].lower() in GENERIC_PREFIXES])
    pattern, personal = infer_patterns(emails)
    signals, fit = score_signals(all_text + " " + row.get("school_name",""))

    confidence = 0
    if emails: confidence += 30
    if generic: confidence += 15
    if personal: confidence += 20
    if len(pages) >= 3: confidence += 10
    if any(row.get(k) for k in ["role_principal_head", "role_admissions", "role_counselor", "role_learning_support"]): confidence += 15
    confidence = min(100, confidence)

    row.update(signals)
    row.update({
        "visible_emails": "; ".join(emails),
        "generic_emails": "; ".join(generic),
        "email_pattern_inferred": pattern,
        "personal_email_examples": personal,
        "pages_scraped": "; ".join(pages),
        "source_pages": row.get("source_pages",""),
        "fit_score": fit,
        "contact_confidence": confidence,
        "notes": row.get("notes","") or f"Scraped {len(pages)} pages; found {len(emails)} visible emails."
    })
    return row

def enrich_candidates(candidates, max_pages, progress=None):
    rows=[]
    total=len(candidates)
    for i, c in enumerate(candidates):
        if progress:
            progress.progress((i+1)/max(total,1), text=f"Scraping {i+1}/{total}: {c.get('school_name') or c.get('website')}")
        rows.append(scrape_school(c, max_pages=max_pages))
    return rows

def dataframe_downloads(df, prefix):
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV", csv, f"{prefix}.csv", "text/csv")
    try:
        from io import BytesIO
        bio = BytesIO()
        with pd.ExcelWriter(bio, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="schools")
        st.download_button("Download Excel", bio.getvalue(), f"{prefix}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception:
        pass

st.set_page_config(page_title="School Discovery Engine", layout="wide")
st.title("School Discovery Engine")
st.caption(f"{APP_VERSION} — three discovery modes, candidate retention, contact scraping, and Airtable-ready export")

with st.sidebar:
    st.header("Scraping settings")
    max_pages = st.slider("Max pages to scrape per school", 1, 12, 8)
    st.caption("Higher values find more contacts but run slower on Streamlit Cloud.")

mode = st.radio("Choose one discovery mode", ["Map / geolocation", "School name", "School URL"], horizontal=True)

candidates=[]
debug=[]
run=False

if mode == "Map / geolocation":
    col1, col2, col3 = st.columns([2,1,1])
    with col1:
        place = st.text_input("City / metro / country", "Cape Town, Western Cape, South Africa")
    with col2:
        radius = st.number_input("Radius km", min_value=1, max_value=300, value=100)
    with col3:
        max_results = st.number_input("Max candidates", min_value=10, max_value=500, value=150)
    run = st.button("Find and scrape schools", type="primary")
    if run:
        candidates, debug = discover_by_map(place, radius, int(max_results))

elif mode == "School name":
    names = st.text_area("One school name per line", "The Cape Town French School\nBishops Diocesan College\nHerschel Girls School")
    location = st.text_input("Optional location context", "Cape Town, South Africa")
    max_results = st.number_input("Max candidates per name", min_value=1, max_value=20, value=5)
    run = st.button("Find and scrape by name", type="primary")
    if run:
        candidates, debug = discover_by_name(names, location, int(max_results))

else:
    urls = st.text_area("One school URL per line", "https://www.lfcaire.co.za/\nhttps://www.bishops.org.za/")
    run = st.button("Scrape URLs", type="primary")
    if run:
        candidates, debug = discover_by_urls(urls)

if run:
    with st.expander("Discovery debug / diagnostics", expanded=False):
        st.write("\n".join(debug) if debug else "No debug messages.")

    st.subheader("Candidates")
    st.write(f"Candidates found: **{len(candidates)}**")
    if candidates:
        cand_df = pd.DataFrame(candidates)
        st.dataframe(cand_df, use_container_width=True)
        dataframe_downloads(cand_df, "candidate_schools_raw")

        st.subheader("Scraped / enriched results")
        prog = st.progress(0)
        rows = enrich_candidates(candidates, max_pages=max_pages, progress=prog)
        prog.empty()
        df = pd.DataFrame(rows)

        # Ensure stable columns
        preferred = ["school_name","website","domain","source","source_detail","status","visible_emails","generic_emails","email_pattern_inferred","personal_email_examples","fit_score","contact_confidence","notes","pages_scraped","source_pages","osm_email","phone","lat","lon"] + list(FIT_KEYWORDS.keys()) + list(ROLE_KEYWORDS.keys())
        for c in preferred:
            if c not in df.columns: df[c] = ""
        df = df[preferred + [c for c in df.columns if c not in preferred]]

        st.write(f"Export rows: **{len(df)}**. This should match candidate count unless you manually remove rows.")
        st.dataframe(df, use_container_width=True)
        dataframe_downloads(df, "school_discovery_enriched_results")

        no_site = (df["status"] == "no website found").sum()
        scraped = (df["status"] == "scraped").sum()
        emails = df["visible_emails"].fillna("").astype(str).str.len().gt(0).sum()
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Candidates", len(df))
        c2.metric("Scraped sites", int(scraped))
        c3.metric("No website retained", int(no_site))
        c4.metric("Rows with emails", int(emails))
    else:
        st.warning("No candidates found. Try School name or School URL mode, or broaden the location/radius.")
