# School Discovery Engine v21.1

A Streamlit app for finding and enriching school prospects.

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud
Upload `app.py`, `requirements.txt`, and this README to GitHub and deploy with main file `app.py`.

## Notes
- Fast mode is designed for speed and stability.
- Standard/Deep modes add more web search/contact lookups and can be slower.
- Results are cached for map searches unless map inputs change.
