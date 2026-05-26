import re
import io
import time
from urllib.parse import urlparse, urljoin

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(page_title="School Discovery Engine", layout="wide")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

BAD_DOMAINS = [
    "google.", "bing.", "duckduckgo.", "facebook.com", "instagram.com", "linkedin.com", "youtube.com",
    "x.com", "twitter.com", "tiktok.com", "wikipedia.org", "tripadvisor.", "yelp.", "reddit.com",
    "schoolguide", "schooladvisor", "privateschoolreview", "world-schools.com", "internationalschoolsdatabase",
    "whichschooladvisor", "schools4sa", "saschools", "schoolparrot", "edarabia", "best-schools", "top-schools",
    "expatica", "momspresso", "careersportal", "briefly.co.za", "safacts", "application", "ranking",
]

AGGREGATOR_HINTS = [
    "best", "top", "directory", "list", "ranking", "rankings", "review", "reviews", "compare", "comparison",
    "schools in", "school finder", "find a school", "which school", "international schools database",
]

SCHOOL_HINTS = [
    "school", "college", "academy", "campus", "university", "institute", "lycee", "lycée",
    "international", "primary", "secondary", "preparatory", "prep", "high school", "education",
]

CURRICULUM_KEYWORDS = {
    "IB": ["international baccalaureate", "ib diploma", "ib programme", "pyp", "myp"],
    "Cambridge": ["cambridge", "igcse", "a level", "a-level", "as level", "cie"],
    "CAPS": ["caps curriculum", "national curriculum statement"],
    "American/AP": ["american curriculum", "advanced placement", " ap ", "sat"],
    "British": ["british curriculum", "gcse", "a-level", "key stage"],
    "French": ["french curriculum", "lycée", "lycee", "baccalauréat", "baccalaureat"],
}

