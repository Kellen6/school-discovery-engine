
import streamlit as st
import pandas as pd
import requests, re, time, io, hashlib, json
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin, quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher

try:
    import phonenumbers
except Exception:
    phonenumbers = None

st.set_page_config(page_title="Prospect Discovery Engine", layout="wide")

USER_AGENT = "ProspectDiscoveryEngine/22.0 (educational outreach research; contact: user)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

# -----------------------------
# Session state
# -----------------------------
for key, default in {
    "debug_log": [],
    "candidate_cache": None,
    "candidate_cache_key": None,
    "enriched_cache": None,
    "enriched_cache_key": None,
    "last_timings": {},
    "last_metrics": {},
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

def log(msg):
    st.session_state.debug_log.append(str(msg))

def now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M")

def slugify(s, max_len=60):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(s).lower()).strip("_")
    return s[:max_len] or "search"

def make_filename(mode, query, kind, ext):
    return f"prospect_discovery_{slugify(mode)}_{slugify(query)}_{kind}_{now_stamp()}.{ext}"

# -----------------------------
# Basic utilities
# -----------------------------
def safe_get(url, timeout=10):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        return r
    except Exception as e:
        return None

def clean_url(url):
    if not url:
        return ""
    url = str(url).strip()
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url):
        url = "https://" + url
    # strip common tracking
    try:
        p=urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")
    except Exception:
        return url.rstrip("/")

def domain_of(url):
    try:
        d = urlparse(clean_url(url)).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""

BAD_DOMAINS = {
    "facebook.com","instagram.com","linkedin.com","twitter.com","x.com","youtube.com",
    "wikipedia.org","wikimapia.org","tripadvisor.com","business.site","google.com",
    "google.co.za","maps.google.com","za.linkedin.com"
}
DIRECTORY_HINTS = [
    "schoolguide", "schoolsdigest", "schoolparrot", "saschools", "saschoolsdirect",
    "schools4sa", "yellosa", "africabizinfo", "brabys", "cybo", "snupit",
    "findglocal", "rateyourlecturer", "hellopeter", "vymaps", "waze", "near-place",
    "mapcarta", "osm", "openstreetmap"
]
FALSE_POSITIVE_TERMS = [
    "testing yard","driver","driving licence","licensing","traffic department",
    "parking","sports field","residence","student residence","house residence",
    "village residence","hostel","accommodation","bus stop","train station"
]
EDU_TERMS = [
    "school","college","academy","university","primary","high","pre-primary","prep",
    "campus","institute","education","learning","montessori","waldorf"
]

def is_bad_candidate_name(name):
    n=str(name).lower()
    if any(t in n for t in FALSE_POSITIVE_TERMS):
        # allow if actual education words and not just residence
        if not any(t in n for t in ["school","college","university","academy","campus"]):
            return True
    return False

def is_directory_domain(url):
    d=domain_of(url)
    return any(h in d for h in DIRECTORY_HINTS)

def likely_official_url(url, name=""):
    if not url:
        return False
    d=domain_of(url)
    if not d or any(b in d for b in BAD_DOMAINS):
        return False
    if is_directory_domain(url):
        return False
    return True

def name_tokens(name):
    toks = re.findall(r"[a-z0-9]+", str(name).lower())
    stop={"the","of","and","in","at","campus","school","college","academy","primary","high","pre","preprimary","university"}
    return [t for t in toks if t not in stop and len(t)>1]

def score_url_for_name(url, name, title="", snippet="", location_hint=""):
    if not url:
        return -999
    d=domain_of(url)
    if not d or any(b in d for b in BAD_DOMAINS):
        return -200
    score=0
    text=(d+" "+title+" "+snippet).lower()
    toks=name_tokens(name)
    hits=sum(1 for t in toks if t in text)
    score += hits*8
    if toks:
        score += int(30 * hits / max(len(toks),1))
    # domain similarity
    compact_name="".join(toks)
    compact_dom=re.sub(r"[^a-z0-9]","",d.split(".")[0])
    if compact_name and compact_dom:
        score += int(30*SequenceMatcher(None, compact_name, compact_dom).ratio())
    if ".edu" in d or ".ac." in d:
        score += 8
    if d.endswith(".za") or ".co.za" in d or ".org.za" in d or ".ac.za" in d:
        if "south africa" in location_hint.lower() or "cape town" in location_hint.lower():
            score += 10
    if any(x in text for x in ["official","home","admissions","contact","school","college","university"]):
        score += 7
    if is_directory_domain(url):
        score -= 60
    return score

