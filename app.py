import re
import time
from urllib.parse import urlparse, urljoin, quote_plus

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

APP_VERSION = "v4 - source-page extraction"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SchoolDiscoveryEngine/4.0; +local-use)"}
TIMEOUT = 12

LISTICLE_TERMS = [
    "best", "top", "directory", "directories", "rankings", "ranking", "list of", "schools in",
    "international-schools-database", "whichschooladvisor", "schoolguide", "schoolparrot", "edarabia",
    "expatica", "niche", "goodschools", "privateschoolreview", "wikipedia", "yellosa", "brabys"
]
DIRECTORY_DOMAINS = [
    "international-schools-database.com", "whichschooladvisor.com", "edarabia.com", "expatica.com",
    "schoolguide", "schoolparrot", "wikipedia.org", "yellosa", "brabys", "saschools.co.za",
    "privateschoolreview.com", "niche.com", "goodschoolsguide.co.uk", "schoolandcollegelistings.com"
]
SCHOOL_DOMAIN_HINTS = ["school", "college", "academy", "campus", "university", "institute", "education", "edu"]
BAD_DOMAINS = [
    "facebook.com", "instagram.com", "linkedin.com", "youtube.com", "twitter.com", "x.com",
    "google.com", "bing.com", "duckduckgo.com", "tripadvisor", "booking.com", "airbnb",
    "news24.com", "timeslive.co.za", "parent24.com", "amazon.", "apple.com"
]
CURRICULUM_KEYWORDS = {
    "IB": ["international baccalaureate", "ib diploma", "pyp", "myp"],
    "Cambridge": ["cambridge", "igcse", "a level", "a-level", "as level"],
    "CAPS": ["caps curriculum", "caps"],
    "American": ["american curriculum", "ap courses", "advanced placement"],
    "French": ["french curriculum", "lycée", "lycee", "ae fe", "aefe"],
    "Montessori": ["montessori"],
    "Waldorf": ["waldorf", "steiner"],
}
SIGNAL_KEYWORDS = {
    "Learning Support/SEN": ["learning support", "special educational needs", "sen", "inclusive education", "inclusion", "remedial", "neurodiverse", "neurodiversity"],
    "Counseling/University Guidance": ["university guidance", "college counseling", "career guidance", "guidance counsellor", "guidance counselor", "university counselling", "college applications"],
    "AI/Innovation": ["artificial intelligence", " ai ", "digital learning", "innovation", "edtech", "technology integration", "future ready"],
    "ELL/ESL": ["english language learner", "ell", "esl", "english as an additional language", "eal"],
}
ROLE_PATTERNS = ["principal", "head of school", "director", "counselor", "counsellor", "learning support", "admissions", "dean", "registrar"]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def root_domain(url: str) -> str:
    try:
        host = urlparse(normalize_url(url)).netloc.lower().replace("www.", "")
        return host
    except Exception:
        return ""


