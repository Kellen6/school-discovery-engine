# Prospect Discovery Engine v37

Recovery/merge build.

## What this version does
- Restores v35 discovery behavior so prospects are not lost upstream.
- Keeps prospects even when website resolution fails.
- Uses less aggressive deduplication: name + address/location, not just name.
- Adds conditional contact search fallback when website scraping misses email or phone.
- Adds `search_emails` and `search_phone` fields.
- Preserves radius slider, optimized Schools mode, Custom Search mode, progress bars, metrics, CSV and Excel export.

## Streamlit
Deploy with `app.py` as the main file.
