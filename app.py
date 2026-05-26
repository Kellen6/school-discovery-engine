import io
import re
import time
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

APP_TITLE = "School Discovery Engine — Free v7"
USER_AGENT = "Mozilla/5.0 (compatible; SchoolDiscoveryEngine/7.0; public school outreach research)"
TIMEOUT = 15
MAX_PAGES_PER_SCHOOL = 8

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")

SOURCE_PAGE_HINTS = [
    "best", "top", "directory", "rank", "schools in", "list of", "finder", "catalogue", "database"
]
BAD_DOMAINS = [
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "google.com",
    "wikipedia.org", "wikidata.org", "tripadvisor", "booking.com", "news24.com", "gov.za", "gov.uk",
    "privateschoolreview", "international-schools-database", "schoolguide", "world-schools", "whichschooladvisor"
]
SCHOOL_DOMAIN_HINTS = ["school", "college", "academy", "campus", "education", "edu", "ac.", "sch.", "prep", "high"]
CONTACT_PATH_HINTS = [
    "contact", "contacts", "contact-us", "about", "staff", "team", "leadership", "management", "admissions",
    "admission", "apply", "counselling", "counseling", "support", "learning-support", "inclusion", "sen", "senco"
]
ROLE_KEYWORDS = {
    "principal": ["principal", "head of school", "headmaster", "headmistress", "executive head"],
    "counselor": ["counsellor", "counselor", "college counselor", "university counselor", "guidance"],
    "learning_support": ["learning support", "sen", "senco", "special needs", "inclusion", "inclusive education", "educational support"],
    "admissions": ["admissions", "admission", "enrolment", "enrollment", "registrar"],
    "innovation_ai": ["innovation", "digital learning", "technology integration", "artificial intelligence", " ai ", "edtech"],
}
FIT_KEYWORDS = {
    "international": ["international", "ib", "cambridge", "a level", "a-level", "igcse", "american curriculum", "british curriculum"],
    "university_counseling": ["university counselling", "university counseling", "college counseling", "college counselling", "university guidance", "careers guidance"],
    "learning_support": ROLE_KEYWORDS["learning_support"],
    "ai_innovation": ROLE_KEYWORDS["innovation_ai"],
    "inclusion": ["inclusion", "inclusive", "diverse learners", "neurodiversity", "accessibility"],
    "parent": ["parent workshop", "parent education", "parent evening", "parent seminar"],
}


def norm_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def is_bad_domain(url: str) -> bool:
    d = domain_of(url)
    return any(b in d for b in BAD_DOMAINS)


def is_probably_school_site(url: str, anchor_text: str = "") -> bool:
    d = domain_of(url)
    if not d or is_bad_domain(url):
        return False
    hay = f"{d} {anchor_text}".lower()
    return any(h in hay for h in SCHOOL_DOMAIN_HINTS)