def fetch(url: str):
    try:
        r = requests.get(normalize_url(url), headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return None, f"HTTP {r.status_code}"
        return r.text, None
    except Exception as e:
        return None, str(e)[:120]


def is_bad_domain(url: str) -> bool:
    d = root_domain(url)
    return any(b in d for b in BAD_DOMAINS)


def is_source_page(url: str, title: str = "") -> bool:
    d = root_domain(url)
    text = f"{url} {title}".lower()
    return any(x in d for x in DIRECTORY_DOMAINS) or any(t in text for t in LISTICLE_TERMS)


def likely_school_site(url: str, title: str = "", text: str = "") -> bool:
    d = root_domain(url)
    if not d or is_bad_domain(url) or any(x in d for x in DIRECTORY_DOMAINS):
        return False
    hay = f"{d} {title} {text[:1000]}".lower()
    positive = any(h in hay for h in SCHOOL_DOMAIN_HINTS)
    school_words = ["school", "college", "academy", "students", "curriculum", "admissions", "campus", "principal"]
    return positive or sum(w in hay for w in school_words) >= 3


def ddg_search(query: str, max_results: int = 20):
    url = "https://duckduckgo.com/html/?q=" + quote_plus(query)
    html, err = fetch(url)
    results = []
    if not html:
        return results, err
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a.result__a")[:max_results]:
        href = a.get("href", "")
        title = a.get_text(" ", strip=True)
        if href.startswith("//duckduckgo.com/l/?uddg="):
            from urllib.parse import parse_qs, urlparse, unquote
            qs = parse_qs(urlparse("https:" + href).query)
            href = unquote(qs.get("uddg", [href])[0])
        if href.startswith("http"):
            results.append({"url": normalize_url(href), "title": title, "source_query": query})
    return results, None


def extract_outbound_school_links(source_url: str, limit: int = 80):
    html, err = fetch(source_url)
    extracted = []
    if not html:
        return extracted, err
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    base_domain = root_domain(source_url)
    for a in soup.find_all("a", href=True):
        href = urljoin(source_url, a["href"])
        if not href.startswith("http"):
            continue
        d = root_domain(href)
        if not d or d == base_domain or is_bad_domain(href):
            continue
        label = a.get_text(" ", strip=True)
        if likely_school_site(href, label):
            extracted.append({"url": normalize_url(href), "title": label or d, "found_via": source_url, "source_type": "extracted_from_source_page"})
    # dedupe preserving order
    seen, out = set(), []
    for x in extracted:
        d = root_domain(x["url"])
        if d not in seen:
            seen.add(d); out.append(x)
        if len(out) >= limit:
            break
    return out, None


def clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:20000]


def extract_emails(text: str):
    emails = sorted(set(e for e in EMAIL_RE.findall(text) if not e.lower().endswith((".png", ".jpg", ".jpeg"))))
    return emails[:10]


def detect_keywords(text: str):
    low = f" {text.lower()} "
    curricula = [name for name, kws in CURRICULUM_KEYWORDS.items() if any(k in low for k in kws)]
    signals = [name for name, kws in SIGNAL_KEYWORDS.items() if any(k in low for k in kws)]
    role_hits = [r for r in ROLE_PATTERNS if r in low]
    return curricula, signals, role_hits


def fit_score(curricula, signals, text):
    score = 0
    reasons = []
    if "Learning Support/SEN" in signals:
        score += 3; reasons.append("learning support/SEN")
    if "Counseling/University Guidance" in signals:
        score += 3; reasons.append("counseling/university guidance")
    if any(c in curricula for c in ["IB", "Cambridge", "American", "French"]):
        score += 2; reasons.append("international curriculum")
    if "AI/Innovation" in signals:
        score += 1; reasons.append("AI/innovation signal")
    if "ELL/ESL" in signals:
        score += 1; reasons.append("ELL/ESL")
    low = text.lower()
    if "admissions" in low:
        score += 1; reasons.append("admissions visible")
    return score, "; ".join(reasons)


def enrich_site(item):
    url = normalize_url(item["url"])
    html, err = fetch(url)
    if not html:
        return {"Website": url, "Domain": root_domain(url), "School Name": item.get("title", root_domain(url)), "Status": "Fetch failed", "Error": err}
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else item.get("title", root_domain(url))
    text = clean_text(html)
    curricula, signals, roles = detect_keywords(text)
    score, reasons = fit_score(curricula, signals, text)
    emails = extract_emails(text)
    official = likely_school_site(url, title, text)
    return {
        "School Name": title[:120],
        "Website": url,
        "Domain": root_domain(url),
        "Official Site Likely": official,
        "Source Type": item.get("source_type", "direct_search_result"),
        "Found Via": item.get("found_via", item.get("source_query", "manual")),
        "Curriculum Signals": ", ".join(curricula),
        "Opportunity Signals": ", ".join(signals),
        "Role Keywords Found": ", ".join(roles[:8]),
        "Emails Found": ", ".join(emails),
        "Fit Score": score,
        "Score Reasons": reasons,
        "Outreach Status": "Not started",
        "Owner": "Laura",
        "Next Step": "Review contact page / find decision makers",
        "Status": "OK",
        "Error": "",
    }


