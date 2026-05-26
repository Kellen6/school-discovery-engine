# School Discovery Engine Free v9

Cloud-safe Streamlit app for Laura's school outreach.

## What v9 changes

- Does not rely on Google/DuckDuckGo scraping.
- Uses three discovery paths:
  1. Location search via Nominatim + OpenStreetMap Overpass.
  2. Source/list pages: paste "best schools" or directory URLs and it extracts official school links.
  3. Direct school URLs.
- Adds a Debug tab to show exactly where hosted discovery is failing.
- Scrapes discovered school websites for:
  - contact/admissions/staff pages
  - visible emails
  - generic emails
  - role signals
  - email pattern inference
  - fit score
  - contact confidence
- Exports CSV and Excel.

## Run locally

```bash
cd school_discovery_engine_free_v9
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Community Cloud

Upload `app.py`, `requirements.txt`, and `README.md` to your GitHub repo, commit, then reboot your Streamlit app.

## Usage

Start with:

- Location: `Cape Town, Western Cape, South Africa`
- Radius: `100 km`
- Max OSM results: `150`

If no results appear, open the Debug tab and run connectivity tests. If Overpass is blocked or empty, paste source pages or official school URLs in the sidebar.