FIT_KEYWORDS = {
    "learning_support": ["learning support", "special needs", "sen", "send", "inclusive education", "inclusion", "neurodiversity", "remedial"],
    "college_counseling": ["college counseling", "university counselling", "university counseling", "career guidance", "university placement", "higher education guidance"],
    "ai_tech": ["artificial intelligence", " ai ", "digital learning", "edtech", "innovation", "technology integration", "coding", "robotics"],
    "ell_language": ["english language learner", "eal", "ell", "esl", "language support"],
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

DEFAULT_SOURCE_URLS = """https://www.international-schools-database.com/in/cape-town
https://www.international-schools-database.com/in/johannesburg
https://www.world-schools.com/region/south-africa/
"""


def normalize_url(url: str) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    if not re.match(r"^https?://", url):
        url = "https://" + url
    try:
        p = urlparse(url)
        if not p.netloc:
            return None
        return p.geturl().split("#")[0]
    except Exception:
        return None


def domain(url: str) -> str:
    try:
        d = urlparse(url).netloc.lower()
        if d.startswith("www."):
            d = d[4:]
        return d
    except Exception:
        return ""


def fetch(url: str, timeout=12) -> tuple[str, str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        ctype = r.headers.get("content-type", "")
        if r.status_code >= 400:
            return "", f"HTTP {r.status_code}"
        if "text/html" not in ctype and "application/xhtml" not in ctype and ctype:
            return "", f"Not HTML: {ctype[:40]}"
        return r.text[:1_500_000], "OK"
    except Exception as e:
        return "", str(e)[:120]


def is_bad_domain(d: str) -> bool:
    return any(b in d for b in BAD_DOMAINS)


def looks_like_aggregator(url: str, title: str = "", text: str = "") -> bool:
    s = " ".join([url, title, text[:1000]]).lower()
    return any(h in s for h in AGGREGATOR_HINTS) or is_bad_domain(domain(url))


def looks_like_school_url(url: str, anchor: str = "") -> bool:
    d = domain(url)
    if not d or is_bad_domain(d):
        return False
    s = f"{d} {url} {anchor}".lower()
    if any(x in s for x in ["/tag/", "/category/", "/blog/", "/news/", "?", "#"]):
        # not automatically bad, but less likely as root official site
        pass
    return any(h in s for h in SCHOOL_HINTS) or d.endswith(".edu") or d.endswith(".ac.za")


def extract_links(source_url: str, html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        full = urljoin(source_url, href)
        norm = normalize_url(full)
        if not norm:
            continue
        text = " ".join(a.get_text(" ", strip=True).split())[:160]
        links.append({"url": norm, "anchor": text, "source_url": source_url})
    return links


def root_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def title_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    if soup.title and soup.title.string:
        return soup.title.string.strip()[:180]
    h1 = soup.find("h1")
    return h1.get_text(" ", strip=True)[:180] if h1 else ""


def detect_curriculum(text: str) -> str:
    lower = f" {text.lower()} "
    found = []
    for label, kws in CURRICULUM_KEYWORDS.items():
        if any(kw in lower for kw in kws):
            found.append(label)
    return ", ".join(found)


def score_fit(text: str, url: str) -> tuple[int, list[str]]:
    lower = f" {text.lower()} "
    score = 0
    reasons = []
    for label, kws in FIT_KEYWORDS.items():
        if any(kw in lower for kw in kws):
            if label == "learning_support":
                score += 3; reasons.append("Learning support/SEN signal")
            elif label == "college_counseling":
                score += 3; reasons.append("College/university counseling signal")
            elif label == "ai_tech":
                score += 2; reasons.append("AI/technology signal")
            elif label == "ell_language":
                score += 2; reasons.append("ELL/language support signal")
    if any(x in lower for x in ["international", "cambridge", "ib diploma", "a-level", "igcse"]):
        score += 2; reasons.append("International curriculum/school signal")
    if any(x in domain(url) for x in ["school", "college", "academy"]):
        score += 1; reasons.append("Likely official school domain")
    return score, reasons


def extract_emails(text: str) -> str:
    emails = sorted(set(e.lower() for e in EMAIL_RE.findall(text)))
    bad = ["example.com", "domain.com", "email.com"]
    emails = [e for e in emails if not any(b in e for b in bad)]
    return ", ".join(emails[:10])


def enrich_school(url: str, source_url: str = "Direct/source") -> dict:
    html, status = fetch(url)
    if not html:
        return {"website": url, "domain": domain(url), "source_url": source_url, "fetch_status": status, "school_name": "", "fit_score": 0, "fit_reasons": "", "curriculum_signals": "", "emails_found": "", "page_title": ""}
    text = visible_text(html)
    title = title_text(html)
    score, reasons = score_fit(text, url)
    return {
        "school_name": title.replace(" - Home", "").replace(" | Home", "")[:120],
        "website": url,
        "domain": domain(url),
        "source_url": source_url,
        "page_title": title,
        "fit_score": score,
        "fit_reasons": "; ".join(reasons),
        "curriculum_signals": detect_curriculum(text),
        "emails_found": extract_emails(text),
        "fetch_status": status,
        "outreach_status": "Not started",
        "notes": "",
    }


def process_sources(source_urls: list[str], max_links_per_source: int, max_final: int, include_direct: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidate_map = {}
    source_log = []
    for su in source_urls:
        html, status = fetch(su)
        source_log.append({"source_url": su, "domain": domain(su), "fetch_status": status})
        if not html:
            continue
        text = visible_text(html)
        title = title_text(html)
        # If the source itself looks like an official school, include it.
        if include_direct and looks_like_school_url(su, title) and not looks_like_aggregator(su, title, text):
            candidate_map[root_url(su)] = {"url": root_url(su), "source_url": su, "anchor": "direct official-looking URL"}
        links = extract_links(su, html)[:max_links_per_source]
        for link in links:
            ru = root_url(link["url"])
            if looks_like_school_url(ru, link.get("anchor", "")):
                if ru not in candidate_map:
                    candidate_map[ru] = {"url": ru, "source_url": su, "anchor": link.get("anchor", "")}
    candidates = list(candidate_map.values())[:max_final]
    rows = []
    progress = st.progress(0, text="Enriching candidate school websites...") if candidates else None
    for i, c in enumerate(candidates):
        rows.append(enrich_school(c["url"], c["source_url"]))
        if progress:
            progress.progress((i + 1) / len(candidates), text=f"Enriched {i+1}/{len(candidates)}")
        time.sleep(0.1)
    if progress:
        progress.empty()
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["fit_score", "school_name"], ascending=[False, True])
    return df, pd.DataFrame(source_log)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="schools")
    return output.getvalue()


st.title("School Discovery Engine — Free v5")
st.caption("Use listicles/directories as discovery sources, but export official school websites only.")

with st.sidebar:
    st.header("Inputs")
    st.markdown("Paste source pages or official school websites, one per line.")
    urls_text = st.text_area("Source URLs", value=DEFAULT_SOURCE_URLS, height=190)
    include_direct = st.checkbox("Include pasted official school URLs directly", value=True)
    max_links = st.slider("Max links scanned per source page", 20, 400, 150, 10)
    max_final = st.slider("Max candidate school sites to enrich", 10, 200, 50, 10)
    min_score = st.slider("Minimum fit score shown", 0, 12, 0, 1)
    st.markdown("---")
    st.markdown("If search fails on Streamlit Cloud, use Google manually to find best/top/directory pages, then paste those URLs here.")

source_urls = [normalize_url(x) for x in urls_text.splitlines() if normalize_url(x)]

col1, col2 = st.columns([1, 3])
with col1:
    run = st.button("Find schools", type="primary")
with col2:
    st.write(f"{len(source_urls)} source URL(s) ready")

if run:
    if not source_urls:
        st.error("Paste at least one source URL or official school website.")
    else:
        with st.spinner("Scraping source pages and extracting official school websites..."):
            df, log = process_sources(source_urls, max_links, max_final, include_direct)
        st.subheader("Source page diagnostics")
        st.dataframe(log, use_container_width=True)
        if df.empty:
            st.warning("No official school websites found. Try broader/list-style source pages or paste official school URLs directly.")
        else:
            filtered = df[df["fit_score"] >= min_score].copy()
            st.subheader(f"Prospects found: {len(filtered)}")
            st.dataframe(filtered, use_container_width=True, hide_index=True)
            csv = filtered.to_csv(index=False).encode("utf-8")
            st.download_button("Download CSV for Airtable", csv, "school_prospects.csv", "text/csv")
            st.download_button("Download Excel", to_excel_bytes(filtered), "school_prospects.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("Paste source/list pages in the sidebar, then click Find schools.")
    st.markdown("""
### Recommended free workflow
1. Search Google manually for phrases like `best international schools Cape Town`, `private schools Johannesburg`, or `IB schools South Africa`.
2. Paste the result pages/listicles/directories into the sidebar.
3. The app extracts likely official school websites, scores them, and exports Airtable-ready CSV.

This avoids relying on automated search, which cloud hosts often get blocked from using.
""")
