# School Discovery Engine Free v10

A free Streamlit app for Laura's school outreach prospecting.

## What v10 changes

The app now has three independent modes:

1. **Map / geolocation**: enter a city/area and radius, then use OpenStreetMap/Overpass to find schools.
2. **School name**: enter school names and optional location; the app tries public search-source methods to find likely official websites.
3. **School URL**: paste school websites directly; this is the most reliable mode.

All modes then run the same enrichment process:

- scrape homepage
- find contact/admissions/staff/support pages
- extract visible emails
- detect generic emails
- detect role keywords
- infer email patterns if possible
- calculate fit score and contact confidence
- export CSV / Excel

## Streamlit Cloud deploy

Upload all files in this folder to your GitHub repo root:

- `app.py`
- `requirements.txt`
- `README.md`

Then reboot the Streamlit app.

## Best way to test

Start with **School URL** mode using 3-5 known school websites. If that works, the scraping/enrichment layer is good.

Then try **School name** mode.

Then try **Map/geolocation** mode, which depends on OpenStreetMap coverage and can be inconsistent by city.

## Notes

- Inferred emails are not verified. The export clearly separates visible emails from inferred candidates.
- Some school websites block scraping or hide contact details behind PDFs/images/forms.
- For best results, paste school websites or source/list pages when map search is weak.
