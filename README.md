# SCRAPR – Google Search clone (SerpAPI)

Visual clone of Google web search. Flask backend proxies queries to [SerpAPI](https://serpapi.com); the API key never reaches the browser.

```bash
conda env create -f environment.yml     # creates env "scrapr"
conda activate scrapr
cp .env.example .env                    # then put your SERPAPI_API_KEY in it
python app.py                           # http://localhost:5000
```

Routes: `/` home, `/search?q=&start=`, `/lucky?q=`, `/suggest?q=` (autocomplete; each call uses a SerpAPI search — set `ENABLE_SUGGEST=0` to disable). Responses are cached in memory for 10 minutes.

## Going live (Cloudflare Tunnel)

The site is only up while your machine runs these two commands; stop them and it's down.

```bash
conda activate scrapr
./deploy/serve.sh     # terminal 1: gunicorn on 127.0.0.1:8000 (one worker on purpose)
./deploy/tunnel.sh    # terminal 2: publishes it at your domain
```

One-time setup: `cloudflared tunnel login`, `cloudflared tunnel create scrapr`,
`cloudflared tunnel route dns --overwrite-dns scrapr <your-domain>`, then copy
`deploy/cloudflared.example.yml` to `~/.cloudflared/config.yml` and fill in the tunnel UUID.
Games live in memory, so restarting the server ends any game in progress.
