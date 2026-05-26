# School Discovery Engine Free v5

This version is designed for free hosting on Streamlit Community Cloud.

Important: public search engines often block automated queries from cloud servers. v5 therefore uses a more reliable workflow:

1. Paste source pages such as "best schools", school directory pages, IB/Cambridge pages, or official school URLs.
2. The app scrapes outbound links.
3. It keeps likely official school websites.
4. It enriches and scores those schools.
5. Export CSV/Excel for Airtable.

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

Upload these files to GitHub:
- app.py
- requirements.txt
- README.md

Then deploy with main file: `app.py`.

## Usage

Start with source pages, one URL per line. Examples:
- Best international schools in Cape Town pages
- Best private schools in Johannesburg pages
- School directory/category pages
- Official school websites

The app excludes aggregator/listicle pages from final prospects but uses them as discovery sources.
