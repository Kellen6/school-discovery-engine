# School Discovery Engine Free v7

A free Streamlit app for discovering schools by location and scraping public school websites for contact details.

## What v7 does

- Finds candidate schools using OpenStreetMap / Overpass by location.
- Optionally extracts official school links from pasted source/list pages.
- Scrapes school homepages, contact pages, admissions pages, staff/team pages, leadership pages, and support pages.
- Extracts visible emails, phone numbers, titles, role signals, and page sources.
- Detects generic emails such as info@, admissions@, office@.
- Attempts to infer email patterns only when multiple visible staff emails exist.
- Scores fit for Laura's outreach based on curriculum, learning support/SEN, university counseling, AI/innovation, inclusion, admissions, and parent signals.
- Exports Airtable-ready CSV and Excel files.

## Important limitations

This app only uses public web pages. It does not bypass paywalls, CAPTCHAs, robots restrictions, or JavaScript-only content. Inferred emails are guesses and are clearly marked as unverified.

## Run locally on Mac

```bash
cd ~/Downloads/school_discovery_engine_free_v7
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Upload these files to a public GitHub repo:
   - app.py
   - requirements.txt
   - README.md
2. Go to https://share.streamlit.io
3. Create a new app from that GitHub repo.
4. Main file path: `app.py`
5. Deploy.

## Suggested workflow

1. Enter a location such as `Cape Town, South Africa` or `Lagos, Nigeria`.
2. Select school types and max results.
3. Click `Find schools`.
4. Review candidates.
5. Click `Scrape selected/candidate school websites`.
6. Download CSV/Excel and upload to Airtable.

## Columns to watch

- `verified_emails`: emails visibly found on public pages.
- `inferred_email_pattern`: detected pattern if enough visible emails exist.
- `inferred_contacts`: possible unverified emails generated from names/titles.
- `contact_confidence`: high/medium/low based on visible contacts and staff pages.
- `fit_score`: Laura-fit score for outreach prioritization.
