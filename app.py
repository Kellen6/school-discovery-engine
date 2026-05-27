
import streamlit as st
import pandas as pd
import requests, re, time, json, hashlib, io
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
try:
    import phonenumbers
except Exception:
    phonenumbers = None

st.set_page_config(page_title="Prospect Discovery Engine", layout="wide")

# -----------------------------
# Session init
# -----------------------------
for k, v in {
    "prospects": [],
    "debug_log": [],
    "timing": {},
    "candidate_key": None,
    "candidate_cache": [],
    "retention": {},
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

def log(msg):
    st.session_state.debug_log.append(str(msg))

def now_slug():
    return datetime.now().strftime("%Y%m%d_%H%M")

def slug(s):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", str(s).lower()).strip("_")
    return s[:80] or "search"

# -----------------------------
# Profiles
# -----------------------------
SCHOOL_INCLUDE = [
    "school","college","academy","primary","secondary","high","junior","prep","preparatory",
    "educare","montessori","learning","education","institute","international"
]
SCHOOL_EXCLUDE = [
    "driving school","driving test","testing yard","traffic department","license","licence",
    "residence","student residence","hostel","house residence","parking","stadium","sports field",
    "dance studio","theatre school","language school","training centre","training center"
]
# Do not exclude all arts/language schools universally; for Laura K-12 schools are priority, but discovery should retain and tag.
SOFT_EXCLUDE = ["theatre school","language school","dance studio","music school"]

PAGE_KEYWORDS_BASE = [
    "contact","contact-us","admissions","apply","enrol","enroll","about","staff","team",
    "leadership","principal","heads","campus","locations","fees","downloads","prospectus",
    "newsletter","vacancies","support","learning-support"
]

HEADERS = {"User-Agent": "Mozilla/5.0 ProspectDiscovery/1.0 (compatible; outreach research)"}

# -----------------------------
# HTTP helpers
# -----------------------------
def safe_get(url, timeout=12):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and r.text:
            return r
        return None
    except Exception:
        return None

def get_text_and_soup(url):
    r = safe_get(url)
    if not r:
        return "", None, ""
    ctype = r.headers.get("content-type","")
    if "pdf" in ctype.lower() or url.lower().endswith(".pdf"):
        return "", None, r.url
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script","style","noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True), soup, r.url

# -----------------------------
# Geo/discovery
# -----------------------------
def geocode(location):
    url=f"https://nominatim.openstreetmap.org/search?q={quote_plus(location)}&format=jsonv2&limit=1&addressdetails=1"
    r=requests.get(url, headers=HEADERS, timeout=20)
    log(f"Geocode HTTP {r.status_code}: {url}")
    r.raise_for_status()
    data=r.json()
    if not data:
        return None
    d=data[0]
    addr=d.get("address",{}) or {}
    return {
        "lat": float(d["lat"]), "lon": float(d["lon"]),
        "display_name": d.get("display_name",""),
        "country": addr.get("country",""),
        "country_code": (addr.get("country_code","") or "").upper()
    }

