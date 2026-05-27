# Prospect Discovery Engine v40.3

Streamlit app for prospect discovery and contact enrichment.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## v40.3 fixes
- Adds Photon candidate-discovery fallback when Nominatim search is blocked.
- Adds Overpass POST/GET fallback and clearer provider diagnostics.
- Geocoding can recover from Nominatim 403 via Photon.
- Website validation does not remove prospects.
