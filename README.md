# School Discovery Engine Free v8

This is the Streamlit-hosted/free workflow version.

## What changed in v8

v7 could return `No candidates found` because the Overpass/OpenStreetMap query was too brittle on Streamlit Cloud.

v8 fixes this by:

- Using Nominatim geocoding for the location.
- Searching within a configurable radius around the city/metro.
- Using corrected Overpass syntax.
- Trying multiple public Overpass endpoints.
- Increasing default max results.
- Keeping source/list-page extraction and school website scraping.

## What it does

- Finds schools/universities/colleges by location using OpenStreetMap.
- Lets you paste source/list pages and known school URLs.
- Scrapes candidate school websites for public contact details.
- Extracts visible emails, generic emails, phone numbers, role signals, staff/contact/admissions/support pages.
- Infers email patterns only when enough visible emails exist.
- Scores Laura-fit based on international curriculum, learning support/SEN, university counseling, AI/innovation, inclusion, admissions, and parent signals.
- Exports CSV/Excel for Airtable.

## Run locally

```bash
cd ~/Downloads/school_discovery_engine_free_v8
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

Upload/replace these files in GitHub:

- app.py
- requirements.txt
- README.md

Then reboot the app in Streamlit.

## If a city still returns no results

Try:

- Increase radius to 100–150 km.
- Increase max results to 100–200.
- Use a more specific metro/city: `Cape Town, Western Cape, South Africa` instead of just `Cape Town`.
- Paste one or two source/list pages; the app will use them as discovery sources and extract official school sites.