# -----------------------------
# Geocode + discovery
# -----------------------------
def geocode(location):
    url = "https://nominatim.openstreetmap.org/search"
    params={"q":location,"format":"jsonv2","limit":1,"addressdetails":1}
    try:
        r=requests.get(url, params=params, headers=HEADERS, timeout=18)
        log(f"Geocode HTTP {r.status_code}: {r.url}")
        if r.status_code==200 and r.json():
            j=r.json()[0]
            return float(j["lat"]), float(j["lon"]), j.get("display_name","")
    except Exception as e:
        log(f"Geocode error: {type(e).__name__}: {e}")
    return None, None, ""

def overpass_discover(lat, lon, radius_m, sector_terms, max_results):
    endpoints=[
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.osm.ch/api/interpreter",
    ]
    # broader OSM tags
    terms = sector_terms or ["school","college","university","kindergarten"]
    query=f"""
    [out:json][timeout:25];
    (
      node(around:{int(radius_m)},{lat},{lon})["amenity"~"school|college|university|kindergarten",i];
      way(around:{int(radius_m)},{lat},{lon})["amenity"~"school|college|university|kindergarten",i];
      relation(around:{int(radius_m)},{lat},{lon})["amenity"~"school|college|university|kindergarten",i];
      node(around:{int(radius_m)},{lat},{lon})["office"~"educational_institution",i];
      node(around:{int(radius_m)},{lat},{lon})["building"~"school|college|university",i];
    );
    out center tags {int(max_results)};
    """
    for ep in endpoints:
        try:
            r=requests.post(ep, data={"data":query}, headers=HEADERS, timeout=30)
            log(f"Overpass POST {ep}: HTTP {r.status_code}")
            if r.status_code==200:
                data=r.json()
                rows=[]
                for el in data.get("elements",[])[:max_results]:
                    tags=el.get("tags",{}) or {}
                    name=tags.get("name") or tags.get("official_name") or ""
                    if not name or is_bad_candidate_name(name):
                        continue
                    latv=el.get("lat") or (el.get("center") or {}).get("lat")
                    lonv=el.get("lon") or (el.get("center") or {}).get("lon")
                    website=tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
                    phone=tags.get("phone") or tags.get("contact:phone") or ""
                    email=tags.get("email") or tags.get("contact:email") or ""
                    rows.append({
                        "prospect_name": name,
                        "sector": "school",
                        "source": "overpass",
                        "address": tags.get("addr:full",""),
                        "city": tags.get("addr:city",""),
                        "country": tags.get("addr:country",""),
                        "latitude": latv,
                        "longitude": lonv,
                        "website": clean_url(website) if website else "",
                        "osm_phone": phone,
                        "osm_email": email,
                    })
                log(f"Overpass {ep}: {len(rows)} candidates")
                if rows:
                    return rows
        except Exception as e:
            log(f"Overpass {ep}: {type(e).__name__}: {e}")
    return []

def nominatim_discover(location, sector_queries, max_results):
    rows=[]
    seen=set()
    queries=sector_queries or ["school", "private school", "international school", "college", "university", "academy"]
    for q in queries:
        full=f"{q} in {location}"
        try:
            r=requests.get("https://nominatim.openstreetmap.org/search",
                           params={"q":full,"format":"jsonv2","limit":min(max_results,50),"addressdetails":1,"extratags":1},
                           headers=HEADERS, timeout=20)
            log(f"Nominatim '{full}': HTTP {r.status_code}")
            if r.status_code != 200:
                continue
            for j in r.json():
                name=j.get("name") or j.get("display_name","").split(",")[0]
                if not name or is_bad_candidate_name(name):
                    continue
                key=(name.lower(), round(float(j.get("lat",0)),5), round(float(j.get("lon",0)),5))
                if key in seen:
                    continue
                seen.add(key)
                extra=j.get("extratags") or {}
                addr=j.get("address") or {}
                website=extra.get("website") or extra.get("contact:website") or ""
                rows.append({
                    "prospect_name": name,
                    "sector": "school",
                    "source": "nominatim",
                    "address": j.get("display_name",""),
                    "city": addr.get("city") or addr.get("town") or addr.get("suburb") or "",
                    "country": addr.get("country",""),
                    "latitude": j.get("lat",""),
                    "longitude": j.get("lon",""),
                    "website": clean_url(website) if website else "",
                    "osm_phone": extra.get("phone") or extra.get("contact:phone") or "",
                    "osm_email": extra.get("email") or extra.get("contact:email") or "",
                })
                if len(rows)>=max_results:
                    return rows
        except Exception as e:
            log(f"Nominatim error '{full}': {type(e).__name__}: {e}")
    return rows

