#!/usr/bin/env bash
# Publishes http://127.0.0.1:8000 at the tunnel's hostnames (see ~/.cloudflared/config.yml).
# Ctrl+C = the site is down.
exec cloudflared tunnel run scrapr
