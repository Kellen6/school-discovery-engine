# School Discovery Engine v15

Free Streamlit app for Laura's school outreach workflow.

## What v15 does

- Discover schools by:
  1. Map / geolocation
  2. School name
  3. School URL
  4. Source/list page
- Resolve missing websites using free search/domain guesses
- Scrape school websites for:
  - visible emails
  - generic emails
  - contact/admissions/staff/support pages
  - phone numbers from homepage, footer and contact pages
- Optional web-search fallback for missing contact details and phones
- Separates phone sources:
  - `osm_phone`
  - `website_phone`
  - `search_phone`
  - `directory_phone`
  - `best_phone`
  - `phone_source`
  - `phone_confidence`
  - `all_phones_found`
- Exports raw CSV, enriched CSV and Excel workbook with unique filenames by mode/query/timestamp.

## Streamlit Cloud deployment

Replace your GitHub repo files with the contents of this folder, commit, then reboot the Streamlit app.

Main file: `app.py`

## Recommended settings for first run

For Cape Town:

- Radius: 10–25 km
- Max candidates: 100
- Max missing websites to resolve: 25–40
- Max rows to enrich per run: 25–40
- Scraping speed: 2–3
- Use web search fallback: OFF for first run, ON for smaller batches

The search fallback is slower because it searches for missing websites, emails, and phone numbers when the school site does not expose them clearly.

## Notes

Search-derived contact details are marked as unverified. Review before outreach.
