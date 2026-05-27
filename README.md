# Prospect Discovery Engine v34

Streamlit prospect discovery and enrichment tool.

## Deploy on Streamlit Community Cloud

1. Upload `app.py`, `requirements.txt`, and `README.md` to the root of your GitHub repo.
2. In Streamlit Cloud, deploy `app.py`.
3. Optional: add `GOOGLE_PLACES_API_KEY` in Streamlit Secrets for better website/phone coverage.

## v34 focus

- Restores the stronger website resolver baseline from v26/v33.
- Keeps optimized Schools mode and Custom Search mode.
- Keeps algorithmic custom profile expansion.
- Keeps optional Google Places support.
- Adds safer contact enrichment: homepage/contact/admissions/team pages, `mailto:`, `tel:`, visible text, JSON-LD, and optional PDFs.
- Maintains sector/location on the main page, advanced controls in the sidebar, stacked progress bars, one Prospects table, and CSV/Excel export.
