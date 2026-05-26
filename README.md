# School Discovery Engine v18

Free Streamlit workflow for discovering and enriching outreach prospects.

## What's new in v18

- Stronger phone/contact enrichment:
  - website footer/contact/admissions/staff/leadership pages
  - search fallback queries for phone, contact, admissions, office, reception, campus, principal, and staff
  - separate phone columns: `osm_phone`, `website_phone`, `search_phone`, `directory_phone`, `best_phone`, `phone_source`, `phone_confidence`, `all_phones_found`
- Country-aware phone validation using `phonenumbers`.
- Progress/status bar during discovery and enrichment.
- Download filenames include discovery mode, sector/query label, and timestamp.
- Sector profile architecture for future expansion beyond schools:
  - Schools / education
  - Higher education
  - Healthcare providers
  - NGOs / nonprofits
  - Businesses / companies

Schools are the most mature profile. Other profiles are templates that can be expanded with sector-specific search terms, map tags, contact paths, and scoring logic.

## Local run

```bash
cd school_discovery_engine_free_v18
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud

Upload these files to your GitHub repo root:

- `app.py`
- `requirements.txt`
- `README.md`

Then reboot the app in Streamlit Cloud.

## Suggested settings for Cape Town school runs

- Sector profile: Schools / education
- Mode: Map / geolocation
- Location: Cape Town, Western Cape, South Africa
- Radius: 10–25 km
- Max candidates: 50–100
- Scrape contacts after discovery: ON
- Use web search fallback: ON for better phone/contact coverage, OFF for faster testing
- Scraping speed: 2–3 on Streamlit Cloud


## v18 note
Map/geolocation discovery is cached. If location, radius, sector profile, and max candidates are unchanged, the app reuses the same candidate set and only reruns website/contact enrichment. Clear results resets the cache.
