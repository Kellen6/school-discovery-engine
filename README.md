# Prospect Discovery Engine v32

Streamlit app for discovering prospects by sector/location and enriching them with official websites, emails and phones.

## v32 highlights
- Optimized Schools mode preserved.
- Custom Search mode preserved.
- Stronger website identification with official-site validation and candidate retention.
- Conditional deep contact enrichment: extra pages/PDFs/search only when phone/email are missing.
- Extracts `mailto:`, `tel:`, schema/JSON-LD, visible text, and linked PDFs.
- Country-aware phone validation.
- Separates official websites from directory/contact-source pages.

## Deploy
Upload `app.py`, `requirements.txt`, and `README.md` to the root of your GitHub repo and redeploy on Streamlit Cloud.

## Optional Google Places
Add in Streamlit secrets:

```toml
GOOGLE_PLACES_API_KEY = "your_key"
```

Use Places only for unresolved websites to control cost.
