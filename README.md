# School Discovery Engine Free v12

Streamlit app with three separate discovery modes:

1. Map / geolocation
2. School name
3. School URL

v12 fixes:
- Keeps all candidates in export, including candidates with no website.
- Uses radius-based Overpass instead of fragile area queries.
- Supplements map results with Nominatim for better website coverage.
- Separates raw candidate export from enriched scrape export.
- Scrapes homepage + contact/admissions/staff/support pages.
- Extracts visible and obfuscated emails.
- Detects generic emails, role signals, fit score, contact confidence.
- Avoids dropping candidates just because website scraping fails.

## Streamlit Cloud

Upload these files to your GitHub repo:
- app.py
- requirements.txt
- README.md

Then reboot/redeploy the Streamlit app.

## Local run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```
