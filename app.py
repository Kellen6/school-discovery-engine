import re
import io
import time
import json
import math
import urllib.parse
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional, Set

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

APP_TITLE = "School Discovery Engine — Free v10"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SchoolDiscoveryEngine/10.0; +https://streamlit.io)",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 12
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BAD_EMAIL_TOKENS = ["example.com", "sentry", "wixpress", "wordpress", "schema.org", "domain.com"]
CONTACT_PATH_HINTS = [
    "contact", "contacts", "admissions", "apply", "staff", "team", "leadership", "about", "faculty", "directory",
    "learning-support", "support", "counselling", "counseling", "sen", "inclusion", "academics"
]
ROLE_KEYWORDS = {
    "principal_head": ["principal", "head of school", "headmaster", "headmistress", "executive head", "school director", "rector"],
    "admissions": ["admissions", "enrolment", "enrollment", "registrar", "applications"],
    "counselor": ["counsellor", "counselor", "college counselor", "university counselor", "guidance", "career guidance"],
    "learning_support": ["learning support", "special needs", "sen", "senco", "inclusive education", "inclusion", "educational support", "barriers to learning"],
    "innovation_ai": ["innovation", "digital learning", "technology integration", "ai", "artificial intelligence", "edtech", "ict"],
}
FIT_KEYWORDS = {
    "international_curriculum": ["international baccalaureate", " ib ", "cambridge", "a level", "a-level", "igcse", "international curriculum", "american curriculum", "british curriculum"],
    "college_university_guidance": ["college counseling", "college counselling", "university guidance", "university counselling", "university counseling", "higher education guidance"],
    "learning_support": ROLE_KEYWORDS["learning_support"],
    "ai_innovation": ROLE_KEYWORDS["innovation_ai"],
    "parent_education": ["parent workshop", "parent education", "parents evening", "parent information", "parent seminar"],
}
SOURCE_DOMAINS = ["directory", "best", "top", "list", "ranking", "reviews", "database", "finder", "world-schools", "internationalschools"]


def normalize_url(url: str) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))
    return clean


def domain_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def fetch(url: str) -> Tuple[Optional[str], str, int]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        ctype = r.headers.get("content-type", "")
        if r.status_code >= 400:
            return None, f"HTTP {r.status_code}", r.status_code
        if "text/html" not in ctype and "application/xhtml" not in ctype and ctype:
            return None, f"Non-HTML: {ctype}", r.status_code
        return r.text, "ok", r.status_code
    except Exception as e:
        return None, str(e), 0


def soup_text(soup: BeautifulSoup, max_chars: int = 12000) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.extract()
    text = " ".join(soup.get_text(" ").split())
    return text[:max_chars]


def extract_emails(text: str) -> List[str]:
    found = sorted(set(e.lower().strip(".,;:()[]<>") for e in EMAIL_RE.findall(text or "")))
    return [e for e in found if not any(b in e for b in BAD_EMAIL_TOKENS)]


def classify_generic(emails: List[str]) -> List[str]:
    prefixes = ("info@", "office@", "admin@", "admissions@", "enquiries@", "enquiry@", "contact@", "hello@", "principal@", "reception@", "registrar@")
    return [e for e in emails if e.startswith(prefixes)]


def detect_signals(text: str) -> Dict[str, str]:
    low = f" {text.lower()} "
    out = {}
    for key, kws in FIT_KEYWORDS.items():
        hits = [kw.strip() for kw in kws if kw in low]
        out[key] = ", ".join(sorted(set(hits))[:8])
    for key, kws in ROLE_KEYWORDS.items():
        hits = [kw.strip() for kw in kws if kw in low]
        out[f"role_{key}"] = ", ".join(sorted(set(hits))[:8])
    return out


def fit_score(signals: Dict[str, str]) -> int:
    score = 0
    weights = {"learning_support": 3, "college_university_guidance": 3, "international_curriculum": 2, "ai_innovation": 2, "parent_education": 1}
    for k, w in weights.items():
        if signals.get(k):
            score += w
    return score


