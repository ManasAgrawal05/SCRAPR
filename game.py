"""In-memory lobby + game manager. The actual game rules live in an Engine subclass."""
import base64
import binascii
import secrets
import threading
import time

ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
ID_LENGTH = 5
MAX_PLAYERS = 8  # artificial cap
MIN_PLAYERS = 2
AVATAR_PREFIX = "data:image/jpeg;base64,"
AVATAR_MAX = 40000  # characters; the browser downsizes to ~96px
STALE_AFTER = 6 * 3600
EVENT_STAT = {"search": "searches", "click": "clicks"}
MAX_EVENTS = 5000
PLACEHOLDER_QUESTION = "Placeholder question: what is the answer to this very obscure question?"


class GameError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def clean_avatar(value):
    """Validate a downsized JPEG data URL; returns it, or None for 'no image'."""
    if not value:
        return None
    if not isinstance(value, str) or len(value) > AVATAR_MAX or not value.startswith(AVATAR_PREFIX):
        raise GameError("Invalid image")
    try:
        base64.b64decode(value[len(AVATAR_PREFIX):], validate=True)
    except (binascii.Error, ValueError):
        raise GameError("Invalid image")
    return value


class Engine:
    """Override these to implement the real game. Called with the Game lock held."""

    def start(self, players, settings=None):
        self.players = [p["id"] for p in players]
        self.question = PLACEHOLDER_QUESTION
        self.log = []

    finished = False

    def blocked_words(self, query):
        """Words of `query` that may not be searched right now."""
        return []

    def tick(self):
        """Advance any timers. Called before every read or write, lock held."""

    def on_event(self, player_id, event):
        """Called for each tracking event (search / click)."""

    def apply(self, player_id, action):
        """Validate + apply one player action; raise GameError if illegal."""
        self.log.append({"player": player_id, "action": action})

    def state(self):
        """JSON-serialisable public game state."""
        return {"question": self.question, "moves": len(self.log), "last": self.log[-1] if self.log else None}


