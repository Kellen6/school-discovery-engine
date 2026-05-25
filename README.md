# School Discovery Engine v4

Free local Streamlit app for discovering schools, using listicles/directories as source pages rather than final prospects.

## What v4 does

1. Searches for school targets by geography and segment.
2. Detects “best/top/list/directory” pages.
3. Visits those source pages and extracts outbound official school website links.
4. Scrapes official school sites.
5. Scores fit based on curriculum, SEN/learning support, counseling, AI/innovation, ELL/ESL.
6. Exports Airtable-ready CSV/Excel.

## Mac setup

```bash
cd ~/Downloads
unzip school_discovery_engine_free_v4.zip
cd school_discovery_engine_free_v4
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open the browser URL shown by Streamlit, usually:

```text
http://localhost:8501
```

## Hosting for Laura

Simplest free/cheap option: Streamlit Community Cloud.

Steps:

1. Create a GitHub account if needed.
2. Create a new GitHub repo, e.g. `school-discovery-engine`.
3. Upload `app.py`, `requirements.txt`, and this README.
4. Go to Streamlit Community Cloud.
5. Connect the GitHub repo.
6. Deploy with main file path: `app.py`.
7. Share the generated Streamlit URL with Laura.

Cost: $0 for public GitHub repo / public app. For private repo/app, pricing depends on Streamlit plan.

Important: this app scrapes public websites. Use moderate search volumes and respect websites' terms and robots rules.
