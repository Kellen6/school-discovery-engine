
import streamlit as st
import pandas as pd
import requests
import re
import time
import math
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from io import BytesIO

st.set_page_config(page_title="School Discovery Engine v9", layout="wide")

APP_VERSION = "v9 - cloud-safe diagnostics + multi-source discovery"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LauraSchoolDiscovery/1.0; +https://streamlit.app)"
}

SCHOOL_KEYWORDS = [
    "school","academy","college","university","campus","institute","lycee","lycée",
    "gymnasium","primary","secondary","high school","preparatory","prep","international"
]

DIRECTORY_WORDS = [
    "best","top","ranking","rankings","directory","list","lists","review","reviews",
    "wikipedia","facebook","linkedin","instagram","youtube","twitter","x.com",
    "schoolguide","saschools","schools4sa","international-schools-database",
    "privateschoolreview","studyinternational","edarabia","whichschooladvisor"
]

ROLE_KEYWORDS = {
    "principal": ["principal", "head of school", "headmaster", "headmistress", "executive head"],
    "admissions": ["admissions", "enrolment", "enrollment", "registrar"],
    "counseling": ["counsellor", "counselor", "college counseling", "university guidance", "career guidance"],
    "learning_support": ["learning support", "sen", "send", "special educational needs", "inclusive education", "inclusion"],
    "innovation": ["innovation", "digital learning", "technology integration", "edtech", "ai"]
}

FIT_KEYWORDS = {
    "international": ["international", "ib", "cambridge", "a-level", "a level", "american curriculum", "british curriculum"],
    "learning_support": ROLE_KEYWORDS["learning_support"],
    "counseling": ROLE_KEYWORDS["counseling"],
    "ai_innovation": ["artificial intelligence", " ai ", "digital learning", "innovation", "future-ready", "technology"],
    "parent": ["parent workshop", "parent evening", "parent education", "parent webinar"]
}

CONTACT_PAGE_HINTS = [
    "contact", "contacts", "admissions", "staff", "team", "leadership", "management",
    "about", "people", "faculty", "directory", "support", "counselling", "counseling"
]

