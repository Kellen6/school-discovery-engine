# School Discovery Engine v16

Free Streamlit app for discovering schools, resolving websites, scraping contact details, and exporting outreach-ready CSV/Excel files.

## v16 changes

- Defaults to **enrich all discovered rows** instead of silently capping enrichment.
- Optional enrichment limit is still available for very large runs on Streamlit Cloud.
- Country-aware phone validation using `phonenumbers`.
- Phone extraction now matches the inferred country from the search/location hint, instead of assuming South Africa only.
- Better phone columns remain: `osm_phone`, `website_phone`, `search_phone`, `directory_phone`, `best_phone`, `phone_source`, `phone_confidence`, `all_phones_found`.
- Download filenames include mode/query/date-time so repeated exports do not overwrite each other.

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

Upload these files to your GitHub repo:

- `app.py`
- `requirements.txt`
- `README.md`

Then reboot the Streamlit app.

## Recommended settings

For a normal city search:

- Enrich all discovered rows: ON
- Scraping speed: 2 or 3
- Max missing websites to resolve: 100-150
- Web search fallback: OFF first, ON for a second pass or smaller batches

For large country/region searches:

- Enrich all discovered rows: OFF
- Optional max rows to enrich: 100-200
- Use the raw export to preserve all discovered candidates
