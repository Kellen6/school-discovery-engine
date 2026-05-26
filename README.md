# School Discovery Engine v14

Free Streamlit workflow for finding schools, resolving websites, scraping public contact details, and exporting outreach-ready CSV/Excel files.

## What v14 adds

- Website scraping for school home/contact/admissions/staff/support pages
- Web search fallback when scraping does not find useful contact details
- Search-derived contacts are clearly labelled as unverified
- Separate columns for:
  - `visible_emails`
  - `generic_emails`
  - `search_emails`
  - `search_generic_emails`
  - `search_contact_sources`
  - `contact_source`
  - `contact_confidence`
- Persistent results after CSV download
- Raw candidate export + enriched export

## Discovery modes

1. **Map / geolocation**  
   Enter a city/metro and radius. Uses Nominatim/OSM plus fallbacks.

2. **School name**  
   Paste school names, one per line. The app attempts to resolve websites and scrape contacts.

3. **School URL**  
   Paste official school websites, one per line. Best option when you already know targets.

4. **Source/list page**  
   Paste “best schools” / directory/list URLs. The app extracts likely official school links and scrapes those.

## Contact source labels

- `website`: email/contact found on the school website
- `website + search_result`: found on school website and additional web search sources
- `search_result`: found through web search fallback
- `website_no_email`: website scraped but no visible email found
- `website_unreachable_or_no_contact`: website exists but could not be scraped/useful contacts were not found
- `no_website_search_attempted`: no website available, but search fallback was attempted

## Important caution

Search-derived contacts are not guaranteed to be verified. Review `search_contact_sources` before outreach.

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

Upload these files to a GitHub repo:

- `app.py`
- `requirements.txt`
- `README.md`

Then deploy with main file:

```text
app.py
```