def safe_get(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        return {"ok": r.status_code < 400, "status": r.status_code, "url": r.url, "text": r.text[:100000], "error": ""}
    except Exception as e:
        return {"ok": False, "status": None, "url": url, "text": "", "error": str(e)}

def normalize_url(url):
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"): url = "https:" + url
    if not url.startswith("http"): url = "https://" + url
    parsed=urlparse(url)
    if not parsed.netloc: return ""
    return parsed.scheme + "://" + parsed.netloc + parsed.path.rstrip("/")

def domain(url):
    try:
        d = urlparse(url).netloc.lower()
        if d.startswith("www."): d=d[4:]
        return d
    except Exception:
        return ""

def looks_directory(url, title=""):
    s=(url+" "+title).lower()
    return any(w in s for w in DIRECTORY_WORDS)

def looks_school_name(name):
    n=(name or "").lower()
    return any(k in n for k in SCHOOL_KEYWORDS)

def emails_from_text(text):
    raw = set(re.findall(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', text or ""))
    # filter obvious junk
    out=[]
    for e in raw:
        el=e.lower().strip(".,;:)")
        if any(bad in el for bad in ["example.com","sentry","wixpress","schema.org","domain.com"]): continue
        out.append(el)
    return sorted(set(out))

def infer_pattern(emails):
    # Basic pattern inference from visible person-like emails.
    locals_=[e.split("@")[0] for e in emails if "@" in e]
    patterns=[]
    for l in locals_:
        if re.match(r"^[a-z]+\.[a-z]+$", l): patterns.append("firstname.lastname")
        elif re.match(r"^[a-z][a-z]+$", l) and len(l)>5: patterns.append("firstname/lastname")
        elif re.match(r"^[a-z]\.[a-z]+$", l): patterns.append("firstinitial.lastname")
        elif re.match(r"^[a-z][a-z]+[._-][a-z][a-z]+$", l): patterns.append("firstname_separator_lastname")
    if not patterns: return ""
    return max(set(patterns), key=patterns.count)

def extract_links_from_html(base_url, html):
    soup=BeautifulSoup(html or "", "html.parser")
    links=[]
    for a in soup.find_all("a", href=True):
        href=urljoin(base_url, a["href"])
        text=a.get_text(" ", strip=True)
        links.append({"url": normalize_url(href), "text": text})
    return links

def extract_school_links_from_source_page(source_url):
    res=safe_get(source_url)
    rows=[]
    logs=[f"Fetch {source_url}: status={res['status']} ok={res['ok']} error={res['error']}"]
    if not res["ok"]:
        return rows, logs
    links=extract_links_from_html(res["url"], res["text"])
    source_dom=domain(res["url"])
    for l in links:
        u=l["url"]; d=domain(u); text=l["text"]
        if not d or d==source_dom: 
            continue
        if looks_directory(u, text):
            continue
        if looks_school_name(text) or looks_school_name(u):
            rows.append({"name": text or d, "website": u, "source": source_url, "method": "source_page_outbound_link"})
    # de-dupe by domain
    seen=set(); dedup=[]
    for r in rows:
        d=domain(r["website"])
        if d not in seen:
            seen.add(d); dedup.append(r)
    logs.append(f"Extracted {len(dedup)} candidate outbound school links")
    return dedup, logs

def nominatim_geocode(place):
    url="https://nominatim.openstreetmap.org/search"
    params={"q": place, "format":"json", "limit":1}
    try:
        r=requests.get(url, params=params, headers=HEADERS, timeout=20)
        if r.status_code>=400: return None, f"Nominatim status {r.status_code}: {r.text[:200]}"
        data=r.json()
        if not data: return None, "Nominatim returned zero locations"
        return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"]), "display_name": data[0].get("display_name","")}, ""
    except Exception as e:
        return None, str(e)

def overpass_query(lat, lon, radius_km, max_results):
    radius=int(radius_km*1000)
    q=f"""
    [out:json][timeout:30];
    (
      node(around:{radius},{lat},{lon})["amenity"~"school|college|university|kindergarten"];
      way(around:{radius},{lat},{lon})["amenity"~"school|college|university|kindergarten"];
      relation(around:{radius},{lat},{lon})["amenity"~"school|college|university|kindergarten"];
      node(around:{radius},{lat},{lon})["school"];
      way(around:{radius},{lat},{lon})["school"];
      relation(around:{radius},{lat},{lon})["school"];
    );
    out center tags {max_results};
    """
    endpoints=[
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.osm.ch/api/interpreter"
    ]
    logs=[]
    for ep in endpoints:
        try:
            r=requests.post(ep, data={"data":q}, headers=HEADERS, timeout=45)
            logs.append(f"Overpass {ep}: status={r.status_code}, bytes={len(r.text)}")
            if r.status_code<400:
                data=r.json()
                elements=data.get("elements", [])
                rows=[]
                for e in elements:
                    tags=e.get("tags", {})
                    name=tags.get("name") or tags.get("operator") or ""
                    website=tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
                    email=tags.get("email") or tags.get("contact:email") or ""
                    phone=tags.get("phone") or tags.get("contact:phone") or ""
                    if not name and not website: continue
                    if not looks_school_name(name) and tags.get("amenity") not in ["school","college","university","kindergarten"]:
                        continue
                    rows.append({
                        "name": name or domain(website),
                        "website": normalize_url(website) if website else "",
                        "email": email,
                        "phone": phone,
                        "osm_type": e.get("type",""),
                        "osm_id": e.get("id",""),
                        "source": "OpenStreetMap",
                        "method": "osm_radius"
                    })
                return rows[:max_results], logs
        except Exception as e:
            logs.append(f"Overpass {ep}: ERROR {e}")
    return [], logs

def scrape_school_website(row, max_pages=8):
    website=normalize_url(row.get("website",""))
    result=dict(row)
    result.update({
        "generic_emails":"",
        "all_visible_emails":"",
        "email_pattern":"",
        "role_signals":"",
        "fit_score":0,
        "contact_confidence":0,
        "scraped_pages":"",
        "scrape_notes":""
    })
    if not website:
        result["scrape_notes"]="No website available from source."
        return result
    home=safe_get(website, timeout=15)
    notes=[f"home status={home['status']} ok={home['ok']}"]
    if not home["ok"]:
        result["scrape_notes"]="; ".join(notes+[home["error"]])
        return result
    pages=[home["url"]]
    links=extract_links_from_html(home["url"], home["text"])
    for l in links:
        lower=(l["url"]+" "+l["text"]).lower()
        if domain(l["url"])==domain(home["url"]) and any(h in lower for h in CONTACT_PAGE_HINTS):
            if l["url"] not in pages:
                pages.append(l["url"])
        if len(pages)>=max_pages:
            break
    combined=""
    all_emails=set()
    fetched=[]
    for p in pages[:max_pages]:
        pr=safe_get(p, timeout=12)
        fetched.append(f"{p} ({pr['status']})")
        if pr["ok"]:
            text=BeautifulSoup(pr["text"], "html.parser").get_text(" ", strip=True)
            combined += "\n" + text[:30000]
            all_emails.update(emails_from_text(pr["text"] + " " + text))
    role_hits=[]
    lower=combined.lower()
    fit=0
    for cat, kws in ROLE_KEYWORDS.items():
        if any(k in lower for k in kws):
            role_hits.append(cat)
    for cat,kws in FIT_KEYWORDS.items():
        if any(k in lower for k in kws):
            fit += {"international":2, "learning_support":3, "counseling":2, "ai_innovation":1, "parent":1}.get(cat,1)
    generic=[e for e in all_emails if e.split("@")[0].lower() in ["info","office","admin","admissions","enquiries","enquiry","contact","principal","registrar"]]
    pattern=infer_pattern(sorted(all_emails))
    confidence=0
    if all_emails: confidence += 40
    if generic: confidence += 15
    if role_hits: confidence += 20
    if pattern: confidence += 15
    if len(fetched)>1: confidence += 10
    result["all_visible_emails"]=", ".join(sorted(all_emails))
    result["generic_emails"]=", ".join(sorted(generic))
    result["email_pattern"]=pattern
    result["role_signals"]=", ".join(role_hits)
    result["fit_score"]=fit
    result["contact_confidence"]=min(confidence,100)
    result["scraped_pages"]=" | ".join(fetched[:max_pages])
    result["scrape_notes"]="; ".join(notes)
    return result

def to_excel_bytes(df):
    bio=BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="schools")
    return bio.getvalue()

st.title("School Discovery Engine")
st.caption(APP_VERSION)

with st.expander("How this version works", expanded=False):
    st.write("""
This version does not rely on Google/DuckDuckGo scraping. It has three paths:
1. Location search via OpenStreetMap/Nominatim/Overpass.
2. Source/list page extraction: paste best-schools/directories and it extracts official school links.
3. Direct school URLs: paste websites you already know.

After discovery, it scrapes each school website for contact pages, emails, role signals, and fit/contact confidence.
""")

tab1, tab2, tab3 = st.tabs(["1. Discover", "2. Scrape & score", "3. Debug"])

with st.sidebar:
    st.header("Discovery settings")
    location=st.text_input("City / metro / location", value="Cape Town, Western Cape, South Africa")
    radius=st.slider("Radius km", 5, 250, 100)
    max_results=st.slider("Max OSM results", 10, 500, 150)
    st.divider()
    source_pages=st.text_area("Optional: paste source/list pages, one per line", height=120, placeholder="https://example.com/best-schools-in-cape-town")
    direct_urls=st.text_area("Optional: paste official school URLs, one per line", height=120, placeholder="https://www.schoolname.org")
    scrape_now=st.checkbox("Scrape websites after discovery", value=True)
    max_pages=st.slider("Max pages per school to scrape", 1, 12, 6)

if "candidates" not in st.session_state:
    st.session_state.candidates=pd.DataFrame()
if "logs" not in st.session_state:
    st.session_state.logs=[]

with tab1:
    if st.button("Find candidates", type="primary"):
        logs=[]
        rows=[]
        # OSM path
        geo, err=nominatim_geocode(location)
        if geo:
            logs.append(f"Geocoded: {geo['display_name']} ({geo['lat']}, {geo['lon']})")
            osm_rows, osm_logs=overpass_query(geo["lat"], geo["lon"], radius, max_results)
            logs += osm_logs
            logs.append(f"OSM candidate rows: {len(osm_rows)}")
            rows += osm_rows
        else:
            logs.append(f"Geocoding failed: {err}")
        # source pages
        for line in source_pages.splitlines():
            u=normalize_url(line)
            if u:
                sr, slogs=extract_school_links_from_source_page(u)
                logs += slogs
                rows += sr
        # direct urls
        for line in direct_urls.splitlines():
            u=normalize_url(line)
            if u:
                rows.append({"name": domain(u), "website": u, "source": "manual_url", "method": "manual_url"})
        # dedupe by domain/name
        seen=set(); dedup=[]
        for r in rows:
            key=domain(r.get("website","")) or (r.get("name","").lower())
            if not key or key in seen: continue
            seen.add(key); dedup.append(r)
        df=pd.DataFrame(dedup)
        st.session_state.candidates=df
        st.session_state.logs=logs
        if df.empty:
            st.warning("No candidates found. Check Debug tab to see whether geocoding, Overpass, or filtering failed. Try a source page or direct URLs as fallback.")
        else:
            st.success(f"Found {len(df)} candidate schools/sites.")
            st.dataframe(df, use_container_width=True)
    elif not st.session_state.candidates.empty:
        st.dataframe(st.session_state.candidates, use_container_width=True)

with tab2:
    df=st.session_state.candidates
    if df.empty:
        st.info("Run discovery first, or paste direct URLs/source pages in the sidebar.")
    else:
        if st.button("Scrape websites + score contacts"):
            progress=st.progress(0)
            out=[]
            for i,row in df.iterrows():
                out.append(scrape_school_website(row.to_dict(), max_pages=max_pages))
                progress.progress((len(out))/len(df))
                time.sleep(0.05)
            res=pd.DataFrame(out)
            st.session_state.candidates=res
            st.success(f"Scraped {len(res)} candidates.")
            st.dataframe(res, use_container_width=True)
        else:
            st.dataframe(df, use_container_width=True)
        csv=st.session_state.candidates.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV", data=csv, file_name="school_discovery_results.csv", mime="text/csv")
        try:
            xbytes=to_excel_bytes(st.session_state.candidates)
            st.download_button("Download Excel", data=xbytes, file_name="school_discovery_results.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception as e:
            st.caption(f"Excel export unavailable: {e}")

with tab3:
    st.subheader("Diagnostics")
    st.write("Use this when the hosted app returns no results.")
    if st.button("Run connectivity tests"):
        tests=[
            ("Nominatim", "https://nominatim.openstreetmap.org/search?q=Cape%20Town&format=json&limit=1"),
            ("Overpass", "https://overpass-api.de/api/status"),
            ("Example school site", "https://www.bishops.org.za/")
        ]
        for name,url in tests:
            res=safe_get(url, timeout=20)
            st.write(f"**{name}**: status={res['status']} ok={res['ok']} error={res['error']}")
            if res["text"]:
                st.code(res["text"][:500])
    st.subheader("Last run logs")
    if st.session_state.logs:
        for l in st.session_state.logs:
            st.text(l)
    else:
        st.caption("No logs yet.")
