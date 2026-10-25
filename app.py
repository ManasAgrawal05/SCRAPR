import os
import time
from collections import deque
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request

import proxy
import quiz
from modes import DEFAULT_MODE, MODES
from game import MAX_PLAYERS, MIN_PLAYERS, GameError, GameManager

SERPAPI_URL = "https://serpapi.com/search.json"
PAGE_SIZE = 10
CACHE_TTL = 600


def load_env(path=".env"):
    p = Path(__file__).parent / path
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


load_env()
app = Flask(__name__)
_cache = {}
_words = {}  # (query, start) -> word count of each organic result shown
games = GameManager({m["id"]: m["engine"] for m in MODES if m["engine"]})


# Numbers shown in the Rules panel come from the engine's constants, so the text can't drift from the game.
RULES = dict(
    rounds=min(quiz.ROUNDS, *(len(v) for v in quiz.QUESTIONS.values())), min_players=MIN_PLAYERS, max_players=MAX_PLAYERS,
    base=quiz.BASE, per_sec=quiz.TIME_PENALTY_PER_SEC, max_time=quiz.MAX_TIME_PENALTY,
    per_click=quiz.CLICK_PENALTY, max_click=quiz.MAX_CLICK_PENALTY,
    per_wrong=quiz.WRONG_PENALTY, max_wrong=quiz.MAX_WRONG_PENALTY,
    floor=quiz.BASE - quiz.MAX_TIME_PENALTY - quiz.MAX_CLICK_PENALTY - quiz.MAX_WRONG_PENALTY,
    grace=quiz.GRACE, limit_min=quiz.ROUND_LIMIT // 60, intermission=quiz.INTERMISSION,
)


@app.context_processor
def inject_rules():
    return {"rules": RULES}


# ---- abuse protection -------------------------------------------------------
# Search, suggestions and the page proxy spend SerpAPI credits / bandwidth, so they only work for
# a game that is currently being played, and are rate-limited per client.
MAX_GAMES = 500
_hits = {}


def client_ip():
    # behind the Cloudflare Tunnel every request comes from loopback; the real address is in this header
    if request.remote_addr in ("127.0.0.1", "::1"):
        return request.headers.get("CF-Connecting-IP") or request.remote_addr
    return request.remote_addr


