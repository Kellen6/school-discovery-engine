import streamlit as st
import pandas as pd
import requests, re, io, json, time, math, hashlib
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urlparse, urljoin, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
import phonenumbers

st.set_page_config(page_title="Prospect Discovery Engine", layout="wide")

UA = "ProspectDiscoveryEngine/40.1 (educational prospect research; contact: user@example.com)"
REQ_TIMEOUT = 10

SUSPICIOUS_GENERIC_DOMAINS = {
    "highschool.org", "school.org", "college.com", "academy.com", "education.com",
    "welcome.org", "valley.net", "torah.com", "nairobi.com", "olm.com",
    "palmolive.com", "palmolive.org", "bps.com", "msps.com", "diocesan.com"
}
GENERIC_DOMAIN_WORDS = {"school", "college", "academy", "education", "highschool", "primary", "secondary", "welcome", "valley", "torah"}
BAD_PROSPECT_TERMS = ["driving school", "testing yard", "licensing", "parking", "residence", "hostel", "student accommodation", "apartment", "estate agent", "training centre"]
SCHOOL_TERMS = ["school", "college", "academy", "primary", "secondary", "prep", "pre-prep", "high school", "campus", "montessori", "educare", "kindergarten"]
SCHOOL_PAGE_HINTS = ["contact", "contact-us", "admissions", "admission", "apply", "staff", "team", "leadership", "about", "campus", "fees", "prospectus", "newsletter"]
DEFAULT_PAGE_HINTS = ["contact", "contact-us", "about", "team", "staff", "services", "locations", "appointments", "booking"]

COUNTRY_HINTS = {
    "south africa": {"tlds": [".co.za", ".org.za", ".ac.za", ".school.za", ".za"], "region": "ZA"},
    "kenya": {"tlds": [".ac.ke", ".co.ke", ".or.ke", ".sc.ke", ".ke"], "region": "KE"},
    "nigeria": {"tlds": [".edu.ng", ".sch.ng", ".org.ng", ".com.ng", ".ng"], "region": "NG"},
    "united kingdom": {"tlds": [".sch.uk", ".ac.uk", ".org.uk", ".co.uk", ".uk"], "region": "GB"},
    "ghana": {"tlds": [".edu.gh", ".org.gh", ".com.gh", ".gh"], "region": "GH"},
    "rwanda": {"tlds": [".ac.rw", ".co.rw", ".org.rw", ".rw"], "region": "RW"},
    "uganda": {"tlds": [".ac.ug", ".co.ug", ".org.ug", ".ug"], "region": "UG"},
    "tanzania": {"tlds": [".ac.tz", ".co.tz", ".or.tz", ".tz"], "region": "TZ"},
}

if "debug" not in st.session_state: st.session_state.debug=[]
if "prospects" not in st.session_state: st.session_state.prospects=None
if "timing" not in st.session_state: st.session_state.timing={}
if "last_key" not in st.session_state: st.session_state.last_key=None
if "geocode_cache" not in st.session_state: st.session_state.geocode_cache={}

def log(msg):
    st.session_state.debug.append(str(msg))

def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()

def tokens(s):
    return [t for t in norm(s).split() if len(t)>1 and t not in {"the","and","of","for","in","at","to","a","an","school","college","academy","primary","secondary","high"}]

def root_domain(url):
    try:
        h=urlparse(url if str(url).startswith("http") else "https://"+str(url)).netloc.lower()
        h=h.split('@')[-1].split(':')[0]
        if h.startswith('www.'): h=h[4:]
        return h
    except Exception: return ""

def canonical_url(url):
    if not url: return ""
    url=str(url).strip()
    if not url: return ""
    if not url.startswith("http"):
        url="https://"+url
    p=urlparse(url)
    if not p.netloc: return ""
    return f"{p.scheme}://{p.netloc}".rstrip('/')

def country_info(country):
    c=norm(country)
    for k,v in COUNTRY_HINTS.items():
        if k in c: return v
    # derive ISO-ish fallback for phonenumbers only where unknown; ZA default not used for validation if absent
    return {"tlds": [], "region": None}

