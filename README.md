# Prospect Discovery Engine v36

Streamlit app for prospect discovery by sector/location.

Key v36 fixes:
- Candidate retention: website failures never remove prospects.
- Broader school discovery: more OSM tags and school search terms.
- Retention diagnostics: raw found, no-name Overpass elements, false positives, duplicates, retained prospects.
- Conditional contact enrichment: when phone/email are missing, bounded contact search fallback runs.
- Search-derived email/phone fields retained separately.

Deploy on Streamlit Community Cloud with `app.py` as the entrypoint.
