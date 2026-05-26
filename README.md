# School Discovery Engine v18.1

Free Streamlit prospect discovery/enrichment app.

## Run locally
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud
Upload `app.py`, `requirements.txt`, and this README to GitHub. In Streamlit Cloud, create a new app using `app.py` as the main file.

## v18.1 fix
When map/location/radius/sector inputs are unchanged, the app now skips the visible discovery/geocoding progress stage and shows only cached-candidate enrichment progress.
