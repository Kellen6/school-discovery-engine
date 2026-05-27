# Prospect Discovery Engine v40

Streamlit app for discovering prospects by sector/location, resolving official websites, and scraping contact details.

## v40 focus
- Preserves school-optimized discovery and custom search modes
- Retains prospects even when website identification fails
- Stricter website validation to avoid acronym/generic false positives
- Downgrades suspicious domains instead of marking them high confidence
- Extracts phones/emails from home/contact/admissions/staff pages, `mailto:`, `tel:`, and schema text

## Streamlit Cloud
Upload `app.py`, `requirements.txt`, and `README.md` to the root of your GitHub repo and deploy `app.py`.
