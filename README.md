# Discovery & Contact Enrichment Engine v20

Free Streamlit app for finding prospects and enriching contact details. It currently ships with a school profile, but the code is structured around sector profiles so it can be extended to clinics, NGOs, universities, companies, or other prospect categories.

## What v20 improves

- Faster default enrichment via contact search depth controls
- Cleaner user-facing buttons and table fields
- Candidate and enriched downloads in both CSV and Excel
- Timestamped filenames by mode/query/date-time
- Cached map discovery: rerunning contact enrichment does not rerun geocoding/map discovery unless map inputs change
- Progress/status indicators during discovery and enrichment
- Diagnostics moved into an expandable section

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

Upload these files to your GitHub repo:

- app.py
- requirements.txt
- README.md

Then deploy with `app.py` as the main file.

## Recommended workflow

1. Choose `Schools` as the sector profile.
2. Choose `Map / geolocation`.
3. Enter the location and max candidates.
4. Use `Standard — website + limited web fallback` for normal runs.
5. Use `Fast — website only` for quick testing.
6. Use `Deep` only for smaller batches or when you need more complete contacts.
7. Download enriched Excel for outreach.

## Notes

This app uses public web pages and free public map/search sources. Results vary depending on source availability, site blocking, and whether schools publish contact information publicly. Inferred or search-derived results should be reviewed before outreach.