def infer_country_from_location(location):
    loc=norm(location)
    for c in COUNTRY_HINTS:
        if c in loc: return c.title()
    parts=[p.strip() for p in str(location).split(',') if p.strip()]
    return parts[-1] if parts else ""

def http_get(url, timeout=REQ_TIMEOUT):
    try:
        return requests.get(url, headers={"User-Agent":UA}, timeout=timeout, allow_redirects=True)
    except Exception:
        return None

def fetch_text(url, timeout=REQ_TIMEOUT):
    r=http_get(url, timeout)
    if not r or r.status_code>=400: return "", "", ""
    ct=r.headers.get("content-type","").lower()
    if "text/html" not in ct and "application/xhtml" not in ct and ct:
        return "", "", r.url
    soup=BeautifulSoup(r.text, "lxml")
    title=(soup.title.string if soup.title and soup.title.string else "")
    for tag in soup(["script","style","noscript"]): tag.decompose()
    text=soup.get_text(" ", strip=True)
    return title, text[:200000], r.url

def stage_record(stage, status, **kwargs):
    try:
        if "stage_diagnostics" not in st.session_state:
            st.session_state.stage_diagnostics=[]
        rec={"stage":stage,"status":status,**kwargs}
        st.session_state.stage_diagnostics.append(rec)
    except Exception:
        pass


def geocode(location, manual_lat=None, manual_lon=None):
    """Return lat/lon/country with resilient fallbacks.
    Public Nominatim can return 403/rate-limit from Streamlit Cloud, so we:
    1) use manual coordinates if supplied,
    2) use session cache for repeated locations,
    3) try Nominatim with a proper User-Agent,
    4) fall back to Photon/Komoot geocoder,
    5) return a clear failure diagnostic instead of silently returning 0 prospects.
    """
    loc_key=norm(location)
    if manual_lat is not None and manual_lon is not None:
        try:
            lat=float(manual_lat); lon=float(manual_lon)
            country=infer_country_from_location(location)
            res={"lat":lat,"lon":lon,"display":f"Manual coordinates for {location}","country":country,"provider":"manual"}
            st.session_state.geocode_cache[loc_key]=res
            log(f"Geocode manual: {lat},{lon} for {location}")
            stage_record("geocoding","success",provider="manual",lat=lat,lon=lon,country=country)
            return res
        except Exception as e:
            log(f"Manual geocode invalid: {type(e).__name__}: {e}")
            stage_record("geocoding","failed",provider="manual",error=str(e))

    if loc_key in st.session_state.geocode_cache:
        res=st.session_state.geocode_cache[loc_key]
        log(f"Geocode cache hit: {location} -> {res.get('lat')},{res.get('lon')}")
        stage_record("geocoding","success",provider="cache",lat=res.get("lat"),lon=res.get("lon"),country=res.get("country"))
        return res

    # Nominatim primary
    url=f"https://nominatim.openstreetmap.org/search?q={quote_plus(location)}&format=jsonv2&limit=1&addressdetails=1"
    try:
        t=time.time()
        r=requests.get(url, headers={"User-Agent":UA,"Accept":"application/json"}, timeout=20)
        log(f"Geocode HTTP {r.status_code}: {url}")
        stage_record("geocoding_provider","attempted",provider="nominatim",http_status=r.status_code,elapsed_seconds=round(time.time()-t,2))
        if r.ok:
            data=r.json()
            if data:
                d=data[0]
                res={"lat":float(d["lat"]),"lon":float(d["lon"]),"display":d.get("display_name",""),"country":(d.get("address") or {}).get("country", infer_country_from_location(location)),"provider":"nominatim"}
                st.session_state.geocode_cache[loc_key]=res
                stage_record("geocoding","success",provider="nominatim",lat=res["lat"],lon=res["lon"],country=res.get("country"))
                return res
            else:
                log("Nominatim returned no geocode results")
        else:
            # 403 means policy/rate-limit/shared hosted IP issue; continue to fallback.
            log(f"Nominatim geocode failed HTTP {r.status_code}; trying fallback geocoder")
    except Exception as e:
        log(f"Nominatim geocode exception: {type(e).__name__}: {e}")
        stage_record("geocoding_provider","failed",provider="nominatim",error=f"{type(e).__name__}: {e}")

    # Photon fallback
    purl=f"https://photon.komoot.io/api/?q={quote_plus(location)}&limit=1"
    try:
        t=time.time()
        r=requests.get(purl, headers={"User-Agent":UA,"Accept":"application/json"}, timeout=20)
        log(f"Photon geocode HTTP {r.status_code}: {purl}")
        stage_record("geocoding_provider","attempted",provider="photon",http_status=r.status_code,elapsed_seconds=round(time.time()-t,2))
        if r.ok:
            data=r.json()
            feats=data.get("features") or []
            if feats:
                f=feats[0]
                lon,lat=f.get("geometry",{}).get("coordinates",[None,None])[:2]
                props=f.get("properties") or {}
                country=props.get("country") or infer_country_from_location(location)
                res={"lat":float(lat),"lon":float(lon),"display":props.get("name") or location,"country":country,"provider":"photon"}
                st.session_state.geocode_cache[loc_key]=res
                stage_record("geocoding","success",provider="photon",lat=res["lat"],lon=res["lon"],country=res.get("country"))
                return res
    except Exception as e:
        log(f"Photon geocode exception: {type(e).__name__}: {e}")
        stage_record("geocoding_provider","failed",provider="photon",error=f"{type(e).__name__}: {e}")

    stage_record("geocoding","failed",provider="all",location=location,error="all geocoders failed")
    return None