def contact_confidence(emails: List[str], pages_scraped: int, signals: Dict[str, str]) -> int:
    score = 0
    if emails:
        score += 35
    if classify_generic(emails):
        score += 15
    if any(signals.get(f"role_{r}") for r in ["principal_head", "admissions", "counselor", "learning_support"]):
        score += 25
    score += min(25, pages_scraped * 5)
    return min(score, 100)


def infer_email_pattern(emails: List[str]) -> Tuple[str, List[str]]:
    personal = []
    for e in emails:
        local = e.split("@")[0]
        if local in ["info", "office", "admin", "admissions", "contact", "hello", "reception", "registrar"]:
            continue
        if re.search(r"[a-z]", local) and not re.search(r"\d{3,}", local):
            personal.append(e)
    if not personal:
        return "", []
    patterns = []
    for e in personal:
        local = e.split("@")[0]
        if "." in local:
            patterns.append("firstname.lastname@")
        elif "_" in local:
            patterns.append("firstname_lastname@")
        elif len(local) > 2:
            patterns.append("firstinitiallastname@ or firstname@")
    if not patterns:
        return "", personal[:5]
    # mode-ish
    pattern = max(set(patterns), key=patterns.count)
    return pattern, personal[:8]


def likely_contact_links(base_url: str, soup: BeautifulSoup, max_links: int = 8) -> List[str]:
    links = []
    base_domain = domain_of(base_url)
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        label = " ".join([href.lower(), a.get_text(" ", strip=True).lower()])
        if any(h in label for h in CONTACT_PATH_HINTS):
            u = urllib.parse.urljoin(base_url, href)
            u = normalize_url(u)
            if not u:
                continue
            if domain_of(u) == base_domain:
                links.append(u)
    # common paths fallback
    for p in ["contact", "contact-us", "admissions", "about-us", "staff", "leadership", "learning-support"]:
        links.append(urllib.parse.urljoin(base_url.rstrip("/") + "/", p))
    dedup = []
    seen = set()
    for l in links:
        if l not in seen:
            seen.add(l); dedup.append(l)
    return dedup[:max_links]


def scrape_school(url: str, source: str = "") -> Dict:
    url = normalize_url(url)
    row = {
        "school_name": "", "website": url or "", "domain": domain_of(url or ""), "source": source,
        "status": "", "pages_scraped": 0, "source_pages": "", "visible_emails": "", "generic_emails": "",
        "email_pattern_inferred": "", "personal_email_examples": "", "fit_score": 0, "contact_confidence": 0,
        "international_curriculum": "", "college_university_guidance": "", "learning_support": "", "ai_innovation": "", "parent_education": "",
        "role_principal_head": "", "role_admissions": "", "role_counselor": "", "role_learning_support": "", "role_innovation_ai": "",
        "notes": ""
    }
    if not url:
        row["status"] = "invalid_url"
        return row
    html, msg, code = fetch(url)
    if not html:
        # retry http if https failed
        if url.startswith("https://"):
            alt = "http://" + url[len("https://"):]
            html, msg, code = fetch(alt)
            if html:
                url = alt; row["website"] = url; row["domain"] = domain_of(url)
        if not html:
            row["status"] = f"fetch_failed: {msg}"
            return row
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    h1 = soup.find("h1")
    row["school_name"] = (h1.get_text(" ", strip=True) if h1 else title)[:120]
    pages = [(url, soup_text(soup))]
    for link in likely_contact_links(url, soup):
        html2, msg2, code2 = fetch(link)
        if html2:
            soup2 = BeautifulSoup(html2, "lxml")
            pages.append((link, soup_text(soup2)))
        time.sleep(0.08)
    all_text = "\n".join(t for _, t in pages)
    emails = extract_emails(all_text)
    signals = detect_signals(all_text)
    pattern, personal_examples = infer_email_pattern(emails)
    row.update(signals)
    row["pages_scraped"] = len(pages)
    row["source_pages"] = " | ".join(p for p, _ in pages[:10])
    row["visible_emails"] = ", ".join(emails)
    row["generic_emails"] = ", ".join(classify_generic(emails))
    row["email_pattern_inferred"] = pattern
    row["personal_email_examples"] = ", ".join(personal_examples)
    row["fit_score"] = fit_score(signals)
    row["contact_confidence"] = contact_confidence(emails, len(pages), signals)
    row["status"] = "ok"
    return row


