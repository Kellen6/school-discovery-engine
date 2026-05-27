# Prospect Discovery Engine v35

Key v35 fixes:
- Website failure never removes a prospect.
- Only obvious false positives are filtered.
- Dedupe is less aggressive: name + address/location, not name alone.
- Restores on-page metric cards: Prospects, Websites, Emails, Phones.
- Adds retention diagnostics: raw found, false positives removed, duplicates removed, retained prospects.
- Keeps radius slider, optimized Schools mode, Custom Search mode, progress bars, CSV and Excel export.

Deploy on Streamlit Community Cloud with `app.py` as the entrypoint.