def overpass_schools(lat,lon,radius_km,limit):
    radius=int(radius_km*1000)
    query=f"""
[out:json][timeout:25];
(
  node[amenity~"school|college|university|kindergarten"](around:{radius},{lat},{lon});
  way[amenity~"school|college|university|kindergarten"](around:{radius},{lat},{lon});
  relation[amenity~"school|college|university|kindergarten"](around:{radius},{lat},{lon});
  node[building~"school|college|university"](around:{radius},{lat},{lon});
  way[building~"school|college|university"](around:{radius},{lat},{lon});
);
out center tags {min(limit*3,300)};
"""
    endpoints=["https://overpass-api.de/api/interpreter","https://overpass.kumi.systems/api/interpreter","https://overpass.osm.ch/api/interpreter"]
    for ep in endpoints:
        try:
            r=requests.post(ep, data={"data":query}, headers={"User-Agent":UA}, timeout=30)
            log(f"Overpass POST {ep}: HTTP {r.status_code}")
            if r.ok:
                elems=r.json().get("elements",[])
                log(f"Overpass {ep}: {len(elems)} candidates")
                rows=[]
                for e in elems:
                    tags=e.get("tags",{})
                    name=tags.get("name") or tags.get("operator")
                    if not name: continue
                    lat=e.get("lat") or e.get("center",{}).get("lat")
                    lon=e.get("lon") or e.get("center",{}).get("lon")
                    rows.append({
                        "prospect_name":name,
                        "address":tags.get("addr:full") or ", ".join([tags.get(k,"") for k in ["addr:housenumber","addr:street","addr:city"] if tags.get(k)]),
                        "city":tags.get("addr:city",""),"country":tags.get("addr:country",""),
                        "lat":lat,"lon":lon,"website":canonical_url(tags.get("website") or tags.get("contact:website") or ""),
                        "osm_phone":tags.get("phone") or tags.get("contact:phone") or "",
                        "source":"Overpass"
                    })
                return rows[:limit]
        except Exception as e:
            log(f"Overpass error {ep}: {type(e).__name__}: {e}")
    return []

def nominatim_search(term,location,limit):
    url=f"https://nominatim.openstreetmap.org/search?q={quote_plus(term+' in '+location)}&format=jsonv2&limit={limit}&addressdetails=1&extratags=1"
    try:
        r=requests.get(url, headers={"User-Agent":UA}, timeout=20)
        log(f"Nominatim '{term} in {location}': HTTP {r.status_code}")
        out=[]
        if r.ok:
            for d in r.json():
                ex=d.get("extratags") or {}
                addr=d.get("address") or {}
                name=d.get("name") or d.get("display_name","").split(',')[0]
                out.append({"prospect_name":name,"address":d.get("display_name",""),"city":addr.get("city") or addr.get("town") or addr.get("suburb") or "","country":addr.get("country",""),"lat":d.get("lat"),"lon":d.get("lon"),"website":canonical_url(ex.get("website") or ex.get("contact:website") or ""),"osm_phone":ex.get("phone") or ex.get("contact:phone") or "","source":"Nominatim"})
        return out
    except Exception as e:
        log(f"Nominatim error {term}: {e}")
        return []