def geocode_location(q: str) -> Optional[Tuple[float, float, str]]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": q, "format": "json", "limit": 1}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name", q)
    except Exception:
        return None
    return None


def overpass_schools(lat: float, lon: float, radius_km: int, max_results: int) -> Tuple[List[Dict], str]:
    radius = int(radius_km * 1000)
    q = f"""
    [out:json][timeout:25];
    (
      node(around:{radius},{lat},{lon})[amenity=school];
      way(around:{radius},{lat},{lon})[amenity=school];
      relation(around:{radius},{lat},{lon})[amenity=school];
      node(around:{radius},{lat},{lon})[amenity=college];
      way(around:{radius},{lat},{lon})[amenity=college];
      node(around:{radius},{lat},{lon})[amenity=university];
      way(around:{radius},{lat},{lon})[amenity=university];
    );
    out center tags {max_results};
    """
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter",
    ]
    last = ""
    for ep in endpoints:
        try:
            r = requests.post(ep, data={"data": q}, headers=HEADERS, timeout=35)
            if r.status_code != 200:
                last = f"{ep}: HTTP {r.status_code} {r.text[:150]}"; continue
            data = r.json()
            rows = []
            for el in data.get("elements", [])[:max_results]:
                tags = el.get("tags", {}) or {}
                name = tags.get("name") or tags.get("official_name") or ""
                website = tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
                email = tags.get("email") or tags.get("contact:email") or ""
                phone = tags.get("phone") or tags.get("contact:phone") or ""
                if name or website:
                    rows.append({"school_name": name, "website": normalize_url(website) or "", "osm_email": email, "osm_phone": phone, "source": "map/geolocation"})
            return rows, f"ok: {len(rows)} rows from {ep}"
        except Exception as e:
            last = f"{ep}: {e}"
    return [], last


def search_school_name(name: str, location: str = "") -> Tuple[List[str], str]:
    query = f"{name} {location} official website school".strip()
    # Try DuckDuckGo lightweight HTML. This may be blocked on some hosts, but name mode also lets user paste found URL.
    urls = []
    diagnostics = []
    try:
        ddg = "https://duckduckgo.com/html/"
        r = requests.get(ddg, params={"q": query}, headers=HEADERS, timeout=TIMEOUT)
        diagnostics.append(f"DuckDuckGo HTTP {r.status_code}")
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "uddg=" in href:
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [""])[0]
                href = urllib.parse.unquote(parsed)
            u = normalize_url(href)
            if not u:
                continue
            d = domain_of(u)
            if any(bad in d for bad in ["duckduckgo", "google", "facebook", "instagram", "linkedin", "wikipedia", "youtube"]):
                continue
            # avoid obvious directories unless no better choices
            urls.append(u)
        # rank official-looking URLs higher
        name_tokens = [t for t in re.sub(r"[^a-z0-9 ]", " ", name.lower()).split() if len(t) > 2]
        def rank(u):
            d = domain_of(u)
            score = sum(3 for t in name_tokens if t in d)
            score -= sum(2 for s in SOURCE_DOMAINS if s in d)
            if d.endswith((".ac.za", ".edu", ".school", ".org", ".co.za", ".com")):
                score += 1
            return score
        urls = sorted(set(urls), key=rank, reverse=True)[:5]
    except Exception as e:
        diagnostics.append(f"DuckDuckGo error: {e}")
    return urls, " | ".join(diagnostics)