# -----------------------------
# Search + website resolution
# -----------------------------
def ddg_html_search(query, max_results=8, timeout=12):
    # lightweight HTML endpoint; can be blocked but usually works enough
    url="https://duckduckgo.com/html/"
    try:
        r=requests.get(url, params={"q":query}, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return []
        soup=BeautifulSoup(r.text, "html.parser")
        out=[]
        for a in soup.select("a.result__a")[:max_results]:
            href=a.get("href","")
            title=a.get_text(" ", strip=True)
            parent=a.find_parent("div", class_="result")
            snippet=parent.get_text(" ", strip=True) if parent else ""
            # DDG may use redirect url with uddg param
            if "uddg=" in href:
                import urllib.parse as up
                qs=up.parse_qs(up.urlparse(href).query)
                href=qs.get("uddg",[href])[0]
            out.append({"url":clean_url(href),"title":title,"snippet":snippet})
        return out
    except Exception as e:
        return []

def guess_domains(name, country_hint=""):
    toks=name_tokens(name)
    if not toks:
        return []
    base="".join(toks)
    base2="-".join(toks)
    tlds=["co.za","org.za","ac.za","school.za","com","org"]
    guesses=[]
    for b in [base, base2]:
        for tld in tlds:
            guesses.append(f"https://www.{b}.{tld}")
            guesses.append(f"https://{b}.{tld}")
    return guesses[:16]

def validate_homepage(url, name, timeout=7):
    r=safe_get(url, timeout=timeout)
    if not r or r.status_code >= 400 or "text/html" not in r.headers.get("content-type",""):
        return False, "", ""
    soup=BeautifulSoup(r.text[:250000], "html.parser")
    title=(soup.title.get_text(" ", strip=True) if soup.title else "")
    text=soup.get_text(" ", strip=True)[:3000]
    score=score_url_for_name(url, name, title, text)
    return score>=35, title, text

def resolve_website_for_name(name, location_hint="", mode="standard"):
    if not name:
        return "", "none"
    # 1) official website search
    queries=[
        f'"{name}" "{location_hint}" official website',
        f'"{name}" school website',
        f'"{name}" contact',
    ]
    if mode=="deep":
        queries += [f'"{name}" admissions', f'"{name}" "{location_hint}"']
    best=("", -999, "search")
    for q in queries[: (1 if mode=="fast" else len(queries))]:
        results=ddg_html_search(q, max_results=6 if mode!="deep" else 10, timeout=10)
        for res in results:
            url=res["url"]
            if not likely_official_url(url, name):
                continue
            sc=score_url_for_name(url, name, res.get("title",""), res.get("snippet",""), location_hint)
            if sc > best[1]:
                best=(url, sc, "search_official")
        if best[1] >= 55:
            return best[0], best[2]
    # 2) domain guesses only in standard/deep
    if mode!="fast":
        for url in guess_domains(name, location_hint):
            ok, title, text = validate_homepage(url, name, timeout=4)
            if ok:
                return clean_url(url), "domain_guess"
    # 3) accept weaker search if not directory
    if best[1] >= 38:
        return best[0], best[2] + "_low_conf"
    return "", "not_found"

def resolve_websites(rows, location_hint, mode, workers, progress=None):
    out=[dict(r) for r in rows]
    missing=[(i,r) for i,r in enumerate(out) if not r.get("website")]
    if not missing:
        return out
    total=len(missing)
    done=0
    def worker(item):
        i,r=item
        try:
            url, method = resolve_website_for_name(r.get("prospect_name",""), location_hint, mode=mode)
            return i, url, method
        except Exception as e:
            return i, "", f"error:{type(e).__name__}"
    with ThreadPoolExecutor(max_workers=max(1,workers)) as ex:
        futs=[ex.submit(worker, item) for item in missing]
        for fut in as_completed(futs):
            i,url,method=fut.result()
            if url:
                out[i]["website"]=url
                out[i]["website_source"]=method
            else:
                out[i]["website_source"]=out[i].get("website_source") or method
            done+=1
            if progress:
                progress.progress(min(done/total,1.0), text=f"Finding websites: {done}/{total}")
    return out

# -----------------------------
# Contact scraping
# -----------------------------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
OBFUSCATED_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+\-]+)\s*(?:\[at\]|\(at\)| at )\s*([A-Za-z0-9.\-]+)\s*(?:\[dot\]|\(dot\)| dot )\s*([A-Za-z]{2,})", re.I)