def request_get(url: str):
    try:
        r = requests.get(norm_url(url), headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return None
        ctype = r.headers.get("content-type", "").lower()
        if "text/html" not in ctype and "application/xhtml" not in ctype and ctype:
            return None
        return r
    except Exception:
        return None


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def soup_from_url(url: str):
    r = request_get(url)
    if not r:
        return None, None
    try:
        return BeautifulSoup(r.text, "lxml"), r.url
    except Exception:
        return None, r.url


def extract_source_links(source_urls):
    rows = []
    for src in source_urls:
        soup, final_url = soup_from_url(src)
        if not soup:
            rows.append({"source_url": src, "status": "failed", "candidate_url": "", "anchor_text": ""})
            continue
        for a in soup.find_all("a", href=True):
            href = urljoin(final_url, a.get("href"))
            text = clean_text(a.get_text(" "))[:120]
            if is_probably_school_site(href, text):
                rows.append({"source_url": final_url, "status": "candidate", "candidate_url": href.split("#")[0], "anchor_text": text})
    # Deduplicate by domain
    seen = set()
    out = []
    for row in rows:
        d = domain_of(row.get("candidate_url", ""))
        if row["status"] == "candidate" and d in seen:
            continue
        if d:
            seen.add(d)
        out.append(row)
    return pd.DataFrame(out)


def overpass_query(location: str, school_types, limit: int):
    # Nominatim geocode
    geo_url = "https://nominatim.openstreetmap.org/search"
    params = {"q": location, "format": "json", "limit": 1}
    g = requests.get(geo_url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    g.raise_for_status()
    data = g.json()
    if not data:
        return pd.DataFrame()
    lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
    radius = 35000
    amenity_filters = []
    if "Schools" in school_types:
        amenity_filters.append('node["amenity"="school"]')
        amenity_filters.append('way["amenity"="school"]')
        amenity_filters.append('relation["amenity"="school"]')
    if "Universities / Colleges" in school_types:
        amenity_filters.append('node["amenity"="university"]')
        amenity_filters.append('way["amenity"="university"]')
        amenity_filters.append('relation["amenity"="university"]')
        amenity_filters.append('node["amenity"="college"]')
        amenity_filters.append('way["amenity"="college"]')
        amenity_filters.append('relation["amenity"="college"]')
    body = "".join(f"{f}(around:{radius},{lat},{lon});" for f in amenity_filters)
    query = f"""
    [out:json][timeout:25];
    (
      {body}
    );
    out center tags {limit};
    """
    res = requests.post("https://overpass-api.de/api/interpreter", data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=35)
    res.raise_for_status()
    elements = res.json().get("elements", [])[:limit]
    rows = []
    for e in elements:
        tags = e.get("tags", {})
        name = tags.get("name") or tags.get("operator") or ""
        website = tags.get("website") or tags.get("contact:website") or ""
        email = tags.get("email") or tags.get("contact:email") or ""
        phone = tags.get("phone") or tags.get("contact:phone") or ""
        rows.append({
            "school_name": name,
            "location_query": location,
            "source": "OpenStreetMap",
            "website": norm_url(website) if website else "",
            "osm_email": email,
            "osm_phone": phone,
            "lat": e.get("lat") or e.get("center", {}).get("lat"),
            "lon": e.get("lon") or e.get("center", {}).get("lon"),
            "raw_type": tags.get("amenity", ""),
        })
    return pd.DataFrame(rows)


def find_candidate_pages(base_url: str, soup):
    candidates = []
    base_domain = domain_of(base_url)
    candidates.append(base_url)
    for a in soup.find_all("a", href=True):
        text = clean_text(a.get_text(" ")).lower()
        href = urljoin(base_url, a["href"])
        if domain_of(href) != base_domain:
            continue
        low = href.lower() + " " + text
        if any(h in low for h in CONTACT_PATH_HINTS):
            candidates.append(href.split("#")[0])
    # Deduplicate, cap
    out = []
    seen = set()
    for u in candidates:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out[:MAX_PAGES_PER_SCHOOL]


def infer_email_pattern(emails):
    local_parts = [e.split("@")[0].lower() for e in emails if "@" in e]
    if len(local_parts) < 2:
        return ""
    patterns = []
    for lp in local_parts:
        if "." in lp and all(part.isalpha() for part in lp.split(".")[:2]):
            patterns.append("firstname.lastname")
        elif "_" in lp and all(part.isalpha() for part in lp.split("_")[:2]):
            patterns.append("firstname_lastname")
        elif re.match(r"^[a-z][a-z]+[.\-_]?[a-z]+$", lp):
            patterns.append("name_based")
    if patterns:
        return max(set(patterns), key=patterns.count)
    return ""


def extract_people_candidates(text):
    # Conservative name/title extraction from nearby role keyword lines.
    candidates = []
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    for sent in sentences:
        s = clean_text(sent)
        low = s.lower()
        matched_roles = [role for role, kws in ROLE_KEYWORDS.items() if any(k in low for k in kws)]
        if not matched_roles:
            continue
        # Capture title-case names of 2-4 words, excluding common role terms
        names = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b", s)
        for name in names[:3]:
            if any(w.lower() in ["Head", "School", "Learning", "Support", "Admissions", "Principal"] for w in name.split()):
                continue
            candidates.append({"name": name, "roles": ",".join(matched_roles), "evidence": s[:220]})
    # Deduplicate by name-role
    seen = set(); out = []
    for c in candidates:
        key = (c["name"], c["roles"])
        if key not in seen:
            out.append(c); seen.add(key)
    return out[:10]


def make_inferred_emails(people, domain, pattern):
    if not pattern or not domain:
        return []
    out = []
    for p in people:
        parts = re.sub(r"[^A-Za-z\s]", "", p["name"]).lower().split()
        if len(parts) < 2:
            continue
        first, last = parts[0], parts[-1]
        if pattern == "firstname.lastname":
            email = f"{first}.{last}@{domain}"
        elif pattern == "firstname_lastname":
            email = f"{first}_{last}@{domain}"
        else:
            continue
        out.append(f"{p['name']} ({p['roles']}): {email} [UNVERIFIED]")
    return out


def score_fit(text):
    low = f" {text.lower()} "
    score = 0
    hits = []
    weights = {
        "international": 2,
        "university_counseling": 3,
        "learning_support": 3,
        "ai_innovation": 2,
        "inclusion": 2,
        "parent": 1,
    }
    for category, kws in FIT_KEYWORDS.items():
        if any(k in low for k in kws):
            score += weights[category]
            hits.append(category)
    return score, ", ".join(hits)


def scrape_school(row):
    website = row.get("website", "") or row.get("candidate_url", "")
    website = norm_url(website)
    result = dict(row)
    if not website:
        result.update({"scrape_status": "no website", "verified_emails": "", "generic_emails": "", "phone_numbers": "", "pages_scraped": "", "role_signals": "", "fit_score": 0, "fit_signals": "", "contact_confidence": "low", "inferred_email_pattern": "", "inferred_contacts": ""})
        return result
    soup, final_url = soup_from_url(website)
    if not soup:
        result.update({"scrape_status": "failed", "verified_emails": "", "generic_emails": "", "phone_numbers": "", "pages_scraped": "", "role_signals": "", "fit_score": 0, "fit_signals": "", "contact_confidence": "low", "inferred_email_pattern": "", "inferred_contacts": ""})
        return result
    pages = find_candidate_pages(final_url, soup)
    all_text = ""
    all_emails = set()
    all_phones = set()
    pages_ok = []
    for p in pages:
        psoup, pfinal = soup_from_url(p)
        if not psoup:
            continue
        pages_ok.append(pfinal)
        text = clean_text(psoup.get_text(" "))
        all_text += "\n" + text[:20000]
        all_emails.update(e.lower() for e in EMAIL_RE.findall(text))
        all_phones.update(clean_text(x) for x in PHONE_RE.findall(text))
        # mailto links
        for a in psoup.find_all("a", href=True):
            href = a["href"]
            if href.lower().startswith("mailto:"):
                all_emails.add(href[7:].split("?")[0].strip().lower())
        time.sleep(0.15)
    generic = sorted([e for e in all_emails if e.split("@")[0] in ["info", "office", "admin", "admissions", "admission", "enquiries", "enquiry", "contact", "reception"]])
    role_hits = []
    low = all_text.lower()
    for role, kws in ROLE_KEYWORDS.items():
        if any(k in low for k in kws):
            role_hits.append(role)
    fit_score, fit_signals = score_fit(all_text)
    pattern = infer_email_pattern(sorted(all_emails))
    people = extract_people_candidates(all_text)
    domain = domain_of(final_url)
    inferred = make_inferred_emails(people, domain, pattern)
    if all_emails and ("staff" in " ".join(pages_ok).lower() or len(all_emails) >= 2):
        confidence = "high"
    elif all_emails or role_hits:
        confidence = "medium"
    else:
        confidence = "low"
    title = soup.title.get_text(" ").strip() if soup.title else ""
    if not result.get("school_name"):
        result["school_name"] = title[:100]
    result.update({
        "website": final_url,
        "scrape_status": "ok",
        "verified_emails": "; ".join(sorted(all_emails)),
        "generic_emails": "; ".join(generic),
        "phone_numbers": "; ".join(sorted(all_phones)[:8]),
        "pages_scraped": "; ".join(pages_ok),
        "role_signals": ", ".join(role_hits),
        "fit_score": fit_score,
        "fit_signals": fit_signals,
        "contact_confidence": confidence,
        "inferred_email_pattern": pattern,
        "people_candidates": " | ".join([f"{p['name']} ({p['roles']})" for p in people]),
        "inferred_contacts": " | ".join(inferred),
    })
    return result


def to_excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="prospects")
    return output.getvalue()


st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)
st.caption("Free workflow: discover schools by location, scrape public websites, score fit, and export Airtable-ready prospect data.")

