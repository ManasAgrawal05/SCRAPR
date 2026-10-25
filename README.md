# SCRAPR – Google Search clone (SerpAPI)

Visual clone of Google web search. Flask backend proxies queries to [SerpAPI](https://serpapi.com); the API key never reaches the browser.

```bash
conda env create -f environment.yml     # creates env "scrapr"
conda activate scrapr
cp .env.example .env                    # then put your SERPAPI_API_KEY in it
python app.py                           # http://localhost:5000
```

Routes: `/` home, `/search?q=&start=`, `/lucky?q=`, `/suggest?q=` (autocomplete; each call uses a SerpAPI search — set `ENABLE_SUGGEST=0` to disable). Responses are cached in memory for 10 minutes.