def extract_school_links_from_source_page(url: str, max_links: int = 50) -> Tuple[List[str], str]:
    url = normalize_url(url)
    if not url:
        return [], "invalid source URL"
    html, msg, code = fetch(url)
    if not html:
        return [], msg
    soup = BeautifulSoup(html, "lxml")
    source_domain = domain_of(url)
    links = []
    for a in soup.find_all("a", href=True):
        u = normalize_url(urllib.parse.urljoin(url, a["href"]))
        if not u: continue
        d = domain_of(u)
        if d == source_domain: continue
        if any(bad in d for bad in ["facebook", "instagram", "twitter", "x.com", "linkedin", "youtube", "google", "maps"]): continue
        # loose official-looking school domains
        label = (a.get_text(" ", strip=True) + " " + d).lower()
        if any(k in label for k in ["school", "college", "academy", "international", "campus", "university", "primary", "high"]):
            links.append(u)
    return list(dict.fromkeys(links))[:max_links], f"extracted {len(set(links))} outbound candidate links"


def enrich_candidates(candidates: List[Dict], do_scrape: bool, progress_label: str) -> pd.DataFrame:
    rows = []
    seen = set()
    prog = st.progress(0, text=progress_label)
    total = max(len(candidates), 1)
    for i, c in enumerate(candidates):
        url = normalize_url(c.get("website", ""))
        key = domain_of(url or "") or c.get("school_name", "").lower()
        if key in seen:
            continue
        seen.add(key)
        if do_scrape and url:
            row = scrape_school(url, c.get("source", ""))
            if c.get("school_name") and (not row.get("school_name") or len(row.get("school_name", "")) < 5):
                row["school_name"] = c.get("school_name")
            if c.get("osm_email"):
                row["visible_emails"] = ", ".join(sorted(set([c["osm_email"].lower()] + [e for e in row.get("visible_emails", "").split(", ") if e])))
            if c.get("osm_phone"):
                row["notes"] = f"OSM phone: {c.get('osm_phone')}"
        else:
            row = {"school_name": c.get("school_name", ""), "website": url or "", "domain": domain_of(url or ""), "source": c.get("source", ""), "status": "not_scraped", "visible_emails": c.get("osm_email", ""), "notes": c.get("notes", "")}
        rows.append(row)
        prog.progress(min((i+1)/total, 1.0), text=f"{progress_label} {i+1}/{total}")
    prog.empty()
    return pd.DataFrame(rows)


