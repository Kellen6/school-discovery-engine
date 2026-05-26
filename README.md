# Prospect Discovery Engine v29

Streamlit app for discovering prospects by sector/location, resolving official websites, and enriching phones/emails.

## v29 update
- Country-aware domain handling for all countries, not just South Africa/Kenya.
- Uses ISO country lookup via `pycountry` to generate local domain patterns such as `.co.xx`, `.org.xx`, `.edu.xx`, `.ac.xx`, `.school.xx`, `.sch.xx`, and country TLDs.
- Keeps optimized Schools mode intact.
- Global domains like `.com`/`.org` are allowed, but treated cautiously outside the US unless page/title evidence strongly matches the prospect.

## Deploy
Upload these files to the root of your GitHub repo:
- `app.py`
- `requirements.txt`
- `README.md`

Then reboot the Streamlit app.
