import streamlit as st
import pandas as pd
import requests, re, json, time, io, hashlib
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
try:
    import phonenumbers
except Exception:
    phonenumbers = None

st.set_page_config(page_title="Prospect Discovery Engine", layout="wide")

HEADERS = {"User-Agent": "ProspectDiscoveryEngine/34 contact-enrichment research"}
COMMON_FALSE_DOMAINS = {
    "palmolive.com", "palmolive.org", "nairobi.com", "olm.com", "bps.com", "msps.com",
    "academy.com", "school.com", "college.com", "education.com", "primaryschool.com"
}
DIRECTORY_HINTS = ["directory", "listing", "yelp", "waze", "facebook", "linkedin", "yellow", "primaryschool.co", "kenyaprimary", "schoolguide", "schools4sa", "businesslist"]
COUNTRY_TLDS = {"south africa":"za", "kenya":"ke", "nigeria":"ng", "ghana":"gh", "uganda":"ug", "rwanda":"rw", "tanzania":"tz", "united kingdom":"uk", "canada":"ca", "united states":"us", "india":"in"}
SCHOOL_TERMS = ["school", "college", "academy", "primary", "secondary", "high school", "preparatory", "prep", "international school"]
SCHOOL_EXCLUDES = ["driving school", "testing yard", "residence", "hostel", "student accommodation", "parking", "training college"]
DEFAULT_PAGES = ["contact", "contact-us", "admissions", "admission", "about", "staff", "team", "leadership"]
DEEP_PAGES = DEFAULT_PAGES + ["apply", "fees", "downloads", "prospectus", "newsletter", "campus", "locations", "support", "learning-support", "vacancies"]

