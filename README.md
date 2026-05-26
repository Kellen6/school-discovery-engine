# Discovery & Contact Enrichment Engine v19

Streamlit app for discovering organizations and enriching websites, emails, phones, contact pages, and diagnostics.

## v19 changes
- Preserves discovered/source websites; enrichment never overwrites a known website with blank.
- Adds search fallback diagnostics: executed, queries run, URLs checked, phones/emails found, errors.
- Retries scrape failures via search fallback when enabled.
- Makes phone/email merge hierarchy explicit: website > search/directory > OSM.
- Keeps cached map candidates when map inputs do not change.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
