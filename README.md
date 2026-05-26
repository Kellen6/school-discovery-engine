# School Discovery Engine v14.2

Streamlit app for school prospect discovery and contact scraping.

## What changed in v14.2
- Prevents apparent hangs by adding hard caps for website resolution and enrichment.
- Web search fallback for contacts is now OFF by default because it is slow on Streamlit Cloud.
- Adds smaller default worker count and shorter HTTP timeouts.
- Keeps raw candidates even when enrichment is skipped or capped.
- Downloads preserve results.

## Recommended Streamlit Cloud settings
For a first run:
- Scraping speed: 3
- Max missing websites to resolve: 25–40
- Max rows to enrich per run: 25–40
- Use web search fallback for missing contacts: OFF

After confirming the app works, turn search fallback ON for a smaller batch.

## Files
- `app.py`
- `requirements.txt`
- `README.md`