def overpass_query(lat, lon, radius_m, profile):
    # deliberately broad: get many educational OSM features, then filter later
    if profile == "Schools":
        q=f"""
        [out:json][timeout:35];
        (
          node(around:{radius_m},{lat},{lon})["amenity"~"school|college|university|kindergarten|language_school|music_school|dancing_school"];
          way(around:{radius_m},{lat},{lon})["amenity"~"school|college|university|kindergarten|language_school|music_school|dancing_school"];
          relation(around:{radius_m},{lat},{lon})["amenity"~"school|college|university|kindergarten|language_school|music_school|dancing_school"];
          node(around:{radius_m},{lat},{lon})["school"];
          way(around:{radius_m},{lat},{lon})["school"];
          relation(around:{radius_m},{lat},{lon})["school"];
          node(around:{radius_m},{lat},{lon})["building"="school"];
          way(around:{radius_m},{lat},{lon})["building"="school"];
        );
        out center tags {min(250, max(50, int(radius_m/300)))};
        """
    else:
        q=f"""
        [out:json][timeout:35];
        (
          node(around:{radius_m},{lat},{lon})["name"];
          way(around:{radius_m},{lat},{lon})["name"];
        );
        out center tags 250;
        """
    endpoints=["https://overpass-api.de/api/interpreter","https://overpass.kumi.systems/api/interpreter","https://overpass.osm.ch/api/interpreter"]
    for ep in endpoints:
        try:
            r=requests.post(ep, data={"data":q}, headers=HEADERS, timeout=40)
            log(f"Overpass POST {ep}: HTTP {r.status_code}")
            if r.status_code == 200:
                js=r.json()
                elems=js.get("elements",[])
                log(f"Overpass {ep}: {len(elems)} raw elements")
                return elems
        except Exception as e:
            log(f"Overpass error {ep}: {type(e).__name__}: {e}")
    return []

def nominatim_search_terms(location, profile, custom_term=""):
    if profile == "Schools":
        return [
            f"school in {location}",
            f"primary school in {location}",
            f"high school in {location}",
            f"private school in {location}",
            f"international school in {location}",
            f"college in {location}",
            f"academy in {location}",
        ]
    else:
        base=custom_term.strip() or "business"
        return [f"{base} in {location}", f"{base} near {location}"]

def nominatim_discover(location, profile, custom_term, limit_each=50):
    rows=[]
    for term in nominatim_search_terms(location, profile, custom_term):
        url=f"https://nominatim.openstreetmap.org/search?q={quote_plus(term)}&format=jsonv2&limit={limit_each}&addressdetails=1&extratags=1"
        try:
            r=requests.get(url, headers=HEADERS, timeout=20)
            log(f"Nominatim '{term}': HTTP {r.status_code}")
            if r.status_code!=200: continue
            for d in r.json():
                addr=d.get("address",{}) or {}
                extra=d.get("extratags",{}) or {}
                rows.append({
                    "organization_name": d.get("name") or d.get("display_name","").split(",")[0],
                    "website": extra.get("website") or extra.get("url") or "",
                    "osm_phone": extra.get("phone") or extra.get("contact:phone") or "",
                    "source": "nominatim",
                    "address": d.get("display_name",""),
                    "lat": d.get("lat",""),
                    "lon": d.get("lon",""),
                    "country": addr.get("country",""),
                    "country_code": (addr.get("country_code","") or "").upper()
                })
        except Exception as e:
            log(f"Nominatim error '{term}': {type(e).__name__}: {e}")
    return rows

def elements_to_rows(elems, country="", country_code=""):
    rows=[]
    for e in elems:
        tags=e.get("tags",{}) or {}
        name=tags.get("name") or tags.get("official_name") or ""
        if not name: 
            rows.append({"_skip_reason":"no_name"})
            continue
        lat=e.get("lat") or (e.get("center") or {}).get("lat","")
        lon=e.get("lon") or (e.get("center") or {}).get("lon","")
        addr=", ".join([v for k,v in tags.items() if k.startswith("addr:") and k in ["addr:housenumber","addr:street","addr:suburb","addr:city"]])
        rows.append({
            "organization_name": name,
            "website": tags.get("website") or tags.get("contact:website") or tags.get("url") or "",
            "osm_phone": tags.get("phone") or tags.get("contact:phone") or "",
            "source": "overpass",
            "address": addr,
            "lat": lat,
            "lon": lon,
            "country": country,
            "country_code": country_code,
            "_tags": json.dumps(tags)[:500]
        })
    return rows

def is_false_positive(name, profile):
    n=name.lower()
    if profile=="Schools":
        hard=["driving test","testing yard","traffic department","licence","license department","parking"]
        return any(x in n for x in hard)
    return False

