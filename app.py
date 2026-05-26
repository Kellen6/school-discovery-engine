import re, io, time, hashlib, urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
import phonenumbers
from phonenumbers import NumberParseException
try:
    from duckduckgo_search import DDGS
except Exception:
    DDGS = None

st.set_page_config(page_title="Prospect Discovery Engine", layout="wide")

UA = "ProspectDiscoveryEngine/21.0 (contact enrichment; respectful requests)"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

SECTOR_PROFILES = {
    "School": {
        "nominatim_terms": ["school", "private school", "international school", "college", "academy", "university"],
        "overpass_tags": ['node["amenity"~"school|college|university"]', 'way["amenity"~"school|college|university"]', 'relation["amenity"~"school|college|university"]'],
        "page_targets_fast": ["contact"],
        "page_targets_standard": ["contact", "admissions", "about", "staff"],
        "page_targets_deep": ["contact", "admissions", "about", "staff", "leadership", "office", "reception", "campus", "support", "directory"],
        "role_terms": ["principal", "head of school", "director", "admissions", "counsellor", "counselor", "learning support", "SEN", "inclusion"],
    },
    "University / Higher Ed": {
        "nominatim_terms": ["university", "college", "campus", "higher education"],
        "overpass_tags": ['node["amenity"~"university|college"]', 'way["amenity"~"university|college"]', 'relation["amenity"~"university|college"]'],
        "page_targets_fast": ["contact"],
        "page_targets_standard": ["contact", "admissions", "faculty", "about"],
        "page_targets_deep": ["contact", "admissions", "faculty", "leadership", "departments", "student-support", "directory"],
        "role_terms": ["dean", "admissions", "registrar", "student support", "faculty", "director"],
    },
    "Generic organization": {
        "nominatim_terms": ["organization", "office", "company", "ngo"],
        "overpass_tags": ['node["office"]', 'way["office"]', 'relation["office"]'],
        "page_targets_fast": ["contact"],
        "page_targets_standard": ["contact", "about", "team"],
        "page_targets_deep": ["contact", "about", "team", "leadership", "staff", "directory"],
        "role_terms": ["director", "manager", "founder", "team", "contact"],
    },
}

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
OBFUSCATED_PATTERNS = [
    (re.compile(r"([A-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)| at )\s*([A-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)| dot )\s*([A-Z]{2,})", re.I), r"\1@\2.\3")
]