def is_false_positive(name, sector):
    n=norm(name)
    if any(b in n for b in BAD_PROSPECT_TERMS): return True
    if sector == "Schools (optimized)":
        # Keep academy/college/prep/etc. Do not require 'school' in name.
        if "driver" in n or "testing" in n: return True
    return False

def dedupe(rows):
    seen=set(); out=[]; dup=0
    for r in rows:
        key=norm(r.get("prospect_name"))+"|"+norm(r.get("address"))[:80]
        if key in seen:
            dup+=1; continue
        seen.add(key); out.append(r)
    return out, dup

def discover(sector, custom_term, location, radius, limit, manual_lat=None, manual_lon=None):
    """Discover candidates. This function must NEVER return zero simply because websites fail.
    Website validation happens later and only affects website/status fields.
    """
    g=geocode(location, manual_lat=manual_lat, manual_lon=manual_lon)
    if not g:
        return [], {"geocoded":False,"raw_found":0,"no_name":0,"false_positive_removed":0,"duplicates_removed":0,"retained":0,"note":"geocode failed"}
    country=g.get("country") or infer_country_from_location(location)
    if sector == "Schools (optimized)":
        terms=["school","private school","international school","primary school","secondary school","high school","college","academy","preparatory school"]
    else:
        terms=expand_custom_terms(custom_term.strip())

    rows=[]
    overpass_count=0
    if sector == "Schools (optimized)":
        op=overpass_schools(g["lat"],g["lon"],radius,max(limit*2,80))
        overpass_count=len(op)
        rows.extend(op)

    # Always supplement with text/place search. Do not stop early if Overpass returns fewer than cap.
    for t in terms:
        if len(rows) >= max(limit*2,80):
            break
        rows.extend(nominatim_search(t, location, max(20, limit//2)))
        time.sleep(0.15)

    # Emergency broad fallback if upstream APIs behave oddly.
    if not rows and sector == "Schools (optimized)":
        for t in ["school", "college", "academy"]:
            rows.extend(nominatim_search(t, location, 50))
            time.sleep(0.15)

    for r in rows:
        if not r.get("country"):
            r["country"]=country

    raw_total=len(rows)
    no_name=sum(1 for r in rows if not str(r.get("prospect_name","")).strip())
    rows=[r for r in rows if str(r.get("prospect_name","")).strip()]

    # Only remove obvious non-prospects. Do NOT require website or official-site validation here.
    before_filter=len(rows)
    kept=[]; removed=[]
    for r in rows:
        if is_false_positive(r.get("prospect_name",""), sector):
            removed.append(r)
        else:
            kept.append(r)
    rows=kept
    false_removed=len(removed)

    rows, dup=dedupe(rows)

    # If filtering/dedupe accidentally collapses everything, recover unfiltered named rows.
    if not rows and raw_total:
        rows=[r for r in kept or [x for x in rows if str(x.get("prospect_name","")).strip()]]

    retained=rows[:limit]
    return retained, {
        "geocoded":True,
        "overpass_candidates":overpass_count,
        "raw_found":raw_total,
        "no_name":no_name,
        "false_positive_removed":false_removed,
        "duplicates_removed":dup,
        "retained":len(retained),
        "note":"websites are optional and never used to remove prospects"
    }

def expand_custom_terms(term):
    t=norm(term)
    terms=[term]
    if "pizza" in t:
        terms += ["pizzeria", "pizza restaurant", "pizza takeaway", "restaurant"]
    elif "physio" in t or "physical therapist" in t:
        terms += ["physiotherapist", "physiotherapy clinic", "physical therapy clinic", "rehabilitation clinic", "sports physio"]
    else:
        if not t.endswith('s'): terms.append(term+'s')
        if t.endswith('s'): terms.append(term[:-1])
        terms += [term + " near me", term + " business"]
    return list(dict.fromkeys([x for x in terms if x.strip()]))[:8]

def ddg_results(query, max_results=8):
    url="https://duckduckgo.com/html/?q="+quote_plus(query)
    r=http_get(url, timeout=12)
    results=[]
    if not r or not r.ok: return results
    soup=BeautifulSoup(r.text,"lxml")
    for a in soup.select("a.result__a")[:max_results]:
        href=a.get("href","")
        txt=a.get_text(" ", strip=True)
        if "uddg=" in href:
            m=re.search(r"uddg=([^&]+)", href)
            if m: href=unquote(m.group(1))
        results.append({"title":txt,"url":href})
    return results

def likely_directory(url):
    d=root_domain(url)
    path=urlparse(url).path.lower()
    directory_terms=["directory","schools","listing","waze","facebook","linkedin","wikipedia","primaryschool.co.ke","kenyaprimaryschools","schoolguide","brabys","saschools","zaubee","cybo","africabizinfo"]
    return any(x in d or x in path for x in directory_terms)

def suspicious_domain_for_name(domain, name, country):
    d=domain.lower().strip()
    if d in SUSPICIOUS_GENERIC_DOMAINS: return True
    label=d.split('.')[0]
    ntoks=tokens(name)
    # Acronym/short guesses like am.net, hi.co.za, hc.co.za should never be accepted without content validation.
    if len(label) <= 3 and label not in [re.sub(r'[^a-z0-9]','',t) for t in ntoks]:
        return True
    if label in GENERIC_DOMAIN_WORDS: return True
    # brand collision examples: palmolive for Palm Olive Academy
    compact=re.sub(r"[^a-z0-9]","", norm(name))
    if label and label in {"palmolive", "nairobi", "diocesan"} and label not in compact:
        return True
    return False

def score_website_candidate(name, country, location, url):
    url=canonical_url(url)
    if not url: return {"score":0,"status":"not_found","reason":"blank"}
    domain=root_domain(url)
    if likely_directory(url):
        return {"score":15,"status":"directory_only","reason":"directory/listing page"}
    if suspicious_domain_for_name(domain,name,country):
        # Allow later only if page content proves match.
        base_penalty=35
    else:
        base_penalty=0
    title,text,final=fetch_text(url, timeout=8)
    sample=norm((title or "")+" "+(text or "")[:5000])
    ntoks=tokens(name)
    matched=sum(1 for t in ntoks if t in sample or t in norm(domain))
    score=0
    if ntoks:
        score += min(45, int(45*matched/max(1,len(ntoks))))
    edu_terms=["school","college","academy","education","learners","students","admissions","principal","campus","primary","secondary"]
    score += min(20, sum(4 for e in edu_terms if e in sample))
    c=norm(country)
    loc=norm(location)
    if c and c in sample: score += 10
    if any(part and norm(part) in sample for part in str(location).split(',')[:2]): score += 8
    info=country_info(country)
    if any(domain.endswith(tld.lstrip('.')) or domain.endswith(tld) for tld in info.get("tlds",[])): score += 12
    if domain.endswith(('.com','.org','.net')) and info.get("tlds") and not any(domain.endswith(tld.lstrip('.')) for tld in info.get("tlds",[])):
        score -= 8
    score -= base_penalty
    if final: url=canonical_url(final)
    if score >= 70:
        status="verified official"
    elif score >= 50:
        status="likely official"
    elif score >= 20:
        status="candidate only"
    else:
        status="rejected false positive"
    reason=f"title='{title[:80]}'; matched_tokens={matched}/{len(ntoks)}; domain={domain}; score={score}"
    return {"score":score,"status":status,"reason":reason,"url":url}

def domain_guesses(name,country):
    clean=''.join(tokens(name))
    hyphen='-'.join(tokens(name))
    guesses=[]
    info=country_info(country)
    for base in [clean, hyphen]:
        if len(base)<4: continue
        for tld in info.get("tlds",[])[:4]:
            guesses.append(f"https://www.{base}{tld}")
        # only limited generic guesses; must validate by content
        for tld in [".org", ".com"]:
            guesses.append(f"https://www.{base}{tld}")
    return guesses[:10]

def resolve_website(row, location, thorough=False):
    name=row.get("prospect_name","")
    country=row.get("country") or infer_country_from_location(location)
    existing=row.get("website")
    candidates=[]
    if existing: candidates.append(existing)
    queries=[f'"{name}" official website {location}', f'"{name}" school {location}', f'"{name}" contact {location}']
    if thorough:
        queries += [f'"{name}" admissions', f'"{name}" phone email']
    for q in queries[:4 if thorough else 2]:
        for res in ddg_results(q, max_results=5):
            u=res.get("url","")
            if u and u.startswith("http"): candidates.append(u)
        time.sleep(0.1)
    # domain guesses last; do not trust without content validation
    candidates += domain_guesses(name,country)
    scored=[]
    seen=set()
    for u in candidates:
        cu=canonical_url(u)
        if not cu or root_domain(cu) in seen: continue
        seen.add(root_domain(cu))
        scored.append(score_website_candidate(name,country,location,cu))
    scored=sorted(scored, key=lambda x:x["score"], reverse=True)
    official=[s for s in scored if s["status"] in {"verified official","likely official"}]
    if official:
        best=official[0]
        return best["url"], best["status"], best["reason"], "; ".join([s["url"] for s in scored[:5]])
    # no official, retain candidates separately
    best=scored[0] if scored else {"status":"not_found","reason":"no candidates","url":""}
    return "", best["status"], best["reason"], "; ".join([s.get("url","") for s in scored[:5]])

def extract_contacts_from_html(url, country, sector):
    emails=set(); phones=set(); checked=[]
    if not url: return emails, phones, checked
    pages=[url]
    title,text,final=fetch_text(url, timeout=10)
    if final: url=canonical_url(final); pages=[url]
    # parse links from homepage
    r=http_get(url, timeout=10)
    hints=SCHOOL_PAGE_HINTS if sector=="Schools (optimized)" else DEFAULT_PAGE_HINTS
    if r and r.ok:
        soup=BeautifulSoup(r.text,"lxml")
        for a in soup.find_all('a', href=True):
            href=a.get('href','')
            label=(a.get_text(' ',strip=True)+' '+href).lower()
            if href.startswith('mailto:'):
                emails.add(href.split(':',1)[1].split('?')[0].strip())
            if href.startswith('tel:'):
                phones.add(href.split(':',1)[1].strip())
            if any(h in label for h in hints):
                pages.append(urljoin(url, href))
    pages=list(dict.fromkeys(pages))[:8]
    region=country_info(country).get("region")
    for p in pages:
        try:
            rr=http_get(p, timeout=10)
            if not rr or rr.status_code>=400: continue
            checked.append(p)
            soup=BeautifulSoup(rr.text,"lxml")
            for a in soup.find_all('a', href=True):
                href=a.get('href','')
                if href.startswith('mailto:'):
                    emails.add(href.split(':',1)[1].split('?')[0].strip())
                if href.startswith('tel:'):
                    phones.add(href.split(':',1)[1].strip())
            txt=soup.get_text(" ", strip=True)
            for e in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", txt):
                if not any(b in e.lower() for b in ["example.com","email.com"]): emails.add(e.strip('.,;:'))
            # obfuscated emails
            ob=re.sub(r"\s*(\[at\]|\(at\)| at )\s*", "@", txt, flags=re.I)
            ob=re.sub(r"\s*(\[dot\]|\(dot\)| dot )\s*", ".", ob, flags=re.I)
            for e in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", ob): emails.add(e.strip('.,;:'))
            # phone candidates
            for m in re.findall(r"(?:\+\d{1,3}[\s().-]*)?(?:\(?0?\d{2,4}\)?[\s().-]*)?\d{3,4}[\s().-]*\d{3,4}", txt):
                val=m.strip()
                try:
                    nums=phonenumbers.PhoneNumberMatcher(val, region or None)
                    for n in nums:
                        if phonenumbers.is_valid_number(n.number):
                            phones.add(phonenumbers.format_number(n.number, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
                except Exception:
                    pass
        except Exception:
            continue
    return emails, phones, checked

def search_contacts(name, location, country):
    emails=set(); phones=set(); pages=[]
    qs=[f'"{name}" "contact" {location}', f'"{name}" "phone" {location}', f'"{name}" "admissions" {location}', f'"{name}" "+27" OR "+254"']
    for q in qs[:3]:
        for res in ddg_results(q, max_results=4):
            u=res.get('url','')
            if not u or likely_directory(u):
                # still extract snippet/title? no snippets in html mode
                pass
            title,text,final=fetch_text(u, timeout=8)
            if not text: continue
            pages.append(final or u)
            for e in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text): emails.add(e.strip('.,;:'))
            region=country_info(country).get("region")
            for n in phonenumbers.PhoneNumberMatcher(text[:50000], region or None):
                if phonenumbers.is_valid_number(n.number): phones.add(phonenumbers.format_number(n.number, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
        time.sleep(0.1)
    return emails, phones, pages[:5]

def enrich_row(row, location, sector, thorough=False, contact_fallback=True):
    country=row.get("country") or infer_country_from_location(location)
    row=dict(row)
    site,status,reason,cands=resolve_website(row, location, thorough=thorough)
    # Preserve original website if valid, but never drop row if missing.
    row["website"]=site or row.get("website","")
    row["official_website_status"]=status if site else ("not found" if status in ["rejected false positive","not_found"] else status)
    row["website_validation_reason"]=reason
    row["website_candidates"]=cands
    emails=set(); phones=set(); pages=[]
    if row.get("website"):
        e,p,pages=extract_contacts_from_html(row["website"], country, sector)
        emails |= e; phones |= p
    search_e=set(); search_p=set(); search_pages=[]
    if contact_fallback and (not emails or not phones):
        search_e, search_p, search_pages=search_contacts(row.get("prospect_name",""), location, country)
        if not emails: emails |= search_e
        if not phones: phones |= search_p
    if row.get("osm_phone"):
        phones.add(row.get("osm_phone"))
    row["visible_emails"]="; ".join(sorted(emails))
    row["search_emails"]="; ".join(sorted(search_e))
    row["best_email"]=sorted(emails)[0] if emails else ""
    row["website_phone"]="; ".join(sorted(phones))
    row["search_phone"]="; ".join(sorted(search_p))
    row["best_phone"]=sorted(phones)[0] if phones else ""
    row["contact_pages_checked"]="; ".join((pages or [])+(search_pages or []))
    row["enrichment_status"]="scraped" if row.get("website") else "no_website"
    return row

def to_excel_bytes(df):
    buf=io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer,index=False,sheet_name="Prospects")
    return buf.getvalue()

def safe_filename(sector,location):
    stamp=time.strftime("%Y%m%d_%H%M")
    base=re.sub(r"[^a-z0-9]+","_", f"prospect_discovery_{sector}_{location}".lower()).strip('_')[:90]
    return f"{base}_{stamp}"

st.title("Prospect Discovery Engine")
st.caption("Find prospects by sector/location, resolve official websites, and enrich contact details.")

col1,col2=st.columns([1,2])
with col1:
    sector=st.radio("Prospect type", ["Schools (optimized)", "Custom search"], index=0)
with col2:
    custom_term=""
    if sector=="Custom search":
        custom_term=st.text_input("What are you looking for?", "pizza restaurants")
    location=st.text_input("Location", "Cape Town, Western Cape, South Africa")

colA,colB,colC=st.columns(3)
with colA: radius=st.slider("Search radius (km)", 1, 100, 30)
with colB: limit=st.slider("Maximum prospects", 10, 200, 50, step=10)
with colC: run=st.button("Find prospects", type="primary")

with st.sidebar:
    st.header("Advanced settings")
    speed=st.select_slider("Processing speed", options=["Slow", "Balanced", "Fast"], value="Balanced")
    thorough=st.checkbox("Extra thorough website search", value=False)
    contact_fallback=st.checkbox("Find more contact details when missing", value=True)
    use_manual_coords=st.checkbox("Use manual coordinates if geocoding is blocked", value=False)
    if use_manual_coords:
        manual_lat=st.number_input("Latitude", value=-33.9288, format="%.6f")
        manual_lon=st.number_input("Longitude", value=18.4172, format="%.6f")
    else:
        manual_lat=None
        manual_lon=None
    show_debug=st.checkbox("Show diagnostics", value=True)
workers={"Slow":2,"Balanced":5,"Fast":10}[speed]

key=hashlib.md5(json.dumps({"sector":sector,"custom":custom_term,"loc":location,"r":radius,"limit":limit,"thorough":thorough,"fallback":contact_fallback,"speed":speed,"manual_lat":manual_lat,"manual_lon":manual_lon},sort_keys=True).encode()).hexdigest()

if run:
    st.session_state.debug=[]
    st.session_state.stage_diagnostics=[]
    t0=time.time()
    p1=st.progress(0, text="1/3 Discovering prospects")
    rows, diag=discover(sector, custom_term, location, radius, limit, manual_lat=manual_lat, manual_lon=manual_lon)
    p1.progress(1.0, text=f"1/3 Discovery complete: {len(rows)} prospects retained")
    t1=time.time()
    p2=st.progress(0, text="2/3 Resolving websites")
    p3=st.progress(0, text="3/3 Enriching contact details")
    enriched=[]
    total=len(rows)
    # website + contact enrichment are together per row, but progress shown in two phases based on completion
    def work(r): return enrich_row(r, location, sector, thorough, contact_fallback)
    done=0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures=[ex.submit(work,r) for r in rows]
        for fut in as_completed(futures):
            try: enriched.append(fut.result())
            except Exception as e:
                rr={"prospect_name":"ERROR","enrichment_status":f"failed: {e}"}; enriched.append(rr)
            done+=1
            frac=done/max(1,total)
            p2.progress(min(frac,1.0), text=f"2/3 Resolving websites/contact pages: {done}/{total}")
            p3.progress(min(frac,1.0), text=f"3/3 Enriching contact details: {done}/{total}")
    t2=time.time()
    df=pd.DataFrame(enriched)
    preferred=["prospect_name","website","official_website_status","best_email","best_phone","visible_emails","website_phone","search_emails","search_phone","address","city","country","source","website_candidates","website_validation_reason","contact_pages_checked","enrichment_status"]
    cols=[c for c in preferred if c in df.columns]+[c for c in df.columns if c not in preferred]
    df=df[cols]
    st.session_state.prospects=df
    st.session_state.timing={"discovery_seconds":round(t1-t0,2),"enrichment_seconds":round(t2-t1,2),"total_seconds":round(t2-t0,2),"retention_diagnostics":diag,"search_depth":"Extra thorough" if thorough else "Normal","processing_speed":speed,"stage_diagnostics":st.session_state.get("stage_diagnostics",[])}
    st.session_state.last_key=key

if st.session_state.prospects is not None:
    df=st.session_state.prospects.copy()
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Prospects", len(df))
    c2.metric("Websites", int(df.get("website", pd.Series(dtype=str)).fillna('').astype(str).ne('').sum()))
    c3.metric("Emails", int(df.get("best_email", pd.Series(dtype=str)).fillna('').astype(str).ne('').sum()))
    c4.metric("Phones", int(df.get("best_phone", pd.Series(dtype=str)).fillna('').astype(str).ne('').sum()))
    st.subheader("Prospects")
    view_cols=[c for c in ["prospect_name","website","official_website_status","best_email","best_phone","address","source"] if c in df.columns]
    st.dataframe(df[view_cols], use_container_width=True, hide_index=True)
    fname=safe_filename(sector.replace(' (optimized)',''),location)
    ec1,ec2=st.columns(2)
    with ec1:
        st.download_button("Download CSV", df.to_csv(index=False).encode('utf-8'), file_name=f"{fname}.csv", mime="text/csv")
    with ec2:
        st.download_button("Download Excel", to_excel_bytes(df), file_name=f"{fname}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if show_debug:
        with st.expander("Diagnostics", expanded=False):
            st.json(st.session_state.timing)
            debug_payload={"timing":st.session_state.timing,"debug_log":st.session_state.debug[-500:]}
            st.download_button("Download debug JSON", json.dumps(debug_payload, indent=2).encode("utf-8"), file_name=f"{fname}_debug.json", mime="application/json")
            st.text("\n".join(st.session_state.debug[-200:]))
else:
    st.info("Enter a prospect type and location, then click Find prospects.")
