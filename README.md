# School Discovery Engine — Free v11

Three independent workflows:

1. Map / geolocation discovery using OpenStreetMap Overpass with Nominatim fallback.
2. School-name lookup.
3. Direct school URL or source/list page scraping.

The app scrapes official school websites for visible emails, generic emails, role signals, inferred email patterns, fit score, and contact confidence.

## Streamlit Cloud
Upload `app.py`, `requirements.txt`, and this README to GitHub, then deploy `app.py` from Streamlit Community Cloud.

## Local run
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Notes
Map mode depends on free public OSM infrastructure. If it returns schools without websites, use School name or School URL mode to scrape contacts.