def download_buttons(df: pd.DataFrame, base_name: str):
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV", csv, file_name=f"{base_name}.csv", mime="text/csv")
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="schools")
    st.download_button("Download Excel", bio.getvalue(), file_name=f"{base_name}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)
st.caption("Three separate workflows: map/geolocation, school name, or school URL. All can scrape websites for contact details.")

with st.sidebar:
    st.header("Settings")
    do_scrape = st.checkbox("Scrape school websites for contacts", value=True)
    max_pages_note = st.caption("Scraping checks homepage + likely contact/admissions/staff/support pages.")
    st.divider()
    st.write("Best first test: use **School URL** mode with 2-3 known school sites.")

mode = st.radio(
    "Choose discovery mode",
    ["1. Map / geolocation", "2. School name", "3. School URL"],
    horizontal=True,
)

if mode.startswith("1"):
    st.subheader("1. Map / geolocation discovery")
    st.write("Find schools near a city/area using OpenStreetMap, then scrape websites where OSM includes a website.")
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        location = st.text_input("City / area", value="Cape Town, Western Cape, South Africa")
    with col2:
        radius = st.slider("Radius (km)", 5, 150, 75, 5)
    with col3:
        max_results = st.number_input("Max map results", 10, 500, 150, 10)
    only_with_websites = st.checkbox("Only keep schools with websites", value=False)
    if st.button("Find and scrape schools", type="primary"):
        geo = geocode_location(location)
        if not geo:
            st.error("Could not geocode this location. Try a more specific city/country or use School URL mode.")
        else:
            lat, lon, display = geo
            st.info(f"Geocoded to: {display} ({lat:.4f}, {lon:.4f})")
            candidates, msg = overpass_schools(lat, lon, radius, int(max_results))
            st.write(f"Map query: {msg}")
            if only_with_websites:
                candidates = [c for c in candidates if c.get("website")]
            if not candidates:
                st.warning("No map candidates found. Try larger radius, or use School name / School URL mode.")
            else:
                st.write(f"Candidates found: {len(candidates)}")
                df = enrich_candidates(candidates, do_scrape, "Scraping/enriching")
                st.dataframe(df, use_container_width=True)
                download_buttons(df, "school_discovery_map_results")

elif mode.startswith("2"):
    st.subheader("2. School name lookup")
    st.write("Enter school names. The app tries to find likely official websites, then scrapes them. If search is blocked, paste URLs in mode 3.")
    names_text = st.text_area("School names, one per line", height=180, placeholder="Reddam House Constantia\nBishops Diocesan College\nHerschel Girls School")
    location_hint = st.text_input("Optional location hint", value="Cape Town South Africa")
    if st.button("Find websites and scrape", type="primary"):
        candidates = []
        logs = []
        for name in [n.strip() for n in names_text.splitlines() if n.strip()]:
            urls, diag = search_school_name(name, location_hint)
            logs.append({"school_name": name, "diagnostic": diag, "candidate_urls": ", ".join(urls)})
            if urls:
                # take top 1 by default to avoid directories; show logs for alternatives
                candidates.append({"school_name": name, "website": urls[0], "source": "school-name-search", "notes": f"alternatives: {', '.join(urls[1:])}"})
            else:
                candidates.append({"school_name": name, "website": "", "source": "school-name-search", "notes": "No URL found; use School URL mode"})
        st.write("Lookup diagnostics")
        st.dataframe(pd.DataFrame(logs), use_container_width=True)
        if not candidates:
            st.warning("No school names entered.")
        else:
            df = enrich_candidates(candidates, do_scrape, "Scraping/enriching")
            st.dataframe(df, use_container_width=True)
            download_buttons(df, "school_discovery_name_results")

else:
    st.subheader("3. School URL scraping")
    st.write("Paste official school websites or source/list pages. This is the most reliable workflow on Streamlit Cloud.")
    url_mode = st.radio("URL type", ["Official school websites", "Source/list pages to extract school links"], horizontal=True)
    urls_text = st.text_area("URLs, one per line", height=220, placeholder="https://www.example-school.ac.za\nhttps://www.another-school.org")
    max_links = st.number_input("Max links per source/list page", 5, 200, 50, 5)
    if st.button("Scrape URLs", type="primary"):
        urls = [normalize_url(u) for u in urls_text.splitlines() if u.strip()]
        urls = [u for u in urls if u]
        candidates = []
        if url_mode == "Official school websites":
            candidates = [{"school_name": "", "website": u, "source": "direct-url"} for u in urls]
        else:
            source_logs = []
            for u in urls:
                links, msg = extract_school_links_from_source_page(u, int(max_links))
                source_logs.append({"source_page": u, "status": msg, "links_found": len(links)})
                for link in links:
                    candidates.append({"school_name": "", "website": link, "source": f"source-page: {u}"})
            st.write("Source page extraction")
            st.dataframe(pd.DataFrame(source_logs), use_container_width=True)
        if not candidates:
            st.warning("No candidate URLs found. Try official school URLs directly.")
        else:
            df = enrich_candidates(candidates, do_scrape, "Scraping/enriching")
            st.dataframe(df, use_container_width=True)
            download_buttons(df, "school_discovery_url_results")

with st.expander("What the columns mean"):
    st.markdown("""
- **visible_emails**: emails found directly on school pages.
- **generic_emails**: safer generic addresses like `admissions@`, `info@`, `office@`.
- **email_pattern_inferred**: pattern guessed from visible personal emails. Not verified.
- **fit_score**: rough score based on international curriculum, counseling, learning support, AI/innovation, parent education.
- **contact_confidence**: how much useful contact information was found.
- **source_pages**: pages actually scraped.
""")
