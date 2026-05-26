# Prospect Discovery Engine v23

Streamlit app for discovering and enriching school/prospect records.

## Deploy on Streamlit Community Cloud
Upload these files to GitHub and set `app.py` as the main file.

## What changed in v23
- Stronger website resolver before scraping.
- Keeps likely website candidates when confidence is not high enough.
- Better official-site scoring using school name, location, domain similarity, and exclusion of directories/social/search pages.
- Better first-run/cached-run messaging.
- One Prospects table and one export area.
- Timing diagnostics.

## Notes
Search-engine fallback is best-effort and may be rate limited by hosted environments. If a website cannot be confidently resolved, check the `website_candidates` column.