with st.sidebar:
    st.header("1) Discovery inputs")
    locations_text = st.text_area("Locations, one per line", value="Cape Town, South Africa\nJohannesburg, South Africa", height=100)
    school_types = st.multiselect("Institution types", ["Schools", "Universities / Colleges"], default=["Schools"])
    max_results = st.slider("Max OSM results per location", 5, 100, 25, 5)
    st.divider()
    source_urls_text = st.text_area("Optional source/list pages or known school URLs, one per line", placeholder="https://example.com/best-international-schools-cape-town\nhttps://school.edu", height=120)
    scrape_after_discovery = st.checkbox("Scrape websites after discovery", value=True)

if "schools_df" not in st.session_state:
    st.session_state.schools_df = pd.DataFrame()
if "scraped_df" not in st.session_state:
    st.session_state.scraped_df = pd.DataFrame()

col1, col2 = st.columns([1, 1])
with col1:
    if st.button("Find schools", type="primary"):
        dfs = []
        errors = []
        for loc in [x.strip() for x in locations_text.splitlines() if x.strip()]:
            try:
                with st.spinner(f"Searching OpenStreetMap for {loc}..."):
                    dfs.append(overpass_query(loc, school_types, max_results))
            except Exception as e:
                errors.append(f"{loc}: {e}")
        # Source URL extraction
        source_urls = [x.strip() for x in source_urls_text.splitlines() if x.strip()]
        if source_urls:
            with st.spinner("Extracting school links from source URLs..."):
                link_df = extract_source_links(source_urls)
                if not link_df.empty:
                    candidates = link_df[link_df["status"] == "candidate"].copy()
                    if not candidates.empty:
                        candidates["school_name"] = candidates["anchor_text"]
                        candidates["website"] = candidates["candidate_url"]
                        candidates["source"] = "Source page extraction"
                        dfs.append(candidates[["school_name", "website", "source", "source_url"]])
                    # Also include directly pasted likely school URLs
                    direct_rows = []
                    for u in source_urls:
                        if is_probably_school_site(u):
                            direct_rows.append({"school_name": "", "website": norm_url(u), "source": "Direct URL"})
                    if direct_rows:
                        dfs.append(pd.DataFrame(direct_rows))
        if dfs:
            df = pd.concat(dfs, ignore_index=True)
            df["website"] = df.get("website", pd.Series([""] * len(df))).fillna("").map(norm_url)
            # Deduplicate: website domain first, else name+location
            df["_dedupe"] = df.apply(lambda r: domain_of(r.get("website", "")) or (str(r.get("school_name", "")).lower() + str(r.get("location_query", "")).lower()), axis=1)
            df = df[df["_dedupe"] != ""].drop_duplicates("_dedupe").drop(columns=["_dedupe"])
            st.session_state.schools_df = df
            st.session_state.scraped_df = pd.DataFrame()
            st.success(f"Found {len(df)} candidate schools/institutions.")
            if errors:
                st.warning("Some locations had errors: " + " | ".join(errors[:3]))
        else:
            st.warning("No candidates found. Try a broader city/metro, increase max results, or paste a source/list page.")

