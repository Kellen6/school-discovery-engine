# Prospect Discovery Engine v27

Streamlit app for prospect discovery and contact enrichment.

## What's new in v27

- Keeps **Schools (optimized)** as a first-class mode so the school-specific website/contact improvements are preserved.
- Adds **Custom search** with algorithmic profile expansion from user input.
- Example: entering `physical therapists` expands into related search terms such as physiotherapist, physio, physiotherapy clinic, rehabilitation clinic, and relevant contact pages such as appointments, team, services, and contact.
- Shows the generated profile before running, so the user can see the detected category, search terms, priority pages, target roles, and exclude terms.
- Still supports optional Google Places via Streamlit secrets.

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Cloud

Upload these files to the root of your GitHub repo:

- `app.py`
- `requirements.txt`
- `README.md`

Then reboot the Streamlit app.

## Optional Google Places

In Streamlit Cloud secrets:

```toml
GOOGLE_PLACES_API_KEY = "your_key_here"
```

The app uses Google Places only when available and helpful for website/phone resolution.