def dedupe_rows(rows):
    seen=set(); out=[]; dup=0
    for r in rows:
        name=(r.get("organization_name") or "").strip()
        if not name: 
            continue
        # Name + approximate coordinate; avoids collapsing campuses with different location
        lat=str(r.get("lat",""))[:7]; lon=str(r.get("lon",""))[:7]
        key=re.sub(r"\W+","",name.lower())+"|"+lat+"|"+lon
        if key in seen:
            dup += 1
            continue
        seen.add(key); out.append(r)
    return out, dup

def discover(location, radius_km, max_prospects, profile, custom_term):
    geo=geocode(location)
    if not geo: return []
    elems=overpass_query(geo["lat"], geo["lon"], int(radius_km*1000), profile)
    raw=elements_to_rows(elems, geo["country"], geo["country_code"])
    raw += nominatim_discover(location, profile, custom_term, limit_each=40)
    no_name=sum(1 for r in raw if r.get("_skip_reason")=="no_name")
    cleaned=[]
    false=0
    for r in raw:
        name=r.get("organization_name","")
        if not name: continue
        if is_false_positive(name, profile):
            false += 1
            continue
        cleaned.append(r)
    deduped, dup = dedupe_rows(cleaned)
    retained=deduped[:max_prospects]
    st.session_state.retention={
        "raw_found": len(raw),
        "no_name": no_name,
        "false_positives_removed": false,
        "duplicates_removed": dup,
        "retained_prospects": len(retained)
    }
    return retained

# -----------------------------
# Website resolution
# -----------------------------
COUNTRY_TLD = {"ZA":"za","KE":"ke","NG":"ng","UG":"ug","RW":"rw","GH":"gh","TZ":"tz","GB":"uk","UK":"uk","US":"us","CA":"ca","AU":"au","IN":"in"}

def normalize_url(u):
    if not u: return ""
    u=str(u).strip()
    if not u: return ""
    if not u.startswith(("http://","https://")):
        u="https://"+u
    return u

def domain(u):
    try:
        return urlparse(normalize_url(u)).netloc.lower().replace("www.","")
    except Exception:
        return ""

def name_tokens(name):
    bad={"school","college","academy","primary","secondary","high","junior","prep","preparatory","the","of","and","for","campus"}
    toks=[t for t in re.findall(r"[a-z0-9]+", name.lower()) if len(t)>2 and t not in bad]
    return toks

def score_site(name, url, title_text, country_code="", location=""):
    d=domain(url)
    text=(title_text or "").lower()
    toks=name_tokens(name)
    score=0
    if toks:
        match=sum(1 for t in toks if t in text or t in d)
        score += match*20
        if match >= max(1, min(2, len(toks))): score += 20
    edu_words=["school","college","academy","education","primary","secondary","admissions","learners","students"]
    score += 10 if any(w in text for w in edu_words) else 0
    cc=COUNTRY_TLD.get(country_code.upper(),"")
    if cc and (d.endswith("."+cc) or f".ac.{cc}" in d or f".co.{cc}" in d or f".or.{cc}" in d or f".edu.{cc}" in d):
        score += 20
    # Generic short domains are risky unless heavily evidenced
    if re.fullmatch(r"[a-z]{2,5}\.(com|org|net)", d):
        score -= 30
    if any(bad in d for bad in ["facebook.com","instagram.com","linkedin.com","wikipedia.org","tripadvisor","waze.com"]):
        score -= 25
    return score

def ddg_html_search(query, max_results=5):
    url=f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    r=safe_get(url, timeout=15)
    if not r: return []
    soup=BeautifulSoup(r.text,"html.parser")
    links=[]
    for a in soup.select("a.result__a")[:max_results]:
        href=a.get("href","")
        txt=a.get_text(" ", strip=True)
        if href:
            links.append((txt, href))
    # fallback any links
    if not links:
        for a in soup.find_all("a", href=True)[:20]:
            href=a["href"]; txt=a.get_text(" ", strip=True)
            if href.startswith("http"):
                links.append((txt, href))
    return links[:max_results]