with col2:
    if st.button("Scrape candidate websites"):
        df = st.session_state.schools_df.copy()
        if df.empty:
            st.warning("Find schools first, or paste known school/source URLs and click Find schools.")
        else:
            rows = []
            progress = st.progress(0)
            for i, row in df.iterrows():
                rows.append(scrape_school(row.to_dict()))
                progress.progress((len(rows)) / len(df))
            out = pd.DataFrame(rows)
            # Sort high value first
            if "fit_score" in out.columns:
                out = out.sort_values(["fit_score", "contact_confidence"], ascending=[False, True])
            st.session_state.scraped_df = out
            st.success(f"Scraped {len(out)} school records.")

st.divider()

base_df = st.session_state.scraped_df if not st.session_state.scraped_df.empty else st.session_state.schools_df

if base_df.empty:
    st.info("Start by entering locations and clicking Find schools. Optional: paste listicle/directory pages to extract official school websites.")
else:
    st.subheader("Results")
    filter_cols = st.columns(4)
    with filter_cols[0]:
        min_fit = st.number_input("Minimum fit score", min_value=0, max_value=20, value=0)
    with filter_cols[1]:
        require_email = st.checkbox("Require visible email", value=False)
    with filter_cols[2]:
        confidence_filter = st.multiselect("Contact confidence", ["high", "medium", "low"], default=["high", "medium", "low"])
    with filter_cols[3]:
        keyword_filter = st.text_input("Keyword filter", placeholder="learning support, IB, admissions")

    view = base_df.copy()
    if "fit_score" in view.columns:
        view = view[pd.to_numeric(view["fit_score"], errors="coerce").fillna(0) >= min_fit]
    if require_email and "verified_emails" in view.columns:
        view = view[view["verified_emails"].fillna("").str.len() > 0]
    if "contact_confidence" in view.columns:
        view = view[view["contact_confidence"].fillna("low").isin(confidence_filter)]
    if keyword_filter:
        mask = view.astype(str).apply(lambda col: col.str.contains(keyword_filter, case=False, na=False)).any(axis=1)
        view = view[mask]

    st.dataframe(view, use_container_width=True, height=460)

    st.download_button("Download CSV", data=view.to_csv(index=False).encode("utf-8"), file_name="school_prospects_v7.csv", mime="text/csv")
    st.download_button("Download Excel", data=to_excel_bytes(view), file_name="school_prospects_v7.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.divider()
st.caption("Note: Visible emails are public page findings. Inferred contacts are unverified guesses and should be reviewed before outreach.")