class Game:
    def __init__(self, game_id, engine_cls):
        self.id = game_id
        self.status = "waiting"  # waiting -> ready (2 players) -> playing (host started)
        self.players = []
        self.started_at = None
        self.events = []  # tracking log: searches and result clicks, t = seconds since start
        self.stats = {}
        self.settings = {"difficulty": "difficult", "block_words": True}  # host edits these in the lobby
        self.engine = engine_cls()
        self.version = 0  # bumps on every change, so clients can poll cheaply
        self.touched = time.time()
        self.lock = threading.Lock()

    def add_player(self, name, avatar=None):
        if self.status in ("playing", "finished"):
            raise GameError("Game already started", 409)
        if len(self.players) >= MAX_PLAYERS:
            raise GameError("Lobby is full", 409)
        player = {"id": f"p{len(self.players) + 1}", "name": name or f"Player {len(self.players) + 1}",
                  "avatar": clean_avatar(avatar), "avatar_v": 1, "token": secrets.token_urlsafe(16)}
        self.players.append(player)
        self.stats[player["id"]] = {"searches": 0, "clicks": 0, "words": 0}
        if len(self.players) >= MIN_PLAYERS:
            self.status = "ready"
        self._bump()
        return player

    def set_profile(self, token, name, avatar):
        """Update the caller's display name / image. avatar: omitted=keep, ''=remove."""
        player = self.player_for(token)
        if isinstance(name, str) and name.strip():
            player["name"] = name.strip()[:24]
        if avatar is not None:
            player["avatar"] = clean_avatar(avatar)
            player["avatar_v"] += 1
        self._bump()

    def avatar_bytes(self, player_id):
        for p in self.players:
            if p["id"] == player_id and p["avatar"]:
                return base64.b64decode(p["avatar"][len(AVATAR_PREFIX):])
        return None

    def start(self, token):
        player = self.player_for(token)
        if player is not self.players[0]:
            raise GameError("Only the host can start the game", 403)
        if self.status != "ready":
            raise GameError(f"Need at least {MIN_PLAYERS} players to start" if self.status == "waiting" else "Game already started", 409)
        self.status = "playing"
        self.started_at = time.time()
        self.engine.start(self.players, dict(self.settings))
        self._bump()

    def set_settings(self, token, data):
        player = self.player_for(token)
        if player is not self.players[0]:
            raise GameError("Only the host can change the settings", 403)
        if self.status not in ("waiting", "ready"):
            raise GameError("Settings are locked once the game starts", 409)
        if data.get("difficulty") in ("easy", "difficult"):
            self.settings["difficulty"] = data["difficulty"]
        if isinstance(data.get("block_words"), bool):
            self.settings["block_words"] = data["block_words"]
        self._bump()

    def player_for(self, token):
        for p in self.players:
            if token and secrets.compare_digest(p["token"], token):
                return p
        raise GameError("Invalid player token", 401)

    def record(self, token, event):
        """Log a tracking event (search / result click) for the calling player."""
        player = self.player_for(token)
        self._sync()
        if self.status != "playing":
            raise GameError("Game is not in progress", 409)
        kind = event.get("type")
        if kind not in EVENT_STAT:
            raise GameError("Unknown event type")
        entry = {"player": player["id"], "type": kind, "t": round(time.time() - self.started_at, 2)}
        for field in ("q", "url", "page"):
            if field in event:
                entry[field] = str(event[field])[:500]
        words = event.get("words")  # word count of each result shown (set by the server, see app.py)
        if kind == "search" and isinstance(words, list):
            entry["words"] = [w for w in words if isinstance(w, int)][:50]
            self.stats[player["id"]]["words"] += sum(entry["words"])
        if len(self.events) < MAX_EVENTS:
            self.events.append(entry)
        self.stats[player["id"]][EVENT_STAT[kind]] += 1
        self.engine.on_event(player["id"], entry)
        self._bump()

    def act(self, token, action):
        player = self.player_for(token)
        self._sync()
        if self.status != "playing":
            raise GameError("Game is not in progress", 409)
        result = self.engine.apply(player["id"], action)
        self._sync()
        self._bump()
        return result

    def blocked_words(self, query):
        self._sync()
        return self.engine.blocked_words(query) if self.status == "playing" else []

    def _sync(self):
        """Let the engine move its timers on, and pick up game over."""
        if self.status == "playing":
            self.engine.tick()
            if self.engine.finished:
                self.status = "finished"
                self._bump()

    def view(self):
        self._sync()
        return {
            "id": self.id,
            "status": self.status,
            "version": self.version,
            "players": [{"id": p["id"], "name": p["name"],
                         "avatar": f"/api/games/{self.id}/avatar/{p['id']}?v={p['avatar_v']}" if p["avatar"] else None}
                        for p in self.players],
            "state": self.engine.state() if self.status in ("playing", "finished") else None,
            "settings": self.settings,
            "started_at": self.started_at,
            "now": time.time(),
            "stats": self.stats,
        }

    def _bump(self):
        self.version += 1
        self.touched = time.time()


class GameManager:
    def __init__(self, engine_cls=Engine):
        self.engine_cls = engine_cls
        self.games = {}
        self.lock = threading.Lock()

    def create(self):
        with self.lock:
            self._prune()
            while True:
                gid = "".join(secrets.choice(ID_ALPHABET) for _ in range(ID_LENGTH))
                if gid not in self.games:
                    break
            self.games[gid] = Game(gid, self.engine_cls)
            return self.games[gid]

    def get(self, game_id):
        game = self.games.get((game_id or "").strip().upper())
        if not game:
            raise GameError("Game not found", 404)
        return game

    def _prune(self):
        cutoff = time.time() - STALE_AFTER
        for gid in [g for g, v in self.games.items() if v.touched < cutoff]:
            del self.games[gid]