def limited(bucket, limit, per=60):
    """Decorator: at most `limit` calls per `per` seconds per client IP, else 429."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            now = time.time()
            if len(_hits) > 10000:
                _hits.clear()
            q = _hits.setdefault((bucket, client_ip()), deque())
            while q and q[0] < now - per:
                q.popleft()
            if len(q) >= limit:
                return jsonify(error="Too many requests. Slow down a little."), 429
            q.append(now)
            return fn(*args, **kwargs)
        return wrapper
    return deco


def playing_game(game_id):
    """The Game with this ID if it is being played right now, else None."""
    try:
        game = games.get(game_id)
    except GameError:
        return None
    with game.lock:
        return game if game.view()["status"] == "playing" else None


class SearchError(Exception):
    pass


def serpapi(params):
    key = os.environ.get("SERPAPI_API_KEY")
    if not key or key == "your_key_here":
        raise SearchError("SERPAPI_API_KEY is not set. Add it to .env and restart.")
    cache_key = tuple(sorted(params.items()))
    hit = _cache.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        r = requests.get(SERPAPI_URL, params={**params, "api_key": key}, timeout=20)
        data = r.json()
    except (requests.RequestException, ValueError):
        raise SearchError("Could not reach SerpAPI. Please try again.")
    if r.status_code != 200 or data.get("error"):
        msg = data.get("error", f"HTTP {r.status_code}")
        if "hasn't returned any results" in msg:
            data = {}
        else:
            raise SearchError(f"SerpAPI error: {msg}")
    if len(_cache) > 500:
        _cache.clear()
    _cache[cache_key] = (time.time(), data)
    return data


def shorten_link(link):
    """Google-style breadcrumb: example.com › path › page"""
    u = urlparse(link)
    parts = [p for p in u.path.split("/") if p]
    return " › ".join([u.netloc.removeprefix("www."), *parts[:3]])


@app.template_filter("num")
def num(n):
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return n


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/game/<game_id>")
def game_page(game_id):
    try:
        game = games.get(game_id)
    except GameError:
        return redirect("/")
    return render_template("game.html", game_id=game.id, modes=MODES)


# ---- game API -------------------------------------------------------------
# Players authenticate with the token returned by host/join (X-Player-Token header).

@app.errorhandler(GameError)
def game_error(e):
    return jsonify(error=str(e)), e.status


def _body():
    return request.get_json(silent=True) or {}


@app.post("/api/games")
@limited("host", 60)
def host_game():
    if len(games.games) >= MAX_GAMES:
        return jsonify(error="The server is full right now. Try again in a little while."), 503
    body = _body()
    game = games.create(DEFAULT_MODE)  # the host picks another mode from the lobby tabs
    with game.lock:
        player = game.add_player(str(body.get("name", "")).strip()[:24], body.get("avatar"))
    return jsonify(game=game.view(), player=player), 201


@app.post("/api/games/<game_id>/join")
def join_game(game_id):
    game = games.get(game_id)
    with game.lock:
        body = _body()
        player = game.add_player(str(body.get("name", "")).strip()[:24], body.get("avatar"))
        return jsonify(game=game.view(), player=player)


@app.get("/api/games/<game_id>")
def game_state(game_id):
    game = games.get(game_id)
    with game.lock:
        return jsonify(game.view())


@app.post("/api/games/<game_id>/profile")
def set_profile(game_id):
    game = games.get(game_id)
    body = _body()
    with game.lock:
        game.set_profile(request.headers.get("X-Player-Token"), body.get("name"), body.get("avatar"))
        return jsonify(game.view())


@app.get("/api/games/<game_id>/avatar/<player_id>")
def avatar(game_id, player_id):
    game = games.get(game_id)
    with game.lock:
        data = game.avatar_bytes(player_id)
    if not data:
        return "", 404
    return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/games/<game_id>/mode")
def set_mode(game_id):
    game = games.get(game_id)
    with game.lock:
        game.set_mode(request.headers.get("X-Player-Token"), _body().get("mode"))
        return jsonify(game.view())


@app.post("/api/games/<game_id>/settings")
def set_settings(game_id):
    game = games.get(game_id)
    with game.lock:
        game.set_settings(request.headers.get("X-Player-Token"), _body())
        return jsonify(game.view())


@app.post("/api/games/<game_id>/start")
def start_game(game_id):
    game = games.get(game_id)
    with game.lock:
        game.start(request.headers.get("X-Player-Token"))
        return jsonify(game.view())


@app.post("/api/games/<game_id>/event")
def game_event(game_id):
    game = games.get(game_id)
    with game.lock:
        body = _body()
        body.pop("words", None)  # never trust a client-supplied count
        if body.get("type") == "search":
            try:
                start = (int(body.get("page", 1)) - 1) * PAGE_SIZE
            except (TypeError, ValueError):
                start = 0
            body["words"] = _words.get((str(body.get("q", "")).strip(), start))
        game.record(request.headers.get("X-Player-Token"), body)
        return jsonify(game.view())


@app.post("/api/games/<game_id>/action")
def game_action(game_id):
    game = games.get(game_id)
    with game.lock:
        result = game.act(request.headers.get("X-Player-Token"), _body().get("action"))
        return jsonify({**game.view(), "result": result})


@app.route("/view")
def view_page():
    """In-app browser: SCRAPR game bar on top, the target page (via /proxy) in a sandboxed frame."""
    url = request.args.get("url", "")
    if not url.startswith(("http://", "https://")):
        return redirect("/")
    game = playing_game(request.args.get("game", ""))
    if not game:
        return redirect("/")
    return render_template("view.html", url=url, game=game.id, q=request.args.get("q", ""))


@app.route("/proxy")
@limited("proxy", 600)
def proxy_page():
    if not playing_game(request.args.get("game", "")):
        return render_template("proxy_error.html", message="Pages can only be opened from inside a game.", url=""), 403
    url = request.args.get("url", "")
    headers = {
        # opaque origin: the framed page can never touch SCRAPR's storage (player tokens), even if opened directly
        "Content-Security-Policy": "sandbox allow-scripts",
        "X-Content-Type-Options": "nosniff",
    }
    try:
        final, ctype, body, enc = proxy.fetch(url)
    except proxy.ProxyError as e:
        return render_template("proxy_error.html", message=str(e), url=url), 502, headers
    if "html" in ctype.lower():
        return proxy.prepare_html(final, body, enc), 200, {**headers, "Content-Type": "text/html; charset=utf-8"}
    return body, 200, {**headers, "Content-Type": ctype}


@app.route("/search")
@limited("search", 300)
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return redirect("/")
    try:
        start = max(0, int(request.args.get("start", 0)))
    except ValueError:
        start = 0
    game_id = request.args.get("game", "").strip().upper()
    try:
        g = games.get(game_id)
    except GameError:
        return redirect("/")
    with g.lock:
        view = g.view()
        blocked = g.blocked_words(q)
    if view["status"] != "playing":  # search only exists inside a running game
        return redirect("/game/" + g.id)
    question = view["state"]["question"]
    data, error = {}, None
    if not blocked:  # a blocked search never reaches SerpAPI, so it costs no credit
        try:
            data = serpapi({"engine": "google", "q": q, "start": start, "num": PAGE_SIZE})
        except SearchError as e:
            error = str(e)
    related = data.get("related_searches", [])
    if g:  # don't offer follow-up searches that break the rule
        with g.lock:
            related = [s for s in related if not g.blocked_words(s.get("query", ""))]

    results = data.get("organic_results", [])
    for r in results:
        r["crumb"] = shorten_link(r.get("link", ""))
    if results:
        if len(_words) > 1000:
            _words.clear()
        _words[(q, start)] = [len(f"{r.get('title', '')} {r.get('snippet', '')}".split()) for r in results]
    info = data.get("search_information", {})
    return render_template(
        "results.html",
        q=q,
        game=game_id or None,
        gq=f"&game={game_id}" if game_id else "",
        question=question,
        start=start,
        page=start // PAGE_SIZE + 1,
        results=results,
        total=info.get("total_results"),
        took=info.get("time_taken_displayed"),
        answer=data.get("answer_box"),
        kg=data.get("knowledge_graph"),
        paa=data.get("related_questions", []),
        related=related,
        blocked=blocked,
        has_next="next" in data.get("serpapi_pagination", {}),
        error=error,
    )


@app.route("/suggest")
@limited("suggest", 900)
def suggest():
    q = request.args.get("q", "").strip()
    if not q or os.environ.get("ENABLE_SUGGEST", "1") != "1":
        return jsonify([])
    game = playing_game(request.args.get("game", ""))
    if not game:
        return jsonify([])
    with game.lock:
        if game.blocked_words(q):
            return jsonify([])  # typed text already breaks the rule: skip the API call

    def allowed(text):
        with game.lock:
            return not game.blocked_words(text)
    try:
        data = serpapi({"engine": "google_autocomplete", "q": q})
    except SearchError:
        return jsonify([])
    return jsonify([s["value"] for s in data.get("suggestions", []) if allowed(s["value"])][:8])


if __name__ == "__main__":
    # 0.0.0.0 so other machines (e.g. over Tailscale) can connect. Debug mode exposes a
    # remote-code-execution console, so it is opt-in: FLASK_DEBUG=1 python app.py
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 5000)),
            debug=os.environ.get("FLASK_DEBUG") == "1")
