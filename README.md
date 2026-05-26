# Prospect Discovery Engine v26

Streamlit prospect discovery app for finding prospects, resolving official websites, and scraping contact details.

## What changed in v26

- Much stronger website resolver for schools with missing map websites.
- Better domain guessing for acronyms and parent institutions/campuses.
- More website search queries in Normal and Extra thorough modes.
- Optional Google Places support for near-complete official website and phone coverage.
- Sector and Location remain on the main page.
- Advanced settings remain in the sidebar.
- Stacked progress bars remain visible.
- One Prospects table and CSV/Excel exports.

## Optional Google Places API

Free search/scraping can miss official websites because public search pages can block hosted apps. For much higher coverage, add this to Streamlit Cloud secrets:

```toml
GOOGLE_PLACES_API_KEY = "your_key_here"
```

The app still runs without this key, but website coverage will be less reliable.

## Streamlit Cloud deployment

Upload these files to the root of your GitHub repo:

- `app.py`
- `requirements.txt`
- `README.md`

Then reboot the Streamlit app.
