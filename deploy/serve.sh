#!/usr/bin/env bash
# Production-style server: ONE worker (games live in this process's memory), 8 threads, loopback only.
# Needs the conda env active:  conda activate scrapr
cd "$(dirname "$0")/.." || exit 1
exec gunicorn -w 1 --threads 8 -b 127.0.0.1:8000 --access-logfile - app:app