def init_state():
    defaults = {
        "map_cache_key": None,
        "map_candidates": None,
        "last_timing": {},
        "raw_df": None,
        "enriched_df": None,
        "debug_log": [],
        "timing": {},
        "last_mode_label": "",
        "first_map_run_done": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

def log(msg):
    st.session_state.debug_log.append(str(msg))

def clean_url(url):
    if not url: return ""
    url = str(url).strip()
    if not url: return ""
    if url.startswith("//"): url = "https:" + url
    if not url.startswith(("http://", "https://")): url = "https://" + url
    return url.split("#")[0]

def domain_of(url):
    try: return urllib.parse.urlparse(clean_url(url)).netloc.lower().replace("www.", "")
    except Exception: return ""

def safe_get(url, timeout=8):
    try:
        r = requests.get(clean_url(url), headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code < 400 and "text" in r.headers.get("Content-Type", "text/html"):
            return r.text, r.url, r.status_code
        return "", r.url, r.status_code
    except Exception:
        return "", url, None

def geocode(location):
    q = urllib.parse.urlencode({"q": location, "format": "jsonv2", "limit": 1, "addressdetails": 1})
    url = f"https://nominatim.openstreetmap.org/search?{q}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        log(f"Geocode HTTP {r.status_code}: {url}")
        data = r.json() if r.status_code == 200 else []
        if data:
            d = data[0]
            log(f"Geocoded to: {d.get('display_name')} ({d.get('lat')},{d.get('lon')})")
            return float(d["lat"]), float(d["lon"]), d.get("display_name", location), d.get("address", {})
    except Exception as e:
        log(f"Geocode error: {type(e).__name__}: {e}")
    return None, None, location, {}

def country_code_from_address(address):
    cc = (address or {}).get("country_code", "").upper()
    return cc or None

def overpass_query(lat, lon, radius_km, profile):
    tags = ";".join([f'{t}(around:{int(radius_km*1000)},{lat},{lon})' for t in profile["overpass_tags"]])
    query = f"[out:json][timeout:20];({tags};);out center tags {200};"
    endpoints = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.osm.ch/api/interpreter"]
    rows=[]
    for ep in endpoints:
        try:
            r = requests.post(ep, data={"data": query}, headers=HEADERS, timeout=24)
            log(f"Overpass POST {ep}: HTTP {r.status_code}")
            if r.status_code != 200: continue
            js = r.json(); elems = js.get("elements", [])
            log(f"Overpass {ep}: {len(elems)} elements")
            for el in elems:
                tags = el.get("tags", {}) or {}
                name = tags.get("name") or tags.get("official_name") or ""
                if not name: continue
                center = el.get("center", {})
                rows.append({
                    "school_name": name,
                    "website": tags.get("website") or tags.get("contact:website") or "",
                    "osm_phone": tags.get("phone") or tags.get("contact:phone") or "",
                    "phone": tags.get("phone") or tags.get("contact:phone") or "",
                    "address": ", ".join([v for k,v in tags.items() if k.startswith("addr:") and isinstance(v,str)])[:500],
                    "lat": el.get("lat") or center.get("lat") or "",
                    "lon": el.get("lon") or center.get("lon") or "",
                    "source": "overpass",
                })
            if rows: break
        except Exception as e:
            log(f"Overpass {ep}: {type(e).__name__}: {e}")
    return rows

def nominatim_search(location, profile, limit_each=20):
    rows=[]
    for term in profile["nominatim_terms"]:
        q = f"{term} in {location}"
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": q, "format":"jsonv2", "limit": limit_each, "addressdetails":1, "extratags":1})
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            log(f"Nominatim '{q}': HTTP {r.status_code}")
            if r.status_code != 200: continue
            for d in r.json() or []:
                if not isinstance(d, dict): continue
                name = d.get("name") or (d.get("display_name", "").split(",")[0])
                extra = d.get("extratags") or {}
                addr = d.get("address") or {}
                rows.append({
                    "school_name": name,
                    "website": extra.get("website") or "",
                    "osm_phone": extra.get("phone") or extra.get("contact:phone") or "",
                    "phone": extra.get("phone") or extra.get("contact:phone") or "",
                    "address": d.get("display_name", ""),
                    "lat": d.get("lat", ""),
                    "lon": d.get("lon", ""),
                    "source": "nominatim",
                })
        except Exception as e:
            log(f"Nominatim error '{q}': {type(e).__name__}: {e}")
    return rows

def dedupe_rows(rows, max_candidates=100):
    seen=set(); out=[]
    for r in rows:
        name = (r.get("school_name") or "").strip()
        if not name: continue
        key = re.sub(r"\W+", "", name.lower())[:60]
        if key in seen: continue
        seen.add(key); out.append(r)
        if len(out) >= max_candidates: break
    return out

def discover_by_map(location, radius_km, max_candidates, sector):
    t=time.time(); profile=SECTOR_PROFILES[sector]
    lat, lon, display, address = geocode(location)
    cc=country_code_from_address(address)
    rows=[]
    if lat and lon:
        rows += overpass_query(lat, lon, radius_km, profile)
    if len(rows) < max_candidates:
        rows += nominatim_search(location, profile, limit_each=25)
    rows=dedupe_rows(rows, max_candidates)
    for r in rows:
        r["country_code"] = cc or ""
        r["location_query"] = location
        r["sector"] = sector
    st.session_state.timing["discovery_seconds"] = round(time.time()-t, 2)
    return rows

def extract_emails(text):
    if not text: return []
    t=text
    for pat, repl in OBFUSCATED_PATTERNS:
        t = pat.sub(repl, t)
    emails=sorted(set(e.strip(".,;:()[]<>").lower() for e in EMAIL_RE.findall(t)))
    bad=("example.com", "domain.com", "email.com")
    return [e for e in emails if not any(b in e for b in bad)]

def extract_phones(text, region=None):
    if not text: return []
    candidates=[]
    # broad chunks with + or leading 0 and separators
    for m in re.finditer(r"(?:\+\d{1,3}[\s().-]*)?(?:\(?\d{2,4}\)?[\s().-]*)?\d{3}[\s().-]*\d{3,4}(?:[\s().-]*\d{0,4})?", text):
        s=m.group(0).strip()
        digits=re.sub(r"\D", "", s)
        if len(digits) < 8 or len(digits) > 15: continue
        # reject obvious coordinates/decimals around match
        span=text[max(0,m.start()-2):m.end()+2]
        if re.search(r"\d+\.\d+", span): continue
        candidates.append(s)
    out=[]
    for c in candidates:
        try:
            nums = phonenumbers.PhoneNumberMatcher(c, region or None)
            for match in nums:
                if phonenumbers.is_possible_number(match.number) and phonenumbers.is_valid_number(match.number):
                    out.append(phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
        except Exception:
            pass
        # Try direct parse fallback
        try:
            n=phonenumbers.parse(c, region or None)
            if phonenumbers.is_possible_number(n) and phonenumbers.is_valid_number(n):
                out.append(phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
        except NumberParseException:
            pass
    return sorted(set(out))

def find_candidate_links(base_url, html, targets):
    soup=BeautifulSoup(html or "", "html.parser")
    links=[]; base=clean_url(base_url)
    for a in soup.find_all("a", href=True):
        txt=(a.get_text(" ") or "").lower(); href=a["href"].lower()
        joined=urllib.parse.urljoin(base, a["href"])
        if domain_of(joined) != domain_of(base): continue
        score=0
        for target in targets:
            if target in txt or target in href: score += 1
        if score: links.append((score, joined))
    links=sorted(set(links), reverse=True)
    return [u for _,u in links]

def scrape_site(url, sector, depth="Fast", region=None, timeout=7):
    profile=SECTOR_PROFILES[sector]
    if depth=="Fast": targets=profile["page_targets_fast"]; max_pages=2; timeout=6
    elif depth=="Standard": targets=profile["page_targets_standard"]; max_pages=4; timeout=8
    else: targets=profile["page_targets_deep"]; max_pages=7; timeout=10
    url=clean_url(url)
    html, final_url, status=safe_get(url, timeout=timeout)
    if not html:
        return {"scrape_status":"scrape_failed", "source_url":url, "visible_emails":"", "generic_emails":"", "website_phone":"", "all_phones_found":"", "role_signals":""}
    pages=[final_url]
    pages += find_candidate_links(final_url, html, targets)[:max_pages-1]
    all_text=""; source_pages=[]
    for p in pages[:max_pages]:
        h, fu, stt=safe_get(p, timeout=timeout)
        if h:
            soup=BeautifulSoup(h, "html.parser")
            all_text += "\n" + soup.get_text(" ", strip=True)[:50000]
            all_text += "\n" + h[:50000]
            source_pages.append(fu)
    emails=extract_emails(all_text)
    generic=[e for e in emails if re.match(r"^(info|contact|office|admin|admissions|enquiries|reception|hello|school)@", e)]
    phones=extract_phones(all_text, region=region)
    role_hits=[term for term in profile["role_terms"] if re.search(re.escape(term), all_text, re.I)]
    return {
        "scrape_status":"scraped",
        "source_url":"; ".join(source_pages[:5]),
        "visible_emails":"; ".join(emails),
        "generic_emails":"; ".join(generic),
        "website_phone":"; ".join(phones[:5]),
        "all_phones_found":"; ".join(phones[:10]),
        "role_signals":"; ".join(sorted(set(role_hits))),
    }

def ddg_search(query, max_results=5):
    if DDGS is None: return []
    try:
        with DDGS(timeout=12) as ddgs:
            return list(ddgs.text(query, max_results=max_results)) or []
    except Exception:
        return []

def resolve_website_for_name(name, location_hint=""):
    """Resolve an official website from a name. Bounded but better than domain guessing."""
    if not name:
        return "", "none"
    queries = [
        f'"{name}" official website',
        f'"{name}" "{location_hint}" school',
        f'"{name}" contact',
    ]
    best_url, best_score = "", -999
    for q in queries:
        for res in ddg_html_search(q, 5):
            u = res.get("url", "").split("#")[0]
            if not u.startswith("http"):
                continue
            sc = score_official_website(name, u, res.get("title", ""), location_hint)
            if sc > best_score:
                best_url, best_score = u.split("?")[0], sc
        if best_score >= 12:
            break
    if best_url and best_score >= 6:
        return best_url, f"search_resolved_score_{best_score}"
    # parent institution fallback for campus-labelled places
    parent = re.sub(r"\s*\([^)]*campus[^)]*\)\s*", "", name, flags=re.I).strip()
    parent = re.sub(r"\b(campus|branch)\b.*$", "", parent, flags=re.I).strip(" -,")
    if parent and parent != name:
        for res in ddg_html_search(f'"{parent}" official website', 3):
            u = res.get("url", "").split("#")[0]
            sc = score_official_website(parent, u, res.get("title", ""), location_hint)
            if sc >= 6:
                return u.split("?")[0], f"parent_search_resolved_score_{sc}"
    return "", "not_found"