for key, default in {
    "debug": [], "prospects": None, "candidate_key": None, "timing": {}, "profile": None
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

def log(msg):
    st.session_state.debug.append(str(msg))

def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")[:80]

def tokens(s):
    return [t for t in re.findall(r"[a-z0-9]+", str(s or "").lower()) if len(t) > 2 and t not in {"the","and","of","for","primary","secondary","school","college","academy"}]

def country_from_location(location):
    parts = [p.strip() for p in location.split(",") if p.strip()]
    return parts[-1] if parts else ""

def country_tld(country):
    return COUNTRY_TLDS.get(str(country).lower(), "")

def clean_url(url):
    if not url: return ""
    url = str(url).strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url

def domain(url):
    try:
        netloc = urlparse(clean_url(url)).netloc.lower().replace("www.", "")
        return netloc.split(":")[0]
    except Exception:
        return ""

def is_directory_url(url):
    u = (url or "").lower()
    return any(h in u for h in DIRECTORY_HINTS)

def fetch(url, timeout=10):
    try:
        r = requests.get(clean_url(url), headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code < 400 and r.text:
            return r.url, r.text[:150000]
    except Exception:
        return "", ""
    return "", ""

def page_title_and_text(url):
    real, html = fetch(url, timeout=8)
    if not html: return real or url, "", ""
    soup = BeautifulSoup(html, "html.parser")
    title = norm(soup.title.get_text(" ")) if soup.title else ""
    for tag in soup(["script", "style", "noscript"]): tag.decompose()
    text = norm(soup.get_text(" "))[:6000]
    return real or url, title, text

def website_score(name, url, location, sector="schools"):
    d = domain(url)
    if not d or d in COMMON_FALSE_DOMAINS: return -100
    if is_directory_url(url): return -10
    real, title, text = page_title_and_text(url)
    hay = f"{d} {title} {text}".lower()
    score = 0
    name_tokens = tokens(name)
    if name_tokens:
        hits = sum(1 for t in name_tokens if t in hay)
        score += hits * 18
        if hits >= max(1, len(name_tokens)-1): score += 25
    loc_tokens = tokens(location)
    score += min(15, sum(3 for t in loc_tokens if t in hay))
    if sector == "schools":
        if any(t in hay for t in SCHOOL_TERMS): score += 25
        if any(x in hay for x in SCHOOL_EXCLUDES): score -= 35
    else:
        if any(t in hay for t in tokens(sector)): score += 15
    c = country_from_location(location)
    tld = country_tld(c)
    if tld and d.endswith("."+tld): score += 20
    if d.endswith((".ac."+tld, ".edu."+tld, ".school", ".edu")) and tld: score += 10
    if d.endswith((".com", ".org", ".net")) and tld and tld not in {"us"}:
        score -= 10
    return score

def duck_search(query, max_results=8):
    # Lightweight HTML search fallback. May be blocked occasionally but fails silently.
    out=[]
    try:
        url = "https://duckduckgo.com/html/?q=" + quote_plus(query)
        r = requests.get(url, headers=HEADERS, timeout=12)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a.result__a")[:max_results]:
            href = a.get("href") or ""
            text = norm(a.get_text(" "))
            if href:
                out.append((text, href))
    except Exception as e:
        log(f"Search failed for {query}: {type(e).__name__}")
    return out

def resolve_website(name, location, sector="schools", extra=False):
    name = norm(name)
    if not name: return "", "not found", "", ""
    cands=[]
    # search-first official website resolution
    queries = [f'"{name}" "{location}" official website', f'"{name}" "{location}"', f'"{name}" school website'] if sector=="schools" else [f'"{name}" "{location}" official website', f'"{name}" "{location}" contact']
    if extra:
        queries += [f'"{name}" website', f'"{name}" contact phone email']
    for q in queries[:4 if extra else 3]:
        for title, href in duck_search(q, max_results=6):
            href = clean_url(href)
            d = domain(href)
            if not d or d in COMMON_FALSE_DOMAINS: continue
            cands.append(href)
    # deterministic local-domain guesses, validated by fetching
    base = re.sub(r"[^a-z0-9]", "", name.lower())
    short = "".join(tokens(name)[:3])
    tld = country_tld(country_from_location(location))
    guesses=[]
    if tld:
        guesses += [f"https://www.{base}.co.{tld}", f"https://www.{base}.org.{tld}", f"https://www.{base}.ac.{tld}", f"https://www.{short}.co.{tld}", f"https://www.{short}.org.{tld}"]
    guesses += [f"https://www.{base}.org", f"https://www.{base}.com"]
    cands += guesses
    # de-dupe by domain
    seen=set(); scored=[]
    for u in cands:
        d=domain(u)
        if not d or d in seen: continue
        seen.add(d)
        s=website_score(name,u,location,sector)
        if s > -50:
            scored.append((s,u))
    scored.sort(reverse=True, key=lambda x:x[0])
    cand_str = "; ".join([u for s,u in scored[:5]])
    if not scored: return "", "not found", "", ""
    top_s, top_u = scored[0]
    if top_s >= 70: return top_u, "verified official", cand_str, str(top_s)
    if top_s >= 45: return top_u, "likely official", cand_str, str(top_s)
    return "", "candidates only", cand_str, str(top_s)

def extract_emails(text):
    text = text or ""
    text = re.sub(r"\s*\[at\]\s*|\s*\(at\)\s*|\s+at\s+", "@", text, flags=re.I)
    text = re.sub(r"\s*\[dot\]\s*|\s*\(dot\)\s*|\s+dot\s+", ".", text, flags=re.I)
    emails = set(re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text))
    bad = {"example.com", "domain.com"}
    return sorted([e for e in emails if domain("https://"+e.split("@")[-1]) not in bad])

def extract_phones(text, country):
    if not text: return []
    candidates = set()
    for m in re.findall(r"(?:\+?\d[\d\s().\-]{6,}\d)", text):
        raw = re.sub(r"\s+", " ", m).strip()
        if len(re.sub(r"\D", "", raw)) < 8: continue
        candidates.add(raw)
    out=[]
    if phonenumbers:
        region_map = {"south africa":"ZA", "kenya":"KE", "nigeria":"NG", "ghana":"GH", "uganda":"UG", "rwanda":"RW", "tanzania":"TZ", "united kingdom":"GB", "canada":"CA", "united states":"US"}
        region = region_map.get(str(country).lower())
        for raw in candidates:
            try:
                pn = phonenumbers.parse(raw, region)
                if phonenumbers.is_possible_number(pn) and phonenumbers.is_valid_number(pn):
                    out.append(phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
            except Exception:
                pass
    else:
        out=list(candidates)
    return sorted(set(out))

def discover(location, sector_label, max_results=50):
    t0=time.time(); results=[]
    # geocode
    try:
        gq = "https://nominatim.openstreetmap.org/search?q="+quote_plus(location)+"&format=jsonv2&limit=1&addressdetails=1"
        gr = requests.get(gq, headers=HEADERS, timeout=15)
        log(f"Geocode HTTP {gr.status_code}: {gq}")
        gj = gr.json()[0]
        lat, lon = float(gj["lat"]), float(gj["lon"])
    except Exception as e:
        log(f"Geocode failed: {e}")
        lat, lon = None, None
    terms = SCHOOL_TERMS if sector_label=="schools" else st.session_state.profile.get("entity_terms", [sector_label])
    # Overpass for schools; Nominatim generic fallback for all sectors
    if lat is not None and sector_label=="schools":
        q = f"""[out:json][timeout:25];(node(around:30000,{lat},{lon})[amenity~\"school|college|university|kindergarten\"];way(around:30000,{lat},{lon})[amenity~\"school|college|university|kindergarten\"];relation(around:30000,{lat},{lon})[amenity~\"school|college|university|kindergarten\"];);out center tags {max_results};"""
        try:
            r=requests.post("https://overpass-api.de/api/interpreter", data={"data":q}, headers=HEADERS, timeout=35)
            log(f"Overpass POST https://overpass-api.de/api/interpreter: HTTP {r.status_code}")
            if r.status_code==200:
                elems=r.json().get("elements", [])
                log(f"Overpass candidates: {len(elems)}")
                for el in elems:
                    tags=el.get("tags", {})
                    name=tags.get("name") or tags.get("operator") or ""
                    if not name: continue
                    low=name.lower()
                    if any(x in low for x in SCHOOL_EXCLUDES): continue
                    results.append({"organization_name":name,"website":tags.get("website") or tags.get("contact:website") or "","osm_phone":tags.get("phone") or tags.get("contact:phone") or "","source":"overpass","address":tags.get("addr:street", ""),"country":country_from_location(location)})
        except Exception as e:
            log(f"Overpass failed: {type(e).__name__}")
    for term in terms[:8]:
        try:
            nq = f"{term} in {location}"
            url="https://nominatim.openstreetmap.org/search?q="+quote_plus(nq)+"&format=jsonv2&limit="+str(max_results)+"&addressdetails=1&extratags=1"
            r=requests.get(url, headers=HEADERS, timeout=18)
            log(f"Nominatim '{nq}': HTTP {r.status_code}")
            for item in r.json()[:max_results]:
                name=item.get("name") or item.get("display_name", "").split(",")[0]
                if not name: continue
                et=item.get("extratags") or {}
                results.append({"organization_name":name,"website":et.get("website") or "","osm_phone":et.get("phone") or "","source":"nominatim","address":item.get("display_name",""),"country":country_from_location(location)})
        except Exception as e:
            log(f"Nominatim failed for {term}: {type(e).__name__}")
    # dedupe
    seen=set(); out=[]
    for r in results:
        key=re.sub(r"[^a-z0-9]", "", r["organization_name"].lower())[:40]
        if key in seen: continue
        seen.add(key); out.append(r)
        if len(out)>=max_results: break
    st.session_state.timing["discovery_seconds"] = round(time.time()-t0,2)
    return out

def find_links_for_pages(base_url, pages):
    links=[base_url]
    real, html = fetch(base_url, timeout=10)
    if html:
        soup=BeautifulSoup(html,"html.parser")
        for a in soup.find_all("a", href=True):
            txt=(a.get_text(" ")+" "+a["href"]).lower()
            if any(p.replace("-"," ") in txt.replace("-"," ") for p in pages):
                links.append(urljoin(real or base_url, a["href"]))
    # fallback common paths
    for p in pages:
        links.append(urljoin(base_url, "/"+p))
    seen=[]
    for l in links:
        if domain(l)==domain(base_url) and l not in seen:
            seen.append(l)
    return seen[:14]

def enrich_row(row, location, sector, extra_contacts=False):
    country=row.get("country") or country_from_location(location)
    name=row.get("organization_name") or ""
    website=clean_url(row.get("website") or "")
    status="from source" if website else "not found"
    candidates=""; wscore=""
    if not website:
        website,status,candidates,wscore=resolve_website(name, location, sector, extra=extra_contacts)
    pages = DEEP_PAGES if extra_contacts else DEFAULT_PAGES
    emails=set(); phones=set(); contact_pages=[]
    if website:
        for url in find_links_for_pages(website, pages):
            real, html = fetch(url, timeout=10)
            if not html: continue
            contact_pages.append(real or url)
            soup=BeautifulSoup(html,"html.parser")
            for a in soup.find_all("a", href=True):
                href=a["href"]
                if href.lower().startswith("mailto:"):
                    emails.update(extract_emails(href.replace("mailto:","")))
                if href.lower().startswith("tel:"):
                    phones.update(extract_phones(href.replace("tel:",""), country))
            text=soup.get_text(" ") + " " + html[:50000]
            emails.update(extract_emails(text))
            phones.update(extract_phones(text, country))
            if emails and phones and not extra_contacts:
                break
    osm_phone = row.get("osm_phone") or ""
    if osm_phone:
        phones.update(extract_phones(osm_phone, country))
    best_email = sorted(emails)[0] if emails else ""
    best_phone = sorted(phones)[0] if phones else ""
    out=dict(row)
    out.update({
        "website": website,
        "official_website_status": status,
        "website_candidates": candidates,
        "website_score": wscore,
        "visible_emails": "; ".join(sorted(emails)),
        "best_email": best_email,
        "website_phone": "; ".join(sorted(phones)),
        "best_phone": best_phone,
        "contact_pages_checked": "; ".join(contact_pages[:5]),
        "enrichment_status": "scraped" if website else "no_website"
    })
    return out

def algorithmic_profile(query):
    q=query.lower().strip()
    terms={q, q.rstrip("s")}
    if "pizza" in q:
        terms.update(["pizza restaurant","pizzeria","pizza takeaway","Italian restaurant"])
        meta="food business"; pages=["contact","menu","locations","order","about"]; roles=["owner","manager","reception"]
    elif "physio" in q or "physical therapist" in q:
        terms.update(["physiotherapist","physio clinic","physiotherapy clinic","rehabilitation clinic","sports physio"])
        meta="healthcare provider"; pages=["contact","appointments","booking","team","services","about"]; roles=["practice manager","owner","reception","clinician"]
    else:
        terms.update([q+" business", q+" service", q+" company"])
        meta="business"; pages=["contact","about","team","services","locations"]; roles=["owner","manager","director","reception"]
    return {"sector":q, "meta_type":meta, "entity_terms":list(terms), "priority_pages":pages, "target_roles":roles, "exclude_terms":["jobs","courses","directory"]}

def to_excel(df):
    buf=io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer,index=False,sheet_name="Prospects")
    return buf.getvalue()

st.title("Prospect Discovery Engine")
st.caption("Find prospects by location, resolve official websites, and enrich phone/email contact data.")

col1,col2=st.columns([1,2])
with col1:
    mode=st.radio("Prospect type", ["Schools (optimized)", "Custom search"], horizontal=False)
with col2:
    if mode=="Custom search":
        custom=st.text_input("What are you looking for?", value="physical therapists")
        st.session_state.profile=algorithmic_profile(custom)
        with st.expander("Generated search profile", expanded=True):
            st.json(st.session_state.profile)
        sector="custom"
    else:
        custom="schools"; sector="schools"; st.session_state.profile={"entity_terms":SCHOOL_TERMS}
    location=st.text_input("Location", value="Cape Town, Western Cape, South Africa")

with st.sidebar:
    st.header("Advanced settings")
    max_results=st.slider("Maximum prospects", 10, 200, 50, 10)
    speed_label=st.select_slider("Processing speed", options=["Slow", "Balanced", "Fast"], value="Balanced")
    workers={"Slow":2,"Balanced":5,"Fast":10}[speed_label]
    extra_contacts=st.checkbox("Find more contact details when missing", value=False)
    extra_web=st.checkbox("Extra thorough website search", value=False)
    show_diag=st.checkbox("Show diagnostics", value=True)
    if st.button("Clear results"):
        st.session_state.prospects=None; st.session_state.debug=[]; st.session_state.timing={}; st.rerun()

run=st.button("Find prospects", type="primary")

p1=st.progress(0, text="Discovery not started")
p2=st.progress(0, text="Website resolution not started")
p3=st.progress(0, text="Contact enrichment not started")

if run:
    st.session_state.debug=[]; st.session_state.timing={}
    total_start=time.time()
    p1.progress(5, text="Finding prospects in location…")
    candidates=discover(location, sector, max_results=max_results)
    p1.progress(100, text=f"Discovery complete: {len(candidates)} prospects")
    p2.progress(5, text="Resolving websites and enriching contacts…")
    t=time.time(); rows=[]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs=[ex.submit(enrich_row, r, location, sector, extra_contacts or extra_web) for r in candidates]
        for i,f in enumerate(as_completed(futs),1):
            try: rows.append(f.result())
            except Exception as e: log(f"Enrichment row failed: {type(e).__name__}")
            p2.progress(min(100, int(i/max(1,len(futs))*100)), text=f"Processed {i}/{len(futs)} prospects")
    st.session_state.timing["website_resolution_seconds"] = round(time.time()-t,2)
    p2.progress(100, text="Website/contact processing complete")
    p3.progress(100, text="Contact enrichment complete")
    st.session_state.timing["enrichment_seconds"] = st.session_state.timing.get("website_resolution_seconds",0)
    st.session_state.timing["total_seconds"] = round(time.time()-total_start,2)
    st.session_state.prospects=pd.DataFrame(rows)

if st.session_state.prospects is not None:
    df=st.session_state.prospects.copy()
    st.subheader("Prospects")
    preferred=["organization_name","website","official_website_status","best_email","best_phone","visible_emails","website_phone","address","source","website_candidates","contact_pages_checked","enrichment_status"]
    show=[c for c in preferred if c in df.columns]
    st.dataframe(df[show], use_container_width=True, hide_index=True)
    ts=datetime.now().strftime("%Y%m%d_%H%M")
    fname=f"prospect_discovery_{slug(custom)}_{slug(location)}_{ts}"
    c1,c2=st.columns(2)
    with c1:
        st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8"), file_name=fname+".csv", mime="text/csv")
    with c2:
        st.download_button("Download Excel", to_excel(df), file_name=fname+".xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if show_diag:
        with st.expander("Diagnostics", expanded=False):
            st.json(st.session_state.timing)
            st.text("\n".join(st.session_state.debug[-80:]))
else:
    if show_diag:
        with st.expander("Diagnostics", expanded=False):
            st.write("Run a search to see timing and debug logs.")