def resolve_website(name, location, country_code, existing=""):
    existing=normalize_url(existing)
    candidates=[]
    if existing:
        text, soup, final = get_text_and_soup(existing)
        sc=score_site(name, final or existing, (soup.title.string if soup and soup.title else "")+" "+text[:1000], country_code, location)
        if sc >= 35:
            return final or existing, "verified official" if sc>=60 else "likely official", sc, existing
        candidates.append(existing)
    queries=[
        f'"{name}" "{location}" official website',
        f'"{name}" school "{location}"',
        f'"{name}" contact',
    ]
    best=("", "not found", -999, "; ".join(candidates))
    for q in queries:
        for title, href in ddg_html_search(q, max_results=4):
            u=normalize_url(href)
            d=domain(u)
            if not d or any(x in d for x in ["duckduckgo.com","google.com"]): continue
            candidates.append(u)
            text, soup, final = get_text_and_soup(u)
            title_text=(title+" "+(soup.title.string if soup and soup.title else "")+" "+text[:1200])
            sc=score_site(name, final or u, title_text, country_code, location)
            if sc > best[2]:
                status="verified official" if sc>=70 else ("likely official" if sc>=45 else "candidate only")
                best=(final or u, status, sc, "; ".join(dict.fromkeys(candidates)))
            if best[2] >= 70:
                return best
    if best[2] >= 45:
        return best
    return "", "not found", best[2] if best[2]!=-999 else 0, "; ".join(dict.fromkeys(candidates[:6]))

# -----------------------------
# Contact extraction
# -----------------------------
EMAIL_RE=re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)

def deobfuscate(text):
    t=text
    reps=[(" [at] ","@"),(" (at) ","@"),("[at]","@"),("(at)","@"),(" at ","@"),
          (" [dot] ","."),(" (dot) ","."),("[dot]", "."),("(dot)", "."),(" dot ",".")]
    for a,b in reps: t=t.replace(a,b)
    return t

def extract_emails(text, soup=None):
    found=set(EMAIL_RE.findall(deobfuscate(text or "")))
    if soup:
        for a in soup.find_all("a", href=True):
            href=a["href"]
            if href.lower().startswith("mailto:"):
                found.add(href.split(":",1)[1].split("?")[0])
    return sorted(e.strip().lower() for e in found if not e.lower().endswith((".png",".jpg",".jpeg")))