PHONE_CANDIDATE_RE = re.compile(r"(?:\+?\d[\d\s().\-\/]{6,}\d)")

CONTACT_PATH_HINTS = ["contact", "admissions", "enrol", "enroll", "staff", "leadership", "about", "office", "reception", "campus", "support"]

def extract_emails(text):
    emails=set(EMAIL_RE.findall(text or ""))
    for m in OBFUSCATED_EMAIL_RE.findall(text or ""):
        emails.add(f"{m[0]}@{m[1]}.{m[2]}")
    bad=["example.com","domain.com","email.com","yourname"]
    return sorted(e.lower() for e in emails if not any(b in e.lower() for b in bad))

def country_code_from_hint(location_hint, row):
    c=(row.get("country") or location_hint or "").lower()
    if "south africa" in c: return "ZA"
    if "nigeria" in c: return "NG"
    if "kenya" in c: return "KE"
    if "ghana" in c: return "GH"
    if "united kingdom" in c or "uk" in c: return "GB"
    if "united states" in c or "usa" in c: return "US"
    return None

def extract_phones(text, region=None):
    text=text or ""
    found=set()
    if phonenumbers:
        try:
            for match in phonenumbers.PhoneNumberMatcher(text, region):
                num=match.number
                if phonenumbers.is_valid_number(num):
                    found.add(phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
        except Exception:
            pass
    # fallback candidates if library misses some
    for cand in PHONE_CANDIDATE_RE.findall(text):
        digits=re.sub(r"\D","",cand)
        if len(digits)<8 or len(digits)>15: 
            continue
        if re.match(r"^[0-9]{8,15}$", digits):
            # reject coordinates/random decimals
            if "." in cand and not cand.strip().startswith("+"):
                continue
            found.add(cand.strip())
    return sorted(found)

def fetch_pages_for_site(base_url, mode="fast"):
    pages=[]
    base=clean_url(base_url)
    if not base:
        return pages
    r=safe_get(base, timeout=8 if mode=="fast" else 12)
    if r and r.status_code<400 and "text/html" in r.headers.get("content-type",""):
        pages.append((base,r.text))
        soup=BeautifulSoup(r.text, "html.parser")
        links=[]
        for a in soup.find_all("a", href=True):
            href=a.get("href")
            text=(a.get_text(" ", strip=True) + " " + href).lower()
            if any(h in text for h in CONTACT_PATH_HINTS):
                u=urljoin(base, href)
                if domain_of(u)==domain_of(base):
                    links.append(u.split("#")[0])
        # page limits by mode
        max_pages={"fast":2,"standard":5,"deep":10}.get(mode,3)
        seen={base}
        for u in links:
            if u in seen or len(pages)>=max_pages:
                continue
            seen.add(u)
            rr=safe_get(u, timeout=6 if mode=="fast" else 10)
            if rr and rr.status_code<400 and "text/html" in rr.headers.get("content-type",""):
                pages.append((u,rr.text))
    return pages

GENERIC_PREFIXES=("info@","contact@","admissions@","admin@","office@","reception@","enquiries@","enquiry@","hello@")

def search_contacts(name, location_hint, region, mode):
    if mode=="fast":
        return [], [], []
    queries=[
        f'"{name}" phone',
        f'"{name}" contact email',
        f'"{name}" admissions',
    ]
    if mode=="deep":
        queries += [f'"{name}" reception', f'"{name}" office', f'"{name}" "+27"', f'"{name}" staff']
    emails=set(); phones=set(); sources=[]
    for q in queries:
        for res in ddg_html_search(q, max_results=5 if mode=="standard" else 8, timeout=9):
            txt=(res.get("title","")+" "+res.get("snippet",""))
            for e in extract_emails(txt): emails.add(e)
            for p in extract_phones(txt, region): phones.add(p)
            # scrape likely result pages in deep only / directories allowed
            if mode=="deep" and res.get("url"):
                rr=safe_get(res["url"], timeout=6)
                if rr and rr.status_code<400 and "text/html" in rr.headers.get("content-type",""):
                    t=BeautifulSoup(rr.text[:200000],"html.parser").get_text(" ", strip=True)
                    for e in extract_emails(t): emails.add(e)
                    for p in extract_phones(t, region): phones.add(p)
                    sources.append(res["url"])
        if mode=="standard" and (emails or phones):
            break
    return sorted(emails), sorted(phones), sources[:5]

def enrich_one(row, location_hint, mode, use_search_fallback):
    r=dict(row)
    region=country_code_from_hint(location_hint, r)
    website=r.get("website","")
    website_emails=[]; website_phones=[]; source_pages=[]
    status="no_website"
    if website:
        try:
            pages=fetch_pages_for_site(website, mode=mode)
            if pages:
                status="scraped"
                combined=""
                for u,html in pages:
                    source_pages.append(u)
                    text=BeautifulSoup(html[:500000], "html.parser").get_text(" ", strip=True)
                    combined += "\n" + text
                website_emails=extract_emails(combined)
                website_phones=extract_phones(combined, region)
            else:
                status="scrape_failed"
        except Exception as e:
            status=f"scrape_failed:{type(e).__name__}"
    search_emails=[]; search_phones=[]; search_sources=[]
    if use_search_fallback and (mode!="fast") and (not website_emails or not website_phones or status!="scraped"):
        search_emails, search_phones, search_sources = search_contacts(r.get("prospect_name",""), location_hint, region, mode)
    osm_email=(r.get("osm_email") or "").strip()
    osm_phone=(r.get("osm_phone") or "").strip()
    all_emails=sorted(set(website_emails + search_emails + ([osm_email] if osm_email else [])))
    generic=[e for e in all_emails if e.startswith(GENERIC_PREFIXES)]
    best_email = (generic[0] if generic else (all_emails[0] if all_emails else ""))
    best_phone = website_phones[0] if website_phones else (search_phones[0] if search_phones else osm_phone)
    phone_source = "website" if website_phones else ("search" if search_phones else ("osm" if osm_phone else ""))
    email_source = "website" if website_emails else ("search" if search_emails else ("osm" if osm_email else ""))
    r.update({
        "enrichment_status": status,
        "visible_emails": "; ".join(website_emails),
        "generic_emails": "; ".join(generic),
        "search_emails": "; ".join(search_emails),
        "best_email": best_email,
        "email_source": email_source,
        "website_phone": "; ".join(website_phones),
        "search_phone": "; ".join(search_phones),
        "best_phone": best_phone,
        "phone_source": phone_source,
        "source_pages": "; ".join(source_pages[:5]),
        "search_source_pages": "; ".join(search_sources[:5]),
    })
    return r

def enrich_rows(rows, location_hint, mode, use_search_fallback, workers, progress=None):
    out=[]
    total=len(rows)
    done=0
    def worker(r):
        return enrich_one(r, location_hint, mode, use_search_fallback)
    with ThreadPoolExecutor(max_workers=max(1,workers)) as ex:
        futs=[ex.submit(worker, r) for r in rows]
        for fut in as_completed(futs):
            try:
                out.append(fut.result())
            except Exception as e:
                rr={"prospect_name":"ERROR", "enrichment_status":f"error:{type(e).__name__}"}
                out.append(rr)
            done+=1
            if progress:
                progress.progress(min(done/total,1.0), text=f"Enriching prospects: {done}/{total}")
    # preserve original order
    order={str(r.get("prospect_name","")).lower():i for i,r in enumerate(rows)}
    out.sort(key=lambda r: order.get(str(r.get("prospect_name","")).lower(), 999999))
    return out

# -----------------------------
# UI
# -----------------------------
st.title("Prospect Discovery Engine")
st.caption("Find prospects, resolve websites, and enrich contact details.")

with st.sidebar:
    st.header("Search setup")
    sector=st.selectbox("Sector profile", ["Schools", "Universities / Colleges", "Clinics / Health", "NGOs / Nonprofits", "Custom"], index=0)
    mode_choice=st.radio("Search method", ["Map / location", "Prospect name", "Website URL", "Source/list page"], index=0)
    st.divider()
    enrich_mode=st.selectbox("Contact search depth", ["Fast", "Standard", "Deep"], index=0,
                             help="Fast is quickest: website homepage/contact only. Standard adds official website search. Deep adds broader contact search.")
    use_fallback=st.checkbox("Use web search fallback for missing websites/contact details", value=False)
    speed=st.select_slider("Processing speed", options=["Slow", "Balanced", "Fast", "Maximum"], value="Balanced")
    workers={"Slow":2,"Balanced":5,"Fast":10,"Maximum":16}[speed]
    st.caption(f"Processing speed: {speed}")

mode=enrich_mode.lower()

if mode_choice=="Map / location":
    location=st.text_input("Location", "Cape Town, Western Cape, South Africa")
    radius=st.slider("Search radius (km)", 1, 100, 10)
    max_candidates=st.slider("Max prospects", 10, 300, 100, step=10)
elif mode_choice=="Prospect name":
    location=st.text_input("Location hint", "Cape Town, Western Cape, South Africa")
    names_txt=st.text_area("Prospect names, one per line", height=200)
    max_candidates=999
elif mode_choice=="Website URL":
    location=st.text_input("Location hint", "Cape Town, Western Cape, South Africa")
    urls_txt=st.text_area("Website URLs, one per line", height=200)
    max_candidates=999
else:
    location=st.text_input("Location hint", "Cape Town, Western Cape, South Africa")
    source_urls_txt=st.text_area("Source/list page URLs, one per line", height=180)
    max_candidates=st.slider("Max prospects", 10, 300, 100, step=10)

def sector_queries(sector):
    if sector.startswith("School"):
        return ["school","private school","international school","college","university","academy"]
    if sector.startswith("Universities"):
        return ["university","college","campus","higher education"]
    if sector.startswith("Clinics"):
        return ["clinic","hospital","health centre","medical centre"]
    if sector.startswith("NGOs"):
        return ["nonprofit","NGO","charity","foundation"]
    return ["school","college","clinic","NGO"]

def cache_key_for_candidates():
    raw={"mode":mode_choice,"sector":sector,"location":location,"radius":radius if mode_choice=="Map / location" else None,
         "max":max_candidates,
         "names":names_txt if mode_choice=="Prospect name" else None,
         "urls":urls_txt if mode_choice=="Website URL" else None,
         "sources":source_urls_txt if mode_choice=="Source/list page" else None}
    return hashlib.md5(json.dumps(raw,sort_keys=True).encode()).hexdigest()

def cache_key_for_enrichment(cand_key):
    raw={"cand_key":cand_key,"enrich_mode":mode,"use_fallback":use_fallback,"workers":workers}
    return hashlib.md5(json.dumps(raw,sort_keys=True).encode()).hexdigest()

def discover_from_source_pages(txt, max_candidates):
    rows=[]; seen=set()
    for src in [u.strip() for u in txt.splitlines() if u.strip()]:
        r=safe_get(clean_url(src), timeout=12)
        if not r or r.status_code>=400:
            continue
        soup=BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            text=a.get_text(" ", strip=True)
            href=urljoin(src, a.get("href"))
            if not href.startswith("http"):
                continue
            if not likely_official_url(href, text):
                continue
            name=text or domain_of(href)
            if is_bad_candidate_name(name):
                continue
            d=domain_of(href)
            if d in seen: continue
            seen.add(d)
            rows.append({"prospect_name":name, "sector":sector, "source":"source_page", "website":clean_url(href), "address":"", "city":"", "country":"", "latitude":"", "longitude":"", "osm_phone":"", "osm_email":""})
            if len(rows)>=max_candidates:
                break
    return rows

def build_candidates():
    if mode_choice=="Map / location":
        lat, lon, display = geocode(location)
        if lat is None:
            return []
        rows=overpass_discover(lat, lon, radius*1000, [], max_candidates)
        # supplement with nominatim, don't replace
        supplement=nominatim_discover(location, sector_queries(sector), max_candidates)
        seen=set((r.get("prospect_name","").lower(), str(r.get("latitude",""))[:8], str(r.get("longitude",""))[:8]) for r in rows)
        for r in supplement:
            key=(r.get("prospect_name","").lower(), str(r.get("latitude",""))[:8], str(r.get("longitude",""))[:8])
            if key not in seen and len(rows)<max_candidates:
                rows.append(r); seen.add(key)
        return rows[:max_candidates]
    if mode_choice=="Prospect name":
        rows=[]
        for nm in [n.strip() for n in names_txt.splitlines() if n.strip()]:
            if not is_bad_candidate_name(nm):
                rows.append({"prospect_name":nm,"sector":sector,"source":"manual_name","website":"","address":"","city":"","country":"","latitude":"","longitude":"","osm_phone":"","osm_email":""})
        return rows
    if mode_choice=="Website URL":
        rows=[]
        for u in [x.strip() for x in urls_txt.splitlines() if x.strip()]:
            d=domain_of(u)
            rows.append({"prospect_name":d or u,"sector":sector,"source":"manual_url","website":clean_url(u),"address":"","city":"","country":"","latitude":"","longitude":"","osm_phone":"","osm_email":""})
        return rows
    return discover_from_source_pages(source_urls_txt, max_candidates)

button_label="Find prospects"
if st.button(button_label, type="primary"):
    st.session_state.debug_log=[]
    t0=time.time()
    ckey=cache_key_for_candidates()
    ekey=cache_key_for_enrichment(ckey)
    timings={}
    if st.session_state.candidate_cache_key==ckey and st.session_state.candidate_cache is not None:
        st.info(f"Using saved prospect list: {len(st.session_state.candidate_cache)} prospects. Updating contact details only.")
        candidates=st.session_state.candidate_cache
    else:
        if st.session_state.candidate_cache_key is None:
            st.info("Starting new prospect search…")
        else:
            st.info("Search inputs changed. Finding a new prospect list…")
        p=st.progress(0, text="Finding prospects…")
        t=time.time()
        candidates=build_candidates()
        timings["discovery_seconds"]=round(time.time()-t,2)
        st.session_state.candidate_cache=candidates
        st.session_state.candidate_cache_key=ckey
        p.progress(1.0, text=f"Prospect search complete: {len(candidates)} prospects found.")
        # changed candidates invalidate enriched cache
        st.session_state.enriched_cache=None
        st.session_state.enriched_cache_key=None
    if not candidates:
        st.warning("No prospects found. Try a broader location/radius or another search method.")
    else:
        if st.session_state.enriched_cache_key==ekey and st.session_state.enriched_cache is not None:
            rows=st.session_state.enriched_cache
            st.success("Using saved enriched results for these settings.")
        else:
            t=time.time()
            p1=st.progress(0, text="Finding official websites for prospects without one…")
            with_web=resolve_websites(candidates, location, mode, workers, progress=p1)
            timings["website_resolution_seconds"]=round(time.time()-t,2)
            t=time.time()
            p2=st.progress(0, text="Finding contact details from websites and search results…")
            rows=enrich_rows(with_web, location, mode, use_fallback, workers, progress=p2)
            timings["enrichment_seconds"]=round(time.time()-t,2)
            st.session_state.enriched_cache=rows
            st.session_state.enriched_cache_key=ekey
        timings["total_seconds"]=round(time.time()-t0,2)
        st.session_state.last_timings=timings
        df=pd.DataFrame(rows)
        st.session_state.last_df=df

# Display
df=st.session_state.get("last_df")
if df is not None and isinstance(df,pd.DataFrame) and not df.empty:
    st.subheader("Prospects")
    display_cols=[
        "prospect_name","website","best_email","best_phone","enrichment_status",
        "email_source","phone_source","address","source"
    ]
    display_cols=[c for c in display_cols if c in df.columns]
    st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Prospects", len(df))
    c2.metric("Websites", int(df.get("website",pd.Series(dtype=str)).fillna("").astype(str).ne("").sum()) if "website" in df else 0)
    c3.metric("Emails", int(df.get("best_email",pd.Series(dtype=str)).fillna("").astype(str).ne("").sum()) if "best_email" in df else 0)
    c4.metric("Phones", int(df.get("best_phone",pd.Series(dtype=str)).fillna("").astype(str).ne("").sum()) if "best_phone" in df else 0)
    st.subheader("Export")
    query = location if 'location' in globals() else mode_choice
    csv=df.to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV", data=csv, file_name=make_filename(mode_choice, query, "prospects", "csv"), mime="text/csv")
    bio=io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Prospects")
    st.download_button("Download Excel", data=bio.getvalue(), file_name=make_filename(mode_choice, query, "prospects", "xlsx"), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with st.expander("Diagnostics"):
        st.write("Timing")
        st.json(st.session_state.last_timings)
        st.write("Debug log")
        st.text("\n".join(st.session_state.debug_log[-200:]))
else:
    st.info("Choose a search method and click **Find prospects**.")