st.set_page_config(page_title="School Discovery Engine", layout="wide")
st.title("School Discovery Engine")
st.caption(APP_VERSION + " · Free workflow · no paid APIs")

with st.sidebar:
    st.header("Search targets")
    countries = st.text_area("Countries / cities", "Cape Town, South Africa\nJohannesburg, South Africa\nDurban, South Africa")
    segments = st.text_area("Segments", "international schools\nprivate schools\ncambridge schools\nIB schools\nlearning support schools")
    max_results = st.slider("Search results per query", 5, 40, 15)
    scrape_sources = st.checkbox("Use best-of/directories as discovery sources", True)
    official_only = st.checkbox("Final export: official school sites only", True)
    min_score = st.slider("Minimum fit score", 0, 10, 0)
    manual_urls = st.text_area("Optional: paste school or directory URLs, one per line", "")
    run = st.button("Find schools", type="primary")

st.markdown("""
This version uses “best schools” and directory pages as **source pages**. It visits those pages, extracts outbound school links, and exports only likely official school websites.
""")

if run:
    queries = []
    for place in [x.strip() for x in countries.splitlines() if x.strip()]:
        for seg in [x.strip() for x in segments.splitlines() if x.strip()]:
            queries.append(f"{seg} {place}")
            if scrape_sources:
                queries.append(f"best {seg} in {place}")
                queries.append(f"top {seg} in {place}")
    candidates = []
    source_pages = []
    progress = st.progress(0)
    log = st.empty()
    total = max(len(queries), 1)
    for i, q in enumerate(queries):
        log.write(f"Searching: {q}")
        results, err = ddg_search(q, max_results=max_results)
        for r in results:
            if is_source_page(r["url"], r.get("title", "")) and scrape_sources:
                source_pages.append(r)
            elif likely_school_site(r["url"], r.get("title", "")):
                r["source_type"] = "direct_search_result"
                r["found_via"] = q
                candidates.append(r)
        progress.progress((i + 1) / total)
        time.sleep(0.2)

    # manual URLs can be schools or source pages
    for u in [x.strip() for x in manual_urls.splitlines() if x.strip()]:
        item = {"url": normalize_url(u), "title": root_domain(u), "source_query": "manual"}
        if is_source_page(u) and scrape_sources:
            source_pages.append(item)
        else:
            item["source_type"] = "manual_url"; item["found_via"] = "manual"
            candidates.append(item)

    extracted_total = 0
    if scrape_sources and source_pages:
        st.info(f"Found {len(source_pages)} source/list pages. Extracting official school links from them...")
        for sp in source_pages[:50]:
            links, err = extract_outbound_school_links(sp["url"])
            extracted_total += len(links)
            candidates.extend(links)

    # dedupe by domain
    seen, unique = set(), []
    for c in candidates:
        d = root_domain(c["url"])
        if d and d not in seen and not is_bad_domain(c["url"]):
            seen.add(d); unique.append(c)

    if not unique:
        st.warning("No candidate school sites found. Try broader geography/segments or paste directory URLs manually.")
    else:
        st.info(f"Enriching {len(unique)} candidate official sites... extracted {extracted_total} links from source pages.")
        rows = []
        prog2 = st.progress(0)
        for i, item in enumerate(unique):
            rows.append(enrich_site(item))
            prog2.progress((i + 1) / len(unique))
        df = pd.DataFrame(rows)
        if official_only:
            df = df[df["Official Site Likely"] == True]
        df = df[df["Fit Score"] >= min_score]
        df = df.sort_values(["Fit Score", "School Name"], ascending=[False, True]).reset_index(drop=True)
        st.success(f"Found {len(df)} likely official school prospects.")
        st.dataframe(df, use_container_width=True)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("Download Airtable-ready CSV", csv, "school_prospects_v4.csv", "text/csv")
        # Excel
        import io
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="School Prospects")
        st.download_button("Download Excel", buf.getvalue(), "school_prospects_v4.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