def extract_phones(text, soup=None, country_code=""):
    raw=set()
    if soup:
        for a in soup.find_all("a", href=True):
            href=a["href"]
            if href.lower().startswith("tel:"):
                raw.add(href.split(":",1)[1])
    # Broad candidates
    for m in re.findall(r"(?:\+\d{1,3}[\s().-]*)?(?:\(?0?\d{2,4}\)?[\s.-]*)?\d{3,4}[\s.-]*\d{3,4}", text or ""):
        if len(re.sub(r"\D","",m))>=7:
            raw.add(m)
    out=[]
    for p in raw:
        s=p.strip()
        if phonenumbers:
            try:
                region=country_code if country_code else None
                num=phonenumbers.parse(s, region)
                if phonenumbers.is_possible_number(num) and phonenumbers.is_valid_number(num):
                    out.append(phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
                    continue
            except Exception:
                pass
        digits=re.sub(r"\D","",s)
        if 7 <= len(digits) <= 14 and not re.match(r"^(000|111|123)",digits):
            out.append(s)
    return sorted(set(out))

def relevant_links(base_url, soup, max_links=8, deep=False):
    if not soup: return []
    kws=PAGE_KEYWORDS_BASE if deep else ["contact","contact-us","admissions","about"]
    links=[]
    for a in soup.find_all("a", href=True):
        txt=(a.get_text(" ", strip=True)+" "+a["href"]).lower()
        if any(k in txt for k in kws):
            u=urljoin(base_url, a["href"])
            if domain(u)==domain(base_url):
                links.append(u)
    # keep unique order
    return list(dict.fromkeys(links))[:max_links]

def scrape_contacts(row, location, deep_contacts=False):
    website=normalize_url(row.get("website",""))
    country_code=row.get("country_code","") or ""
    result = {
        "visible_emails":"","search_emails":"","best_email":"",
        "website_phone":"","search_phone":"","best_phone":"",
        "contact_pages_checked":"","enrichment_status":"no_website"
    }
    if not website:
        return result
    pages=[website]
    text, soup, final = get_text_and_soup(website)
    if not soup:
        result["enrichment_status"]="scrape_failed"
        return result
    pages += relevant_links(final or website, soup, max_links=10 if deep_contacts else 4, deep=deep_contacts)
    all_text=""; all_emails=set(); all_phones=set(); checked=[]
    for p in list(dict.fromkeys(pages)):
        t, s, f = get_text_and_soup(p)
        if not s and not t: continue
        checked.append(f or p)
        all_text += " " + t[:8000]
        all_emails.update(extract_emails(t, s))
        all_phones.update(extract_phones(t, s, country_code))
    result["visible_emails"]="; ".join(sorted(all_emails))
    result["website_phone"]="; ".join(sorted(all_phones))
    result["best_email"]=sorted(all_emails)[0] if all_emails else ""
    result["best_phone"]=sorted(all_phones)[0] if all_phones else (row.get("osm_phone","") or "")
    result["contact_pages_checked"]="; ".join(checked[:12])
    result["enrichment_status"]="scraped"
    return result

# -----------------------------
# UI
# -----------------------------
st.title("Prospect Discovery Engine")

col1, col2 = st.columns([1,1])
with col1:
    mode = st.radio("What type of prospects?", ["Schools (optimized)", "Custom search"], horizontal=True)
    profile = "Schools" if mode.startswith("Schools") else "Custom"
with col2:
    location = st.text_input("Location", value="Cape Town, Western Cape, South Africa")

custom_term=""
if profile=="Custom":
    custom_term=st.text_input("What are you looking for?", value="physical therapists")
    st.caption("Custom search expands this term into generic place/business discovery. Schools mode keeps the optimized school logic.")

c1,c2,c3=st.columns(3)
with c1:
    radius_km=st.slider("Search radius (km)", 1, 100, 30)
with c2:
    max_prospects=st.slider("Max prospects", 10, 250, 50)
with c3:
    find_more=st.checkbox("Find more contact details when missing", value=False)

with st.sidebar:
    st.header("Advanced settings")
    speed=st.select_slider("Processing speed", options=["Slow","Balanced","Fast"], value="Balanced")
    workers={"Slow":2,"Balanced":5,"Fast":10}[speed]
    use_places=st.checkbox("Use Google Places when available", value=False)
    show_diag=st.checkbox("Show diagnostics", value=True)

run=st.button("Find prospects", type="primary")

p1=st.progress(0, text="Discovery")
p2=st.progress(0, text="Website resolution")
p3=st.progress(0, text="Contact enrichment")

def cache_key():
    raw=json.dumps({"profile":profile,"custom":custom_term,"location":location,"radius":radius_km,"max":max_prospects}, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()

if run:
    st.session_state.debug_log=[]
    st.session_state.timing={}
    t0=time.time()
    key=cache_key()
    if st.session_state.candidate_key == key and st.session_state.candidate_cache:
        candidates=st.session_state.candidate_cache
        log(f"Using cached candidate set: {len(candidates)} prospects")
        p1.progress(100, text=f"Discovery skipped: using {len(candidates)} cached prospects")
        st.session_state.timing["discovery_seconds"]=0
    else:
        p1.progress(10, text="Finding prospects...")
        candidates=discover(location, radius_km, max_prospects, profile, custom_term)
        st.session_state.candidate_key=key
        st.session_state.candidate_cache=candidates
        st.session_state.timing["discovery_seconds"]=round(time.time()-t0,2)
        p1.progress(100, text=f"Discovery complete: {len(candidates)} prospects retained")
    # website resolution
    tw=time.time()
    rows=[dict(r) for r in candidates]
    p2.progress(5, text="Resolving websites...")
    def res_worker(i_r):
        i,r=i_r
        name=r.get("organization_name","")
        if not r.get("website"):
            url,status,score,cands=resolve_website(name, location, r.get("country_code",""), "")
        else:
            url,status,score,cands=resolve_website(name, location, r.get("country_code",""), r.get("website",""))
        return i,url,status,score,cands
    done=0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs=[ex.submit(res_worker, item) for item in enumerate(rows)]
        for fut in as_completed(futs):
            i,url,status,score,cands=fut.result()
            if url: rows[i]["website"]=url
            rows[i]["official_website_status"]=status
            rows[i]["website_score"]=score
            rows[i]["website_candidates"]=cands
            done+=1
            p2.progress(min(100,int(done/max(1,len(rows))*100)), text=f"Website resolution: {done}/{len(rows)}")
    st.session_state.timing["website_resolution_seconds"]=round(time.time()-tw,2)
    # enrichment
    te=time.time()
    p3.progress(5, text="Scraping contact details...")
    enriched=[None]*len(rows); done=0
    def enr_worker(i_r):
        i,r=i_r
        c=scrape_contacts(r, location, deep_contacts=find_more)
        rr=dict(r); rr.update(c)
        return i,rr
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs=[ex.submit(enr_worker, item) for item in enumerate(rows)]
        for fut in as_completed(futs):
            i,rr=fut.result()
            enriched[i]=rr
            done+=1
            p3.progress(min(100,int(done/max(1,len(rows))*100)), text=f"Contact enrichment: {done}/{len(rows)}")
    st.session_state.timing["enrichment_seconds"]=round(time.time()-te,2)
    st.session_state.timing["total_seconds"]=round(time.time()-t0,2)
    st.session_state.timing["processing_speed"]=speed
    st.session_state.prospects=[r for r in enriched if r]

if st.session_state.prospects:
    df=pd.DataFrame(st.session_state.prospects)
    # Ensure useful columns
    cols=["organization_name","website","official_website_status","best_email","visible_emails","best_phone","website_phone","osm_phone","source","address","lat","lon","country","website_candidates","website_score","contact_pages_checked","enrichment_status"]
    for c in cols:
        if c not in df.columns: df[c]=""
    df=df[cols + [c for c in df.columns if c not in cols]]
    websites=int(df["website"].astype(str).str.len().gt(0).sum())
    emails=int(df["best_email"].astype(str).str.len().gt(0).sum())
    phones=int(df["best_phone"].astype(str).str.len().gt(0).sum())
    m1,m2,m3,m4=st.columns(4)
    m1.metric("Prospects", len(df))
    m2.metric("Websites", websites)
    m3.metric("Emails", emails)
    m4.metric("Phones", phones)
    st.subheader("Prospects")
    show_cols=["organization_name","website","official_website_status","best_email","best_phone","source","enrichment_status"]
    st.dataframe(df[show_cols], use_container_width=True)
    fname=f"prospect_discovery_{slug(profile)}_{slug(location)}_{now_slug()}"
    csv=df.to_csv(index=False).encode("utf-8")
    bio=io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="prospects")
    d1,d2=st.columns(2)
    d1.download_button("Download CSV", csv, file_name=f"{fname}.csv", mime="text/csv")
    d2.download_button("Download Excel", bio.getvalue(), file_name=f"{fname}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if show_diag:
        with st.expander("Diagnostics", expanded=False):
            st.json(st.session_state.retention)
            st.json(st.session_state.timing)
            st.text("\n".join(st.session_state.debug_log[-200:]))
else:
    st.info("Enter your search criteria and click Find prospects.")
