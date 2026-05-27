import streamlit as st
import requests, re, json, time, io, hashlib, urllib.parse, concurrent.futures
from bs4 import BeautifulSoup
import pandas as pd
import phonenumbers
import tldextract
from urllib.parse import urljoin, urlparse
from datetime import datetime

try:
    import PyPDF2
except Exception:
    PyPDF2 = None

st.set_page_config(page_title="Prospect Discovery Engine", layout="wide")

UA = "ProspectDiscoveryEngine/32 contact-research bot"
TIMEOUT = 12

# ------------------------- State/helpers -------------------------
for k, v in {
    "debug": [], "timing": {}, "prospects": None, "candidate_key": None,
    "last_inputs": None, "profile": None
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

def log(msg):
    try:
        st.session_state.debug.append(str(msg))
    except Exception:
        pass

def norm_space(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")[:80] or "prospects"

def get_country_from_location(location):
    parts = [p.strip() for p in str(location).split(',') if p.strip()]
    return parts[-1] if parts else ""

COUNTRY_TLDS = {
    "south africa": [".co.za", ".org.za", ".ac.za", ".school.za", ".edu.za"],
    "kenya": [".ac.ke", ".co.ke", ".or.ke", ".sc.ke", ".go.ke"],
    "nigeria": [".edu.ng", ".sch.ng", ".com.ng", ".org.ng"],
    "ghana": [".edu.gh", ".com.gh", ".org.gh"],
    "tanzania": [".ac.tz", ".co.tz", ".or.tz", ".sc.tz"],
    "uganda": [".ac.ug", ".co.ug", ".or.ug", ".sc.ug"],
    "rwanda": [".ac.rw", ".co.rw", ".org.rw", ".rw"],
    "united kingdom": [".ac.uk", ".sch.uk", ".org.uk", ".co.uk"],
    "united states": [".edu", ".org", ".com", ".us"],
}

GENERIC_BAD_DOMAINS = set("""
facebook.com instagram.com linkedin.com youtube.com wikipedia.org waze.com google.com google.co.za
palmolive.com bps.com msps.com olm.com nairobi.com olympic.edu busara.org
""".split())
DIRECTORY_HINTS = ["directory", "listing", "review", "top", "best", "schools near", "primaryschool.co", "kenyaprimary", "businesslist", "yellow", "waze", "maps", "facebook.com"]
SCHOOL_WORDS = ["school", "college", "academy", "primary", "secondary", "high", "junior", "preparatory", "prep", "kindergarten", "learning", "campus", "education"]

# ------------------------- Profile expansion -------------------------
def build_profile(mode, custom_term=""):
    if mode == "Schools (optimized)":
        return {
            "sector": "schools",
            "entity_terms": ["school", "private school", "international school", "primary school", "secondary school", "college", "academy"],
            "include_terms": SCHOOL_WORDS,
            "exclude_terms": ["driving school", "testing yard", "license", "residence", "hostel", "student accommodation", "course", "jobs"],
            "priority_pages_fast": ["contact", "contact-us", "admissions", "about"],
            "priority_pages_deep": ["staff", "team", "leadership", "principal", "campus", "prep", "secondary", "kindergarten", "learning-support", "downloads", "prospectus", "fees", "newsletter"],
            "role_terms": ["principal", "head", "headmaster", "headmistress", "admissions", "counselor", "counsellor", "learning support", "SEN", "registrar"],
            "official_terms": SCHOOL_WORDS,
        }
    term = norm_space(custom_term).lower()
    base = [term]
    if "pizza" in term:
        base += ["pizzeria", "pizza restaurant", "pizza takeaway", "italian restaurant"]
        meta = "food"
    elif any(x in term for x in ["physio", "physical therapist", "therapist"]):
        base += ["physiotherapist", "physio clinic", "physiotherapy clinic", "rehabilitation clinic", "sports physio"]
        meta = "healthcare"
    else:
        words = term.split()
        base += [term.rstrip("s"), term + " business", term + " service", term + " company"]
        meta = "generic"
    return {
        "sector": term or "custom",
        "entity_terms": list(dict.fromkeys([b for b in base if b])),
        "include_terms": [w for w in base if w] + ["contact", "services"],
        "exclude_terms": ["jobs", "careers", "course", "training", "directory", "listing"],
        "priority_pages_fast": ["contact", "contact-us", "about", "services"],
        "priority_pages_deep": ["team", "staff", "people", "locations", "booking", "appointments", "menu", "downloads"],
        "role_terms": ["owner", "manager", "director", "reception", "administrator"],
        "official_terms": ["contact", "services", "about"] + base,
    }

# ------------------------- HTTP/search -------------------------
def fetch(url, timeout=TIMEOUT):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None, f"HTTP {r.status_code}"
        return r, "ok"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

def clean_url(url):
    if not url:
        return ""
    url = str(url).strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")

def domain(url):
    try:
        return tldextract.extract(url).registered_domain.lower()
    except Exception:
        return urlparse(url).netloc.lower().replace("www.", "")

def ddg_search(query, max_results=8):
    # HTML endpoint; best-effort on Streamlit Cloud
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    r, status = fetch(url, timeout=14)
    log(f"DDG '{query[:80]}': {status}")
    results = []
    if not r:
        return results
    soup = BeautifulSoup(r.text, "lxml")
    for a in soup.select("a.result__a")[:max_results]:
        href = a.get("href") or ""
        title = norm_space(a.get_text(" "))
        # unwrap DDG redirect
        if "uddg=" in href:
            qs = urllib.parse.parse_qs(urlparse(href).query)
            href = qs.get("uddg", [href])[0]
        results.append({"url": clean_url(href), "title": title})
    return results

# ------------------------- Discovery -------------------------
def geocode(location):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": location, "format":"jsonv2", "limit":1, "addressdetails":1})
    r, status = fetch(url, timeout=15)
    log(f"Geocode {status}: {url}")
    if not r:
        return None
    data = r.json()
    return data[0] if data else None

def overpass_discover(lat, lon, radius_km, profile, max_n):
    terms = profile["entity_terms"]
    # Generic OSM query using common POI tags and name regex for custom terms
    name_regex = "|".join([re.escape(t) for t in terms[:8]])
    query = f"""
    [out:json][timeout:25];
    (
      node(around:{int(radius_km*1000)},{lat},{lon})[amenity~"school|college|university|kindergarten|restaurant|clinic|doctors|hospital"];
      way(around:{int(radius_km*1000)},{lat},{lon})[amenity~"school|college|university|kindergarten|restaurant|clinic|doctors|hospital"];
      node(around:{int(radius_km*1000)},{lat},{lon})[name~"{name_regex}",i];
      way(around:{int(radius_km*1000)},{lat},{lon})[name~"{name_regex}",i];
    ); out center tags {max_n};
    """
    endpoints = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.osm.ch/api/interpreter"]
    rows=[]
    for ep in endpoints:
        try:
            rr = requests.post(ep, data={"data": query}, headers={"User-Agent": UA}, timeout=32)
            log(f"Overpass POST {ep}: HTTP {rr.status_code}")
            if rr.status_code != 200: continue
            js = rr.json()
            for el in js.get("elements", [])[:max_n*3]:
                tags = el.get("tags", {})
                name = tags.get("name") or tags.get("operator") or ""
                if not name: continue
                rows.append({
                    "organization_name": name,
                    "sector": profile["sector"],
                    "address": tags.get("addr:full") or ", ".join([tags.get(k,"") for k in ["addr:housenumber","addr:street","addr:city"] if tags.get(k)]),
                    "city": tags.get("addr:city") or "",
                    "country": tags.get("addr:country") or "",
                    "lat": el.get("lat") or el.get("center",{}).get("lat"),
                    "lon": el.get("lon") or el.get("center",{}).get("lon"),
                    "website": tags.get("website") or tags.get("contact:website") or "",
                    "osm_phone": tags.get("phone") or tags.get("contact:phone") or "",
                    "source": "overpass",
                })
            if rows:
                log(f"Overpass {ep}: {len(rows)} candidates")
                break
        except Exception as e:
            log(f"Overpass error {ep}: {type(e).__name__}: {e}")
    return rows

def nominatim_text_discover(location, profile, max_n):
    rows=[]
    for term in profile["entity_terms"][:8]:
        q = f"{term} in {location}"
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q":q,"format":"jsonv2","limit":max(10, max_n//2),"addressdetails":1,"extratags":1})
        r,status=fetch(url,timeout=16)
        log(f"Nominatim '{q}': {status}")
        if not r: continue
        try: data=r.json()
        except Exception: data=[]
        for it in data:
            ext=it.get("extratags") or {}
            addr=it.get("address") or {}
            rows.append({
                "organization_name": it.get("name") or it.get("display_name","").split(',')[0],
                "sector": profile["sector"],
                "address": it.get("display_name",""),
                "city": addr.get("city") or addr.get("town") or addr.get("suburb") or "",
                "country": addr.get("country") or get_country_from_location(location),
                "lat": it.get("lat"), "lon": it.get("lon"),
                "website": ext.get("website") or ext.get("contact:website") or "",
                "osm_phone": ext.get("phone") or ext.get("contact:phone") or "",
                "source": "nominatim",
            })
    return rows

def is_false_positive_name(name, profile):
    n=name.lower()
    return any(x in n for x in profile.get("exclude_terms", []))

def discover(location, radius_km, profile, max_n):
    geo=geocode(location)
    rows=[]
    if geo:
        rows += overpass_discover(float(geo["lat"]), float(geo["lon"]), radius_km, profile, max_n)
    rows += nominatim_text_discover(location, profile, max_n)
    seen=set(); out=[]
    for r in rows:
        name=norm_space(r.get("organization_name"))
        if not name or is_false_positive_name(name, profile): continue
        key=(name.lower(), str(r.get("lat"))[:7], str(r.get("lon"))[:7])
        if key in seen: continue
        seen.add(key); out.append(r)
        if len(out)>=max_n: break
    return out

# ------------------------- Website resolution -------------------------
def tokenize_name(name):
    bad={"the","of","and","for","school","college","academy","primary","secondary","high","junior","pre","prep","campus","branch"}
    toks=re.findall(r"[a-z0-9]+", name.lower())
    return [t for t in toks if len(t)>2 and t not in bad]

def is_directory_url(url):
    d=domain(url); u=url.lower()
    return any(h in d or h in u for h in DIRECTORY_HINTS)

def local_tld_bonus(url, country):
    c=(country or "").lower()
    tlds=COUNTRY_TLDS.get(c, [])
    u=url.lower()
    return 25 if any(t in u for t in tlds) else 0

def content_probe(url):
    r,status=fetch(url,timeout=10)
    if not r: return {"ok":False,"status":status,"title":"","text":""}
    soup=BeautifulSoup(r.text[:250000],"lxml")
    title=norm_space(soup.title.get_text(" ") if soup.title else "")
    text=norm_space(soup.get_text(" "))[:6000]
    return {"ok":True,"status":"ok","title":title,"text":text}

def score_website_candidate(name, url, location, country, profile):
    url=clean_url(url); d=domain(url)
    if not d or d in GENERIC_BAD_DOMAINS: return -999, "rejected_bad_domain", None
    if is_directory_url(url): return 10, "directory_contact_source", None
    toks=tokenize_name(name); url_l=url.lower().replace('-', '').replace('.', '')
    score=0; reasons=[]
    # domain/name match
    matches=sum(1 for t in toks if t in url_l)
    score += matches*18
    if matches: reasons.append(f"domain_match_{matches}")
    score += local_tld_bonus(url,country)
    if local_tld_bonus(url,country): reasons.append("local_tld")
    # generic global penalty unless content proves it
    if d.endswith((".com",".org",".net")) and local_tld_bonus(url,country)==0 and (country or "").lower() not in ["united states","usa"]:
        score -= 20; reasons.append("generic_domain_penalty")
    probe=content_probe(url)
    if probe["ok"]:
        hay=(probe["title"]+" "+probe["text"]).lower()
        content_matches=sum(1 for t in toks if t in hay)
        score += content_matches*12
        if content_matches: reasons.append(f"content_match_{content_matches}")
        official_matches=sum(1 for t in profile.get("official_terms",[]) if t.lower() in hay)
        score += min(official_matches,5)*5
        if any(x.lower() in hay for x in profile.get("exclude_terms",[])):
            score -= 40; reasons.append("exclude_content")
        loc_toks=tokenize_name(location)
        if any(t in hay for t in loc_toks[:3]): score += 8; reasons.append("location_match")
    else:
        reasons.append("probe_failed")
    return score, ";".join(reasons), probe

def official_search_queries(name, location, profile):
    return [
        f'"{name}" official website',
        f'"{name}" "{location}"',
        f'"{name}" contact',
        f'"{name}" {profile["sector"]}',
    ]

def resolve_website(row, location, country, profile, extra_thorough=False, use_places=False):
    name=row.get("organization_name","")
    existing=clean_url(row.get("website",""))
    candidates=[]
    if existing: candidates.append({"url":existing,"source":"map"})
    qlist=official_search_queries(name, location, profile)
    if extra_thorough:
        qlist += [f'"{name}" phone', f'"{name}" admissions contact', f'"{name}" site:.{(country or "").lower()[:2]}']
    for q in qlist[:6]:
        for res in ddg_search(q, max_results=6):
            if res.get("url"):
                candidates.append({"url":res["url"],"source":"search","title":res.get("title","")})
    # de-dupe by domain/path root
    best=None; allc=[]; seen=set()
    for c in candidates:
        u=clean_url(c["url"]); d=domain(u)
        if not d or d in seen: continue
        seen.add(d)
        sc, reason, probe=score_website_candidate(name,u,location,country,profile)
        allc.append({"url":u,"score":sc,"reason":reason,"source":c.get("source")})
        if best is None or sc>best["score"]: best={"url":u,"score":sc,"reason":reason,"source":c.get("source")}
    if not best:
        return "", "not_found", "", ""
    cand_str=" | ".join([f'{x["url"]} ({x["score"]})' for x in sorted(allc,key=lambda x:x["score"],reverse=True)[:5]])
    if best["score"] >= 75:
        return best["url"], "verified_official", cand_str, best["reason"]
    if best["score"] >= 50:
        return best["url"], "likely_official", cand_str, best["reason"]
    if best["score"] >= 25 and not is_directory_url(best["url"]):
        return "", "candidate_only", cand_str, best["reason"]
    return "", "not_found", cand_str, best["reason"]

# ------------------------- Contact extraction -------------------------
EMAIL_RE=re.compile(r"[A-Z0-9._%+\-]+\s*(?:@|\[at\]|\(at\)| at )\s*[A-Z0-9.\-]+\s*(?:\.|\[dot\]|\(dot\)| dot )\s*[A-Z]{2,}", re.I)

def normalize_email(e):
    e=e.strip().lower()
    e=re.sub(r"\s*(\[at\]|\(at\)| at )\s*","@",e)
    e=re.sub(r"\s*(\[dot\]|\(dot\)| dot )\s*",".",e)
    e=re.sub(r"\s+","",e)
    return e

def extract_emails_from_text(text):
    out=[]
    for e in EMAIL_RE.findall(text or ""):
        em=normalize_email(e)
        if "@" in em and "." in em.split("@")[-1] and not any(b in em for b in ["example.com", "domain.com"]):
            out.append(em)
    return sorted(set(out))

def extract_phones_from_text(text, country_name=""):
    out=[]
    region=None
    if country_name:
        # common manual map; phonenumbers region codes
        mapping={"south africa":"ZA","kenya":"KE","nigeria":"NG","ghana":"GH","uganda":"UG","tanzania":"TZ","rwanda":"RW","united kingdom":"GB","united states":"US"}
        region=mapping.get(country_name.lower())
    raw=text or ""
    # include tel-like and broad numeric candidates
    candidates=re.findall(r"(?:\+?\d[\d\s().\-/]{6,}\d)", raw)
    for c in candidates:
        c=re.sub(r"[^+\d]","",c)
        if len(c)<7 or len(c)>16: continue
        try:
            num=phonenumbers.parse(c, region)
            if phonenumbers.is_valid_number(num):
                out.append(phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
        except Exception:
            continue
    return sorted(set(out))

def get_internal_links(base_url, soup, profile, deep=False):
    links=[]; base_dom=domain(base_url)
    keywords=profile["priority_pages_fast"] + (profile["priority_pages_deep"] if deep else [])
    for a in soup.find_all("a", href=True):
        href=a["href"].strip()
        full=urljoin(base_url,href)
        if domain(full)!=base_dom: continue
        label=(href+" "+a.get_text(" ")).lower()
        if any(k.lower() in label for k in keywords):
            links.append(full)
    # PDFs too
    pdfs=[u for u in links if u.lower().endswith(".pdf")]
    pages=[u for u in links if not u.lower().endswith(".pdf")]
    return list(dict.fromkeys(pages))[:8], list(dict.fromkeys(pdfs))[:4]

def extract_pdf_text(url):
    if PyPDF2 is None: return ""
    r,status=fetch(url,timeout=14)
    if not r: return ""
    if len(r.content)>3_000_000: return ""
    try:
        reader=PyPDF2.PdfReader(io.BytesIO(r.content))
        txt="\n".join([(p.extract_text() or "") for p in reader.pages[:5]])
        return txt
    except Exception:
        return ""

def scrape_contacts(row, location, country, profile, find_more=False):
    website=clean_url(row.get("website",""))
    result={"visible_emails":"","generic_emails":"","best_email":"","website_phone":"","pdf_emails":"","pdf_phone":"","contact_pages":"","scrape_status":"no_website"}
    if not website: return result
    r,status=fetch(website,timeout=TIMEOUT)
    if not r:
        result["scrape_status"]="scrape_failed"; return result
    soup=BeautifulSoup(r.text,"lxml")
    texts=[soup.get_text(" ")]
    pages,pdfs=get_internal_links(website,soup,profile,deep=find_more)
    # Always include likely contact page if not found
    for suffix in ["/contact", "/contact-us", "/admissions", "/about"]:
        pages.append(urljoin(website,suffix))
    pages=list(dict.fromkeys(pages))[: (10 if find_more else 5)]
    fetched_pages=[]
    for p in pages:
        rr,stt=fetch(p,timeout=TIMEOUT)
        if rr:
            fetched_pages.append(p)
            sp=BeautifulSoup(rr.text,"lxml")
            texts.append(sp.get_text(" "))
            # mailto/tel extraction
            mailtos=[a.get("href","") for a in sp.find_all("a", href=True) if a.get("href","").lower().startswith("mailto:")]
            tels=[a.get("href","") for a in sp.find_all("a", href=True) if a.get("href","").lower().startswith("tel:")]
            texts.extend([m.replace("mailto:","") for m in mailtos])
            texts.extend([t.replace("tel:","") for t in tels])
    pdf_text=""
    if find_more:
        # include PDFs from homepage/fetched pages and common suffixes
        for suffix in ["/prospectus.pdf", "/fees.pdf", "/admissions.pdf"]: pdfs.append(urljoin(website,suffix))
        for pdf in list(dict.fromkeys(pdfs))[:5]:
            pdf_text += "\n" + extract_pdf_text(pdf)
    all_text="\n".join(texts)
    emails=extract_emails_from_text(all_text)
    phones=extract_phones_from_text(all_text,country)
    pdf_emails=extract_emails_from_text(pdf_text)
    pdf_phones=extract_phones_from_text(pdf_text,country)
    generic=[e for e in emails+pdf_emails if any(x in e.split('@')[0] for x in ["info","admin","admission","contact","office","enquir","reception"])]
    best=(generic or emails or pdf_emails or [""])[0]
    result.update({
        "visible_emails":"; ".join(emails),
        "generic_emails":"; ".join(sorted(set(generic))),
        "pdf_emails":"; ".join(pdf_emails),
        "best_email":best,
        "website_phone":"; ".join(phones[:5]),
        "pdf_phone":"; ".join(pdf_phones[:5]),
        "best_phone": (phones+pdf_phones+[row.get("osm_phone","") or ""])[0],
        "contact_pages":"; ".join(fetched_pages[:6]),
        "scrape_status":"scraped"
    })
    return result

# ------------------------- UI -------------------------
st.title("Prospect Discovery Engine")
st.caption("Find prospects by sector and location, then enrich with websites, emails, phones, and contact sources.")

mode = st.radio("Prospect type", ["Schools (optimized)", "Custom search"], horizontal=True)
if mode == "Custom search":
    custom_term = st.text_input("What are you looking for?", value="physical therapists")
else:
    custom_term = "schools"
location = st.text_input("Location", value="Cape Town, Western Cape, South Africa")

profile = build_profile(mode, custom_term)
with st.expander("Search profile", expanded=False):
    st.json(profile)

with st.sidebar:
    st.header("Advanced settings")
    radius = st.slider("Search radius (km)", 2, 100, 20)
    max_n = st.slider("Maximum prospects", 10, 200, 50, step=10)
    speed_label = st.select_slider("Processing speed", options=["Safe", "Balanced", "Fast"], value="Balanced")
    workers = {"Safe":2,"Balanced":5,"Fast":10}[speed_label]
    search_depth = st.radio("Search depth", ["Normal", "Extra thorough"], index=0)
    find_more_contacts = st.checkbox("Find more contact details when missing", value=True)
    use_places = st.checkbox("Use Google Places for unresolved websites (optional)", value=False)
    show_debug = st.checkbox("Show diagnostics", value=True)

p1 = st.empty(); p2 = st.empty(); p3 = st.empty()
bar1 = st.progress(0, text="Discovery")
bar2 = st.progress(0, text="Website resolution")
bar3 = st.progress(0, text="Contact enrichment")

run = st.button("Find prospects", type="primary")

if run:
    st.session_state.debug=[]; st.session_state.timing={}
    t0=time.time(); country=get_country_from_location(location)
    bar1.progress(5, text="Finding prospects from map/search sources…")
    candidates=discover(location, radius, profile, max_n)
    bar1.progress(100, text=f"Discovery complete: {len(candidates)} prospects")
    st.session_state.timing["discovery_seconds"] = round(time.time()-t0,2)

    t1=time.time(); bar2.progress(5, text="Finding and validating official websites…")
    # website resolution parallel
    def wtask(r):
        ctry=r.get("country") or country
        url,status,cands,reason=resolve_website(r, location, ctry, profile, extra_thorough=(search_depth=="Extra thorough"), use_places=use_places)
        r=dict(r)
        raw=r.get("website","")
        r["map_website"] = raw
        r["website"] = url or (raw if status in ["verified_official","likely_official"] else "")
        r["official_website_status"] = status
        r["website_candidates"] = cands
        r["website_resolution_reason"] = reason
        return r
    rows=[]
    total=max(len(candidates),1); done=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs=[ex.submit(wtask,r) for r in candidates]
        for fut in concurrent.futures.as_completed(futs):
            try: rows.append(fut.result())
            except Exception as e: log(f"website task error: {type(e).__name__}: {e}")
            done+=1; bar2.progress(min(100,int(done/total*100)), text=f"Website resolution: {done}/{total}")
    st.session_state.timing["website_resolution_seconds"] = round(time.time()-t1,2)

    t2=time.time(); bar3.progress(5, text="Scraping contact details…")
    def etask(r):
        deep = (search_depth=="Extra thorough")
        # conditional deep: first standard; if missing phone/email, rerun deeper on extra pages/PDFs
        base=scrape_contacts(r, location, r.get("country") or country, profile, find_more=deep)
        if find_more_contacts and (not base.get("best_email") or not base.get("best_phone")) and r.get("website"):
            deepres=scrape_contacts(r, location, r.get("country") or country, profile, find_more=True)
            for k,v in deepres.items():
                if v and (not base.get(k) or k in ["visible_emails","generic_emails","website_phone","pdf_emails","pdf_phone","contact_pages"]):
                    base[k]=v
        rr=dict(r); rr.update(base)
        # merge best phone fallback
        if not rr.get("best_phone"):
            rr["best_phone"] = rr.get("website_phone") or rr.get("pdf_phone") or rr.get("osm_phone") or ""
        rr["phone_source"] = "website" if rr.get("website_phone") else ("pdf" if rr.get("pdf_phone") else ("osm" if rr.get("osm_phone") else ""))
        return rr
    enriched=[]; done=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs=[ex.submit(etask,r) for r in rows]
        for fut in concurrent.futures.as_completed(futs):
            try: enriched.append(fut.result())
            except Exception as e: log(f"enrichment task error: {type(e).__name__}: {e}")
            done+=1; bar3.progress(min(100,int(done/total*100)), text=f"Contact enrichment: {done}/{total}")
    st.session_state.timing["enrichment_seconds"] = round(time.time()-t2,2)
    st.session_state.timing["total_seconds"] = round(time.time()-t0,2)
    st.session_state.timing["search_depth"] = search_depth
    st.session_state.timing["processing_speed"] = speed_label
    st.session_state.prospects = pd.DataFrame(enriched)

if st.session_state.prospects is not None:
    df=st.session_state.prospects.copy()
    preferred=["organization_name","sector","website","official_website_status","best_email","visible_emails","generic_emails","best_phone","website_phone","pdf_phone","osm_phone","phone_source","contact_pages","website_candidates","address","source","scrape_status"]
    cols=[c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
    st.subheader("Prospects")
    st.dataframe(df[cols], use_container_width=True, height=420)
    ts=datetime.now().strftime("%Y%m%d_%H%M")
    fname=f"prospect_discovery_{slug(profile['sector'])}_{slug(location)}_{ts}"
    csv=df[cols].to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV", data=csv, file_name=f"{fname}.csv", mime="text/csv")
    out=io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df[cols].to_excel(writer,index=False,sheet_name="Prospects")
    st.download_button("Download Excel", data=out.getvalue(), file_name=f"{fname}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    c1,c2,c3,c4=st.columns(4)
    c1.metric("Prospects", len(df))
    c2.metric("Websites", int(df.get("website",pd.Series(dtype=str)).astype(str).ne("").sum()))
    c3.metric("Best emails", int(df.get("best_email",pd.Series(dtype=str)).astype(str).ne("").sum()))
    c4.metric("Best phones", int(df.get("best_phone",pd.Series(dtype=str)).astype(str).ne("").sum()))

if show_debug:
    with st.expander("Diagnostic details", expanded=False):
        st.write("Timing")
        st.json(st.session_state.timing)
        st.write("Debug log")
        st.text("\n".join(st.session_state.debug[-250:]))
