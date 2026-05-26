# Prospect Discovery & Contact Finder v21

A free Streamlit app for discovering prospects and enriching them with websites, phones, emails, and outreach-ready contact fields.

## What changed in v21

- Faster Fast mode: closest to the earlier v18-style behavior.
- Better website resolution from prospect name searches.
- Better official-site scoring and parent/campus handling.
- Filters out obvious false positives such as driving/testing yards for school searches.
- One user-facing table: **Prospects**.
- One export area that downloads the most complete table currently available.
- CSV and Excel exports with unique timestamped filenames.
- Better first-run/cache messaging.
- Timing diagnostics so you can see whether slowdown is discovery or enrichment.

## Local run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud

Upload these files to GitHub and deploy `app.py` as the main file.

## Recommended settings

For quick runs:
- Contact search depth: **Fast — website only**

For better contact coverage:
- Contact search depth: **Standard — website + limited web fallback**

For maximum coverage but slower runs:
- Contact search depth: **Deep — slower, broader web fallback**
